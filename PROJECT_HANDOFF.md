# DLG Soaring Finder — Project Handoff & Context

**The institutional memory for this project.** Code lives on GitHub; this file captures the *why* —
the architecture, the model, the decisions, the workflow, and what's left — so any new computer (or
new Cowork/Claude conversation) can pick up without re-deriving everything. If you start a fresh
chat, point it at the repo and this file first.

- **Live site:** https://dlg-soaring-finder.netlify.app
- **Repo:** https://github.com/Anthony7820/dlg-soaring-finder  (branches: `main` = production, `dev` = work-in-progress)
- **What it is:** a single-file browser tool that scans terrain + wind + sun to map where (and when) to
  fly RC gliders — separating **thermal** soaring from **slope/ridge** soaring. Built toward "any RC
  soaring pilot," DLG/hand-launch first. No accounts or API keys needed to use it.

---

## 1. Quick start on a new computer

1. Get the code: GitHub → **Code → Download ZIP**, or `git clone https://github.com/Anthony7820/dlg-soaring-finder.git`
2. The whole app is **`index.html`** — just open it in a browser. Everything runs client-side off public data.
3. To edit/deploy, see §4. The services (Netlify, Supabase) are cloud-side — just sign in; nothing to migrate.

## 2. Accounts & services (cloud — not tied to any computer)

- **GitHub** — source of truth. Pushing needs a fine-grained PAT with **Contents: write** (generate fresh on the new machine; not machine-bound).
- **Netlify** (`dlg-soaring-finder`, team `anthony7820`, Free plan) — auto-deploys from `main`. **No build command** (see §4). Just sign in in the browser.
- **Supabase** (`xascgdvviwghsimdvhpz.supabase.co`) — Postgres for the community flight log + leaderboard.
  - The **anon public key** is in `index.html` and is safe to ship (Row-Level-Security restricts it).
  - The **service_role key** is secret — **never commit it**; re-paste from the Supabase dashboard only into the local-only `admin.html` when moderating.
  - Schema is in `SUPABASE_SCHEMA.sql` (run it in the Supabase SQL editor to recreate the table + RLS).

## 3. Free-tier constraints (important)

- **Netlify**: billed **~15 credits per production deploy** (flat, even no-build). The account ran near its cap, hence the workflow in §4. Resets on the monthly billing cycle.
- **Supabase**: 500 MB DB / 5 GB egress / pauses after 7 days inactivity. Community reads are cached ~5 min in localStorage; the query is trimmed; a CHECK constraint bounds values.

## 4. Deploy workflow (conserves Netlify credits)

**Work on `dev` (free), publish to `main` (one paid deploy).**

- All iteration happens on `dev`. Validate **headlessly** (§6) — no deploy needed to test.
- To preview locally without deploying: open the single HTML file in a browser (no hosting required).
- **Publish** = fast-forward `main` to `dev`, then **bake the commit hash** into `index.html` (replace
  `__COMMIT__`) and push `main`. Netlify deploys it (no-build, ~7s). Example:
  ```
  git checkout main && git merge dev --ff-only
  SHA=$(git rev-parse --short HEAD); sed -i "s/__COMMIT__/$SHA/g" index.html
  git commit -am "vX.Y release — stamp $SHA" && git push origin main
  ```
- **Why no build command:** `netlify.toml` has *no* `command` on purpose. A build command spins a build
  container and burns credits on every push; we stamp the version hash ourselves instead. Don't re-add one.
- `.nojekyll` is present so the repo also works on GitHub Pages (a zero-credit fallback host if ever needed:
  repo Settings → Pages → deploy from `main` → root).

## 5. Architecture & files

- **`index.html`** — the entire app (Leaflet map + all logic + styles, ~165 KB). This is `thermal-finder.html` in the working folder; they're identical.
- **`netlify.toml`** — `publish="."`, no build command.
- **`.nojekyll`** — GitHub Pages compatibility.
- **`SUPABASE_SCHEMA.sql`** — community `flights` table + RLS policies.
- **`windcore.js`** — standalone reference/Node-test of the diagnostic wind model (the model itself is embedded in `index.html`).
- **`build_terrain_tiles.py`** — optional pipeline to precompute slope/aspect/TPI tiles (not required; app analyzes on the fly).
- **`admin.html`** — local-only moderation tool (uses the secret service_role key at runtime; never deploy it).
- Tech: Leaflet 1.9.4, geotiff.js, proj4 (all from CDN). Vanilla JS, **no build step**, single file.
- Data sources (all **keyless + CORS**): AWS Terrarium global DEM tiles (~10–30 m); Esri World Imagery
  (greenness proxy); Open-Meteo forecast (winds 10 m + 925 hPa, gusts, CAPE, low cloud, temp/dewpoint,
  boundary-layer height, `utc_offset_seconds`). Sun position computed astronomically.

## 6. Validation approach (no browser available)

There is **no real browser** in the build environment, so validation is a **headless Node `vm` harness**
that loads the page's `<script>` with DOM/Leaflet/fetch stubs and asserts behavior. Patterns used:
- **Load test**: page loads clean; every `getElementById` has a matching element id.
- **Logic invariants**: thermal score is independent of wind direction; slope score is independent of sun;
  aircraft sweet-spot ordering; flyHour wind ceiling per aircraft; veg suppresses thermal.
- **Field tests** (synthetic terrain): spatial variance (not a uniform wash); hotspots **migrate** E→W
  with the sun; flat open field registers moderate; a field in a hollow scores lower; flat trigger-less
  scene doesn't fake hotspots.
- **Sun/time**: true solar time gives correct angles (1pm high, 8pm near horizon), incl. a no-offset fallback.
- **Physics self-test**: `node windcore.js` checks the wind model (speed-up, lift/sink, lee rotor, N & W winds).

Always re-run the harness before publishing.

## 7. The models (the hard-won part)

### Thermal trigger field (per terrain cell, recomputed at the forecast hour's sun)
Thermals fire where the ground is **hotter than its surroundings** AND terrain **releases** the bubble —
not on absolute warmth (that's a uniform wash). Per cell:
- `insol` = solar incidence on the cell's slope/aspect (× vegetation greenness suppression).
- `contrast` = relu(insol − ~300 m local-mean) × 4.5  → heating **anomaly** (this is what makes hotspots
  localize *and* migrate through the day).
- `release` = multi-scale convexity from TPI at ~80 m and ~300 m (convex breaks / spurs / slope-tops).
- `warmth` = insol gate (must be genuinely sunlit); `dry` = 1 − 0.85·greenness (bare/dry releases hotter).
- `flatness` = open flat ground; `exposure` = from broad ~900 m TPI — a field **hemmed in by hills** (a
  hollow, TPI<0) is sheltered and scores lower; open/exposed flat ground is a legit moderate source.
- Combine: `t = 0.13 + 0.25·(flatness·exposure) + 0.40·release + 0.85·release·contrast + 0.35·contrast`,
  then `raw = warmth·dry·t`, normalized by an **absolute reference** (≈1.0) + gamma 1.35 (NOT per-scene
  percentile — that faked hotspots on flat scenes). Then × wind convergence(+)/lee-sink(−), × the day gate below.

### "Is it a thermal day?" gate (one scalar for the area, gates the whole map)
`idx = heatF × blGate`, where `heatF = sin(sun elevation)·(1 − low_cloud)` and `blGate` ramps the
**boundary-layer (mixing) height** 250 → 1500 m. Dead/capped/overcast/after-dark ⇒ ~0 ⇒ map dims + a
plain "thermals essentially off" banner. **Safety:** if mixing-layer data is missing, the score is capped
and labeled (never reads "Strong/Booming" on sun alone) — the model must be quietest when blindest.
Drives the strength badge, the map dimming, and the **"When to throw today"** hour-by-hour strip.

### Slope / ridge soaring (mechanical, sun-independent)
`align (face into wind) × (0.55·steepness-near-aircraft-sweet-spot + 0.45·release)`, then the diagnostic
wind model's orographic lift, minus empirical lee rotor (scaled by the aircraft's rotor tolerance).

### Sun ↔ time (was a real bug)
Open-Meteo gives **local clock** time; the sun math needs **true solar** time. Convert: clock → UTC (via
`utc_offset_seconds`) → solar (+ longitude/15 + equation-of-time). Both the map shading and the forecast
panel use this one corrected value. Fallback to longitude estimate if offset missing; **invalidate cached
weather that lacks the offset** (older caches caused "sun = 0 in daylight").

### Mechanism colors & aircraft
- **Magenta = thermal**, **cyan = slope/ridge**; in "Both" each cell takes the color of whichever wins.
  (Magenta chosen because warm/brown blended into satellite terrain.)
- **Aircraft profiles** (DLG / 2 m / slope / large, editable) re-drive the flight estimate, the slope
  sweet-spot, rotor tolerance, and the flyable wind ceiling.
- **"Why this score"** popups break thermal & slope into their drivers and label terrain vs the diagnostic
  *model* — transparency is a core principle ("show behind the curtain to build trust").

## 8. Key decisions & rationale (from expert-panel reviews)

Four+ specialist "agents" (DLG pilot, meteorologist, fluid dynamicist, soaring CFI, remote-sensing &
validation scientists, a DLG-crowd PM) reviewed the model. Their conclusions, now baked in:
- **Thermal ≠ slope.** Thermal = sun-driven buoyancy (no wind/steepness dependence); slope = wind-on-a-face
  (no sun). Don't let one fake the other. (An earlier "relief gate" wrongly let steepness boost thermals.)
- **Thermal = heating contrast + release, not absolute warmth** (fixes the "same everywhere" wash) — and it
  should **migrate** E→W through the day (it does now).
- **Flat dry open fields ARE real thermal sources** ("parking lots and dry lakebeds boom") — but **sheltered/
  hemmed-in fields aren't** (exposure term).
- **CFD rejected** — expensive fiction without real-time surface temp / soil moisture / lapse inputs; smarter
  terrain analysis is the right level for a browser tool.
- **Real-time satellite "thermal imagery" (LST) is infeasible** — too coarse (≈1–2 km) or too latent (days).
  Best real-time heating signal is the sun + boundary-layer data we already use.
- **Biggest trust win** was the day gate (don't glow on a dead day). **Highest-value next data** = ESA
  WorldCover land cover; **CFD/imagery = no.**

## 9. Known limitations (be honest with pilots)

- Thermal map is **relative within the view**, gated by the day's overall strength — not an absolute climb rate.
- Marks where a bubble **leaves the ground**, not where it's drifted to by the time you core it (no drift offset yet).
- **No upwind "collector" term** (a release fed by a big sun-warmed slope beats an isolated one) — top deferred item.
- No sea-breeze / convergence-line / valley-breeze detection; no sub-~20 m features (a single dark field/lot).
- Vegetation is an **RGB greenness proxy**, not true NDVI; may misread deep shadow/wet ground.
- Wind model is **diagnostic** (mass-conserving), **not CFD** — no true flow separation; lee rotor is empirical.
- Doesn't read **lapse rate**, so it can under-call punchy post-frontal days.
- **No ground-truth validation yet** (see §10).

## 10. Deferred / roadmap (in rough priority)

1. **Upwind collector/fetch term** — meteorologist's top pick; the difference between "ranks slopes" and "predicts thermals."
2. **Frozen-prediction flight logger** — store the model's prediction with each logged flight, then calibrate
   predicted-vs-actual over time. The only path to *proving* accuracy. (Community flight log already exists.)
3. **ESA WorldCover land cover** — real surface type (bare dirt/rock vs grass/forest) into heating. Needs a CORS check.
4. **METAR wind-model validation** — objective cross-check vs airport observations (analysis, not a UI feature).
5. Drift offset; convergence diagnostic; thermal drift using winds-aloft; PWA/offline; week outlook.

## 11. Restoring context in a new conversation

Start the new chat with: *"Here's my DLG Soaring Finder project — repo at
github.com/Anthony7820/dlg-soaring-finder, and PROJECT_HANDOFF.md has the full context."* Then attach or
paste this file. That restores the architecture, model, decisions, workflow, and roadmap without redoing
the whole journey.
