# DLG Thermal & Slope Finder

A single-file browser app that scans terrain + wind to find where to fly RC DLG gliders.
Open `thermal-finder.html` (or `index.html`, identical) in any modern browser — no install,
no accounts, no API keys. Everything runs client-side off public data sources.

## What it does (current — v4)

**Terrain & scoring**
- Auto-loads elevation as you pan (AWS Terrarium tiles), analyzes a region larger than the
  screen and reuses it so panning doesn't re-compute (fast). Satellite / Topo / Streets base maps.
- Scores ground for **thermal triggers** (sun-heated, convex release points — sun-gated so it
  doesn't score at night) and **slope-soaring faces** (steepness + relief into the wind).
- **Draw-area** polygon to focus analysis; click any point for a local readout (works under
  no-fly overlays too — airspace is reported in the same popup).
- Bring-your-own **1 m DEM GeoTIFF** (UTM/WGS84 auto-reprojected) for high-res analysis; an
  uploaded DEM is protected from being overwritten by auto-load.

**Wind model (diagnostic, not CFD)**
- Mass-conserving terrain flow → ridge speed-up, orographic **Lift**, lee-**Rotor** hazard
  (with a calm-air floor), convergence. Lift/Rotor are selectable overlay modes.
- **Live wind** from Open-Meteo (cached in localStorage ~6 h, ~4 calls/day/area) with a
  forecast-hour slider that also moves the sun. Manual wind dial as override.
- **Thermal forecast** (live): CAPE → thermal strength, estimated cloudbase, low-cloud %.
- **DLG flight estimate** (for fun): recommended ballast, penetration speed vs gust, modeled
  rising air, float-vs-penetrate verdict. Roughly-right, clearly labeled — verify at the field.

**Finding spots**
- **Top spots (this view)** — best spots in the current map view, ranked relatively, count
  scales with zoom; each card shows a "wind ON/OFF face" chip, hover to highlight its pin.
- **Epic spots (wide search)** — enter a ZIP + mile radius → best 10 standout spots in the
  region with distance and a "why" (steepness/prominence/sun), star markers, hover to locate.
- **Airspace** flags (Camp Pendleton, KOKB, KCRQ) — approximate, planning-only.

**Logbook & buildings**
- **Flight log** — click the map where you flew, record date/aircraft/max alt/max time/notes;
  saved in localStorage, shown as pins, export/import JSON.
- **Buildings (beta)** — at zoom 15+, pull OSM footprints + heights and burn them onto the
  terrain so wind deflects around them and roofs/walls read as sun-warmed triggers. Optional;
  depends on the free public Overpass service (can be slow/unavailable).

## Hosting

It's one static file using only public, keyless, CORS-friendly APIs, so it hosts anywhere.
A Netlify site **dlg-soaring-finder** is already created in the connected account.
- Drag `index.html` onto **app.netlify.com/projects/dlg-soaring-finder → Deploys** to publish
  to `https://dlg-soaring-finder.netlify.app`, or onto **app.netlify.com/drop** for a new URL.
- (The connector can't push from this sandbox — Netlify's upload endpoint is firewalled here —
  so the final drag-drop is a 20-second manual step.)

## Versions (for revert)
`thermal-finder-v2.html` (epic-why, sun-sync, satellite layers) · `-v3` (flight log) ·
`-v3.5` (DLG sim) · `-v3.7-buildings` · `-v4` (review fixes + thermal forecast + wind chips).
`index.html` mirrors the latest (v4).

## Caveats
- The wind model is diagnostic — directionally useful, not turbulence-accurate. Read Lift and
  Rotor together (strong crest speed-up usually means a dangerous lee).
- OSM building heights are patchy; the flight estimate is a novelty.
- **Always verify airspace/conditions** (FAA B4UFLY / LAANC / NOTAMs), stay ≤400 ft AGL and in
  line of sight before flying.

---

## Precomputed terrain-tile pipeline (`build_terrain_tiles.py`)
Optional: precompute slope/aspect/TPI tiles for a region and host them, so the app reads them
instead of analyzing on the fly. See the script header for the download→build→host→configure
workflow. Run `python build_terrain_tiles.py --selftest` to validate the math.
