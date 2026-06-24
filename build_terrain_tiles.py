#!/usr/bin/env python3
"""
build_terrain_tiles.py  —  Precompute terrain-derivative XYZ tiles for the
DLG Thermal & Slope Finder.

What it does
------------
Takes a DEM (any GDAL/rasterio-readable raster: GeoTIFF / COG / VRT), and
produces a standard {z}/{x}/{y}.png XYZ tile pyramid where each pixel encodes
the *wind/sun-independent* terrain derivatives:

    R = slope     (degrees, 0..64 clamped, scaled to 0..255)
    G = aspect    (compass degrees the slope faces, 0..360 -> 0..255)
    B = TPI       (topographic position index, meters, tanh-compressed
                   around 0; 128 = flat, >128 = ridge/spur, <128 = gully)

The browser reads these tiles, decodes R/G/B, and applies the *live* part
(wind direction + sun angle) to score thermals & slope-soaring — so panning
and zooming is instant and nothing is recomputed on the fly.

Why precompute derivatives instead of raw elevation?
  * No tile-edge seams (TPI/slope need neighbors; we compute on the full
    mosaic before cutting tiles).
  * Much smaller + faster on the client than re-deriving from elevation.

Typical SoCal workflow
----------------------
  # 1. Get 3DEP DEM (example: 10 m seamless for the region, or 1 m for an area)
  #    Easiest source: USGS TNM downloader or the public AWS bucket s3://prd-tnm/
  #    Build a single VRT mosaic from the downloaded tiles:
  gdalbuildvrt socal.vrt /path/to/3dep/*.tif

  # 2. Build derivative tiles (zooms 9-14 from 10 m data is a good regional base)
  python build_terrain_tiles.py socal.vrt ./tiles --minzoom 9 --maxzoom 14

  # 3. Add 1 m detail only for your flying areas (higher zooms), into the SAME dir
  python build_terrain_tiles.py area_1m.tif ./tiles --minzoom 15 --maxzoom 17

  # 4. Host ./tiles on any static host (Cloudflare R2 / S3 / GitHub Pages) and
  #    point the app's TILE_URL at  https://your-host/tiles/{z}/{x}/{y}.png

Resolution guidance
-------------------
  * 1 m for ALL SoCal is tens of TB — don't. Use ~10 m as the regional base
    (sub-GB) and stage 1 m only for the ridges/bluffs you actually fly.
  * The encoding is identical at any resolution, so mixed-resolution pyramids
    just work.

Run the self-test (no GDAL data needed):
  python build_terrain_tiles.py --selftest
"""

import argparse, math, os, sys
import numpy as np
from PIL import Image

# ----------------------------- encoding constants -----------------------------
SLOPE_MAX = 64.0      # degrees; slopes steeper than this clamp to 255
TPI_SCALE = 20.0      # meters; tanh compression scale (so ~±40 m spans the range)

def encode_derivatives(slope_deg, aspect_deg, tpi_m):
    """slope/aspect/tpi float arrays -> uint8 RGB (H,W,3). NaN-safe."""
    s = np.clip(np.nan_to_num(slope_deg, nan=0.0), 0, SLOPE_MAX) * (255.0 / SLOPE_MAX)
    a = np.mod(np.nan_to_num(aspect_deg, nan=0.0), 360.0) * (255.0 / 360.0)
    t = (np.tanh(np.nan_to_num(tpi_m, nan=0.0) / TPI_SCALE) * 127.0) + 128.0
    rgb = np.stack([s, a, t], axis=-1)
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)

def decode_derivatives(rgb):
    """Inverse of encode_derivatives — used by the self-test (mirrors the JS)."""
    rgb = rgb.astype(np.float64)
    slope = rgb[..., 0] * (SLOPE_MAX / 255.0)
    aspect = rgb[..., 1] * (360.0 / 255.0)
    tpi = np.arctanh(np.clip((rgb[..., 2] - 128.0) / 127.0, -0.999, 0.999)) * TPI_SCALE
    return slope, aspect, tpi

# ----------------------------- terrain math -----------------------------------
def slope_aspect(z, dx, dy):
    """Horn 3x3 slope (deg) & aspect (compass deg). Arrays same shape as z.
    Row 0 = north (top). dy is positive ground spacing between rows."""
    # gradients via np.gradient with edge handling (matches Horn closely enough,
    # and avoids per-pixel python loops); use Sobel-like via convolution for Horn.
    g = z.astype(np.float64)
    # pad edges by replication
    gp = np.pad(g, 1, mode='edge')
    # Horn kernels
    dzdx = ((gp[:-2,2:] + 2*gp[1:-1,2:] + gp[2:,2:]) -
            (gp[:-2,:-2] + 2*gp[1:-1,:-2] + gp[2:,:-2])) / (8.0*dx)
    # rows increase downward (south); invert so +dzdy means uphill to the north
    dzdy = ((gp[2:,:-2] + 2*gp[2:,1:-1] + gp[2:,2:]) -
            (gp[:-2,:-2] + 2*gp[:-2,1:-1] + gp[:-2,2:])) / (8.0*dy)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = np.degrees(np.arctan2(dzdy, -dzdx))
    aspect = np.mod(90.0 - aspect, 360.0)
    return slope, aspect

def tpi(z, radius_px):
    """Topographic Position Index: z minus mean of a (2r+1) box, via integral image."""
    g = z.astype(np.float64)
    H, W = g.shape
    I = np.zeros((H+1, W+1), dtype=np.float64)
    I[1:,1:] = np.cumsum(np.cumsum(g, axis=0), axis=1)
    r = radius_px
    out = np.empty_like(g)
    for y in range(H):
        y0, y1 = max(0, y-r), min(H-1, y+r)
        for x in range(W):
            x0, x1 = max(0, x-r), min(W-1, x+r)
            total = I[y1+1, x1+1] - I[y0, x1+1] - I[y1+1, x0] + I[y0, x0]
            n = (y1-y0+1) * (x1-x0+1)
            out[y, x] = g[y, x] - total/n
    return out

def tpi_fast(z, radius_px):
    """Vectorized TPI via integral image (no python double loop)."""
    g = z.astype(np.float64)
    H, W = g.shape
    I = np.zeros((H+1, W+1), dtype=np.float64)
    I[1:,1:] = np.cumsum(np.cumsum(g, axis=0), axis=1)
    r = radius_px
    ys = np.arange(H)
    xs = np.arange(W)
    y0 = np.clip(ys - r, 0, H-1); y1 = np.clip(ys + r, 0, H-1)
    x0 = np.clip(xs - r, 0, W-1); x1 = np.clip(xs + r, 0, W-1)
    # build via broadcasting
    Y0 = y0[:, None]; Y1 = y1[:, None]; X0 = x0[None, :]; X1 = x1[None, :]
    total = I[Y1+1, X1+1] - I[Y0, X1+1] - I[Y1+1, X0] + I[Y0, X0]
    n = (Y1 - Y0 + 1) * (X1 - X0 + 1)
    return g - total / n

# ----------------------------- tiling -----------------------------------------
def deg2num(lat, lon, z):
    lat_r = math.radians(lat)
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y

def num2deg(x, y, z):
    n = 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon

def build_tiles(dem_path, out_dir, minzoom, maxzoom, tpi_radius_m=80.0):
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    from rasterio.windows import from_bounds

    MERC = "EPSG:3857"
    with rasterio.open(dem_path) as src:
        # reproject whole DEM to web mercator at native-ish resolution
        transform, width, height = calculate_default_transform(
            src.crs, MERC, src.width, src.height, *src.bounds)
        dst = np.empty((height, width), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1), destination=dst,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=transform, dst_crs=MERC,
            resampling=Resampling.bilinear, dst_nodata=np.nan)
        # ground resolution (mercator meters/px corrected by latitude at center)
        b = rasterio.transform.array_bounds(height, width, transform)  # (l,b,r,t)
        left, bottom, right, top = b
        # convert mercator bounds to lat/lon for tiling + lat correction
        import rasterio.warp as rw
        (lon0, lon1), (lat0, lat1) = rw.transform(MERC, "EPSG:4326",
                                                   [left, right], [bottom, top])
        latc = (lat0 + lat1) / 2.0
        cosl = math.cos(math.radians(latc))
        px = (right - left) / width  * cosl
        py = (top - bottom) / height * cosl
        r_px = max(2, int(round(tpi_radius_m / max(px, py))))

        # fill nodata for derivative stability
        z = dst.copy()
        if np.isnan(z).any():
            m = np.nanmean(z)
            z = np.where(np.isnan(z), m, z)

        slope, aspect = slope_aspect(z, px, py)
        tp = tpi_fast(z, r_px)
        rgb = encode_derivatives(slope, aspect, tp)  # (H,W,3) uint8

        # helper: world(merc) -> pixel
        def merc_to_px(mx, my):
            col = (mx - left) / (right - left) * width
            row = (top - my) / (top - bottom) * height
            return col, row

        import rasterio.warp as rw2
        n_written = 0
        for zoom in range(minzoom, maxzoom + 1):
            x0f, y0f = deg2num(lat1, lon0, zoom)  # top-left
            x1f, y1f = deg2num(lat0, lon1, zoom)  # bottom-right
            xt0, xt1 = int(math.floor(min(x0f, x1f))), int(math.floor(max(x0f, x1f)))
            yt0, yt1 = int(math.floor(min(y0f, y1f))), int(math.floor(max(y0f, y1f)))
            for xt in range(xt0, xt1 + 1):
                for yt in range(yt0, yt1 + 1):
                    # tile lat/lon bounds -> mercator -> source pixels
                    latN, lonW = num2deg(xt, yt, zoom)
                    latS, lonE = num2deg(xt + 1, yt + 1, zoom)
                    (mxW, mxE), (myS, myN) = rw2.transform(
                        "EPSG:4326", MERC, [lonW, lonE], [latS, latN])
                    cW, rN = merc_to_px(mxW, myN)
                    cE, rS = merc_to_px(mxE, myS)
                    c0, c1 = int(math.floor(cW)), int(math.ceil(cE))
                    r0, r1 = int(math.floor(rN)), int(math.ceil(rS))
                    if c1 <= 0 or r1 <= 0 or c0 >= width or r0 >= height:
                        continue
                    c0c, r0c = max(0, c0), max(0, r0)
                    c1c, r1c = min(width, c1), min(height, r1)
                    if c1c <= c0c or r1c <= r0c:
                        continue
                    sub = rgb[r0c:r1c, c0c:c1c]
                    img = Image.fromarray(sub, "RGB").resize((256, 256), Image.NEAREST)
                    d = os.path.join(out_dir, str(zoom), str(xt))
                    os.makedirs(d, exist_ok=True)
                    img.save(os.path.join(d, f"{yt}.png"))
                    n_written += 1
            print(f"  zoom {zoom}: tiles x[{xt0}..{xt1}] y[{yt0}..{yt1}]")
        print(f"Done. {n_written} tiles -> {out_dir}")
        # write a tiny metadata file
        with open(os.path.join(out_dir, "tiles.json"), "w") as f:
            f.write('{"scheme":"xyz","encoding":"slopeR_aspectG_tpiB",'
                    f'"slope_max":{SLOPE_MAX},"tpi_scale":{TPI_SCALE},'
                    f'"minzoom":{minzoom},"maxzoom":{maxzoom}}}')

# ----------------------------- self test --------------------------------------
def selftest():
    print("Self-test: synthetic terrain -> derivatives -> encode -> decode")
    H = W = 120
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    # a gaussian hill (ridge/peak) + a south-tilted plane
    hill = 60*np.exp(-(((xx-60)**2+(yy-60)**2)/(2*22**2)))
    plane = (H-yy)*0.5            # rises to the north -> faces south
    z = hill + plane
    dx = dy = 10.0               # 10 m pixels

    slope, aspect = slope_aspect(z, dx, dy)
    tp = tpi_fast(z, 8)
    rgb = encode_derivatives(slope, aspect, tp)
    ds, da, dt = decode_derivatives(rgb)

    # 1. encode/decode roundtrip error small
    se = np.abs(ds - slope).mean()
    ae = np.abs((da - aspect + 180) % 360 - 180).mean()
    te = np.abs(dt - tp).mean()
    print(f"  roundtrip mean err: slope {se:.2f}deg  aspect {ae:.2f}deg  tpi {te:.2f}m")

    # 2. hill top is a TPI high (ridge), plane-only corner is ~flat TPI
    top = tp[60,60]; corner = tp[5,110]
    print(f"  TPI at hilltop {top:.1f} (expect >0), flat plane {corner:.1f} (expect ~0)")

    # 3. south slope of the plane region (far corner from hill) faces ~180
    asp_s = aspect[10,10]
    print(f"  plane aspect {asp_s:.0f}deg (expect ~180 = south-facing)")

    # 4. fast vs loop TPI agree
    tp_slow = tpi(z, 8)
    print(f"  tpi_fast vs tpi_loop max diff {np.abs(tp-tp_slow).max():.4f} (expect ~0)")

    ok = (se < 0.6 and ae < 2.0 and te < 1.5 and top > 1 and abs(corner) < 3
          and abs(((asp_s-180+180)%360)-180) < 20 and np.abs(tp-tp_slow).max() < 1e-6)
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

# ----------------------------- cli --------------------------------------------
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    ap = argparse.ArgumentParser(description="Build terrain-derivative XYZ tiles from a DEM.")
    ap.add_argument("dem", help="input DEM (GeoTIFF/COG/VRT)")
    ap.add_argument("out", help="output tile directory")
    ap.add_argument("--minzoom", type=int, default=10)
    ap.add_argument("--maxzoom", type=int, default=15)
    ap.add_argument("--tpi-radius", type=float, default=80.0, help="TPI neighborhood radius, meters")
    a = ap.parse_args()
    build_tiles(a.dem, a.out, a.minzoom, a.maxzoom, a.tpi_radius)
