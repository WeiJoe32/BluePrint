---
title: instruction-blueprint
date: 2026-07-09
tags:
  - makerlabs
  - project
  - flask
  - claude-api
aliases:
  - BluePrint
---

# BluePrint — Photos → Engineering Drawing

## What It Does
Upload 1–4 photos of a physical object + type **1–6 known dimensions** (e.g. length = 4.5cm, width = 2cm — every extra dimension tightens accuracy). Claude vision analyzes the photos and draws the drawing *content* (views + dimensions); **the server composes the finished sheet around it** — border, zone grid, title block, a **mathematically computed isometric view**, and code-rendered detail views. View it in the browser, zoom, print, download `.svg`, or **export a DXF for Fusion 360**. **Refine it by chatting** ("make the side view bigger") or hit **Review & fix** to have the model critique its own rendered drawing and correct visual defects. Drawings **auto-save to a gallery** in your browser. Installable as a **PWA** for the workbench.

For real accuracy, **print the scale marker** (`/marker` page) and lay it flat beside the part — OpenCV measures the photo's true px/mm and Claude gets measured numbers instead of eyeballing.

## Honest Accuracy Bar (read this)
- **With the printed ArUco marker in frame (flat, same plane as the face, shot square-on): ~2% dimensional error** under good conditions
- Without the marker: your given dimensions are exact, everything else is a proportional estimate (**±10–20%**) — give more known dimensions to tighten this
- Derived dimensions are **snapped to design intent** (39.4 → 40; hole near Ø4.5 → "Ø4.5 (M4 CLEARANCE)") — given dimensions are never snapped
- **The isometric view is computed geometry, not model guesswork** — exact whenever the part is one clean extrusion (brackets, plates, PCBs); non-prismatic parts (turned/curved) show "PICTORIAL VIEW OMITTED" instead of a wrong drawing
- Faces not in any photo are **inferred** from symmetry (the sheet says so); section views show guessed internals, labeled "INFERRED"

→ Still verify with calipers before machining. Engineer-level *drafting*, not metrology.

## Stack
Flask + gunicorn · Claude API vision (`claude-haiku-4-5` Draft / `claude-sonnet-5` Fast / `claude-opus-4-8` Best, streaming) · vanilla JS frontend · Render (free tier) · PIN login (protects API credits)

## Which quality tier?
| Tier | Model | Measured cost (v3, IC-chip test 2026-07-10) | Verdict |
|------|-------|------|---------|
| Draft | Haiku 4.5 | **$0.04**, ~28s | Fastest by far (photos auto-downscale for this tier — 2.6× fewer input tokens). No detail view this run. Great for layout previews |
| **Fast** | Sonnet 5 | **$0.22**, ~173s | **Default. Best value** — added a detail view unprompted where it judged one useful. Correctly *omitted* a section view on a solid chair (chair test, v2) and explained why |
| Best | Opus 4.8 | **$0.63**, ~279s | Finest orthographic-view fidelity and dimensioning. No detail view this run (judgment call, not a failure) |

Note: the isometric view is **identical across all three tiers** (v3+) — it's computed server-side from geometry, not drawn by the model. Tier only affects the orthographic views, dimensioning judgment, and whether a detail view gets requested. Haiku does not support adaptive thinking, so `app.py` omits that parameter for the Draft tier (it would 400).

## UI
Cyanotype drafting-sheet theme: Prussian-blue grid paper, chalk-cyan linework, uppercase mono drafting lettering, sharp corners, single amber accent (`#ffd166`) for actions. The upload panel is framed as a drawing sheet (double border, corner register marks, title-block strip); the progress bar is a dashed line being drafted. Generated drawings render as white paper on the blue desk.

## How to Run

**Local, free (no API calls) — stub mode:**
```powershell
cd "MakerLabs\Projects\BluePrint"
pip install -r requirements.txt
# .env: APP_PIN=1234 and BLUEPRINT_STUB=1
python app.py     # → http://127.0.0.1:5000, login with PIN
```
Stub mode streams fake progress + a canned sample sheet — test the whole UI at zero cost. A yellow "Stub mode" badge shows in the header.

**Local, real generation:**
`.env`: `ANTHROPIC_API_KEY=sk-ant-...`, `APP_PIN=...`, remove/blank `BLUEPRINT_STUB`. Costs per the tier table above.

> Tip: hard-refresh (Ctrl+F5) after any UI change — the browser AND the service worker cache static files. `app.py` appends `?v=<file mtime>` to every static URL automatically, so once you've done one hard refresh after a deploy, future loads pick up changes on their own — always bump `CACHE_VERSION` in `static/js/sw.js` too when app.js/style.css/index.html change (belt-and-braces so an installed PWA never gets stuck).

## Features

### v3 (2026-07-10) — server-composed sheets, the "engineer-level drawings" upgrade
Triggered by: the model-drawn isometric view was bad and detail views were weak. Full research (ROADMAP.md "Drawing Quality research") found LLM 3D projection is a *measured* weakness prompting can't fix — so the architecture changed: **the model now draws ONLY the content** (orthographic views + dimensions + notes, inside a fixed content rectangle it's told about) — never the border, zone grid, title block, isometric, or detail views. The server composes everything else:
- **Computed isometric (`iso_projection.py`):** extrudes the front-view outline by the side view's depth and projects it with real 30° isometric trigonometry — mathematically exact, not model freehand. Gated on the part reading as one clean extrusion; non-prismatic parts get "PICTORIAL VIEW OMITTED (NON-PRISMATIC PART)" instead of a wrong drawing.
- **Server-drawn furniture (`sheet_compose.py`):** border, zone grid, title block are a code template — pixel-perfect every time, and the model no longer wastes output tokens redrawing them.
- **Code-rendered detail views:** the model emits a spec (`{center, radius, scale, label}`) for up to 2 features worth magnifying; the server clones the real geometry, scales it, clips it to a circle, and draws the callout ring + leader — geometrically exact at exact ratio, never a hand-drawn approximation.
- **Multi-dimension input:** up to 6 known dimensions (was 1) — every one is authoritative, marked `*`, never snapped. More dimensions = tighter derived accuracy.
- **"Review & fix" button:** rasterizes the finished sheet (resvg-py + bundled DejaVu Sans font — cairosvg breaks on Render, avoid it), shows the render back to Claude alongside your first photo, asks for up to 5 concrete defects + a corrected version. Single round, user-triggered (costs ~one drawing). You see a before/after and choose "Use fixed version" or "Keep original" — the critic is an assist, not an authority (it reliably catches overlapping text/colliding views, not fine misalignment).
- Refine and review both operate on the model's **content fragment** — the server extracts it from the composed sheet you're already holding, so the wire format stays one shape (composed sheet) everywhere the frontend touches it. Drawings from before this upgrade can't be refined/reviewed/DXF'd (`422 old_drawing` / `no_geometry`) — generate a new one.

### v2 (2026-07-09) — accuracy + workflow
- **Scale marker (`/marker`):** print the page (verify the 100mm bar with a ruler — must be exactly 100mm), cut out the 50mm ArUco marker, lay it flat beside the part in the same plane as the photographed face. The app detects it per photo (badge "SCALE ✓ 12.4 px/mm" on the thumbnail), draws the measured scale onto the image, and tells Claude the calibration. Angle warning if shot too obliquely — retake square-on.
- **Refinement chat:** after generating, type an instruction in the viewer ("make the side view 20% larger", "add a dimension to the hole") — up to 8 turns per drawing. Prompt caching makes follow-up turns ~90% cheaper on input when sent within ~5 minutes of each other. Quality tier locks for the drawing session (switching models mid-chat would break the cache).
- **DXF export:** Download DXF button → geometry-only DXF (front/top/side layers, true mm). In Fusion 360: **Insert > Insert DXF, set units to mm**. Text/dimensions don't convert (no tool on earth does that from SVG) — geometry only.
- **Gallery:** every drawing auto-saves to browser localStorage (~300 fit). Rename, reopen, delete. Reopened drawings support view/print/download/DXF, not refine/review (photos only live in the session). Old-format gallery entries (single dimension) still render.
- **PWA:** install to phone home screen (iOS: Share > Add to Home Screen). The installed app opens a "waking the workshop server…" page that polls until Render's ~60s cold start finishes. Installing also protects the gallery from Safari's storage eviction.
- **Capture coaching:** tips above the upload — one photo per face + a 3/4 view, square-on from far (zoom in), matte lighting, glossy parts confuse the AI.

## Files
| File | Purpose |
|------|---------|
| `app.py` | Flask app: PIN auth, streaming `/api/generate` + `/api/refine` + `/api/review`, `/api/detect-scale`, `/api/export-dxf`, `/marker`, `/healthz`, SVG sanitizer, cache-busting static URLs, stub mode |
| `sheet_compose.py` | Composes the full sheet around model content: furniture (border/zone-grid/title-block), computed iso, code detail views; `extract_content_fragment()` is the inverse (used by refine/review) |
| `iso_projection.py` | The computed isometric — extrudes `geo-front` by `geo-side` depth, projects with 30° trig, hidden-line removal; returns `None` when the part isn't a clean extrusion |
| `aruco_scale.py` | OpenCV ArUco detection + scale annotation onto the photo |
| `dxf_export.py` | SVG geometry group → DXF (svgelements + ezdxf, mm, R2010) |
| `config.py` | Env vars + constants (models, `MAX_IMAGES=4`, `MAX_DIMENSIONS=6`, `MARKER_SIZE_MM=50`, `MAX_REFINE_TURNS=8`) |
| `templates/index.html` | 4 states: upload (multi-dimension rows) → progress → viewer (refine bar + Review & fix) + gallery |
| `templates/marker.html` | Printable 50mm ArUco marker + 100mm print-check bar |
| `templates/login.html` | PIN login card |
| `static/js/app.js` | Downscale (2576px), scale detection, multi-dimension rows, NDJSON reader, refine, review, gallery, DXF, viewer |
| `static/js/sw.js` + `static/manifest.webmanifest` + `static/launch.html` | PWA: cache-first static shell (never touches `/api/*`), wake-on-open page — bump `CACHE_VERSION` in sw.js on every frontend release |
| `static/img/aruco-0.png` | The committed marker asset (DICT_5X5_50, id 0) |
| `static/fonts/DejaVuSans.ttf` | Bundled font for resvg-py rasterization (Review & fix) — resvg loads no system fonts |
| `scripts/make_marker.py`, `scripts/make_icons.py` | Dev-time asset generators |
| `test_blueprint.py` | Offline test suite (23 tests) — `python test_blueprint.py` with `BLUEPRINT_STUB=1 APP_PIN=1234` |
| `Procfile` | `gunicorn --timeout 300` (generations run 30–280s) |
| `.env.example` | Template for secrets — copy to `.env`, never commit `.env` |

## API Contract
- `POST /api/generate`: `{images: [b64], dimensions: [{label, value}] (1–6), unit (mm|cm|in), description, quality (draft|fast|best), scale?: {px_per_mm, marker_photo, marker_size_mm}}` → NDJSON stream `status` → `heartbeat`/`progress` → `done {svg}` / `error {message}`. `svg` is the full **composed** sheet.
- `POST /api/refine`: generate fields + `turns: [{instruction, svg}]` (composed sheets, oldest first, turn 0 instruction empty, max 8) + `instruction` → same NDJSON stream. `422 {"error":"old_drawing"}` on a pre-v3 sheet.
- `POST /api/review`: generate fields + `svg` (current composed sheet) → NDJSON stream ending in `done {"svg": "<revised composed sheet>"|null, "defects": ["...", ...]}` (`svg: null` = no defects found). `422 old_drawing` on a pre-v3 sheet.
- `POST /api/detect-scale`: `{image}` → `{found, px_per_mm, marker_size_mm, annotated_image, angle_warning}` or `{found: false}`
- `POST /api/export-dxf`: `{svg}` → DXF download, or 422 `no_geometry`
- `GET /healthz` (no auth) → `{"ok": true}` — the PWA wake ping

Heartbeats fire every 5s from a background thread so Render's proxy never sees an idle connection during the model's thinking phase (Opus/Sonnet runs have thought silently for 225–247s in testing — the heartbeat is load-bearing, not decorative). Pre-stream failures use normal HTTP codes (400 bad input, 401 auth, 413 oversized, 422 old drawing, 503 no API key). The server log prints per-run `model / input_tokens / output_tokens / cache_creation_input_tokens / cache_read_input_tokens / stop_reason` — your exact cost check and the proof that refinement/review caching works.

**Safety:** model SVG output is sanitized server-side (scripts/event handlers/external refs stripped via ElementTree) AND rendered client-side through `<img>` (browsers never execute scripts in SVG-in-img).

## Deploy (Render, dashboard-configured — no render.yaml)

**GitHub: ✅ DONE 2026-07-09** → https://github.com/WeiJoe32/BluePrint (public, `main`).
Verified at push time: `.env` never left the machine, no `sk-ant-` pattern in any pushed file. Only `.env.example` is in the repo.

### ⬜ TODO — Render setup (~10 min, all in the browser)

**Step 0 — generate a secret key.** In any terminal:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
Copy the 64-character output. It only signs the login cookie — it just has to be unguessable. Do NOT reuse the fitness-tracker's.

**Step 1 — create the service.** [render.com](https://render.com) → **New → Web Service** → connect GitHub repo `WeiJoe32/BluePrint`.

| Setting | Value |
|---|---|
| Language / Runtime | Python 3 |
| Branch | `main` |
| Build command | `pip install -r requirements.txt` |
| Start command | leave blank — auto-detected from `Procfile` |
| Instance type | **Free** |

**Step 2 — Environment tab, add 4 variables** (never commit these):

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key — copy the line from `Fitness/fitness-tracker/.env` |
| `APP_PIN` | any PIN you'll remember (this is the only thing guarding your API credits) |
| `FLASK_SECRET_KEY` | the 64-char string from Step 0 |
| `SESSION_COOKIE_SECURE` | `true` |

Do **not** set `PORT` (Render injects it) and do **not** set `BLUEPRINT_STUB` (leave it off so real drawings generate).

**Step 3 — Deploy**, then open the URL, log in with `APP_PIN`, and generate one real drawing to confirm end-to-end.

**Step 4 — if something's wrong**, check the Render log:
- `WARNING: missing env vars: ...` → a variable name is misspelled in Step 2
- login page loops / won't stay logged in → `SESSION_COOKIE_SECURE` isn't `true`, or `FLASK_SECRET_KEY` is missing
- 503 `not_configured` on Generate → `ANTHROPIC_API_KEY` not set
- Every successful generation logs `[BluePrint] model=... input_tokens=... output_tokens=... stop_reason=...` — that's your cost check

> Note: v2 added `opencv-python-headless`, v3 added `resvg-py` — Render builds take a bit longer. Runtime RAM stays within the free tier's 512MB (both are lazy-imported, only paid for on the routes that use them).

### Free-tier realities (verified 2026-07-09, see [[ROADMAP]])
- Spins down after **15 min** idle → first request then takes **~60s** to wake. Not broken, just cold.
- 750 instance-hours/month (one service fits comfortably).
- **Ephemeral filesystem** — nothing written to disk survives a restart. Irrelevant today (app is stateless), but it's why the roadmap says never put SQLite here.
- Auto-deploys on every push to `main`.

### After deploy
- Add the URL to `CLAUDE.md`'s GitHub list and to this file.
- Roadmap item 1.5 (PWA + wake-on-open ping) is what makes the cold start tolerable at the workbench — good first improvement.

## Future Improvements
Two researched roadmap sections in [[ROADMAP]] (`ROADMAP.md`) — **future agents: start there, don't redo the research.**
1. Original 4-agent fan-out (2026-07-09): Tier 1/2/3 improvements — scale detection, refinement, DXF, PWA, image-to-3D landscape.
2. "Drawing Quality research" (2026-07-10): the isometric/detail-view fixes shipped in v3, PLUS still-open ideas — server-drawn dimension rendering (Q3's other half), the numeric plan-then-draw and few-shot-exemplar A/B experiments (Q6, unbuilt), part-ID pre-check.

## Known Limitations
- Dimensions are estimates without a marker (see Accuracy Bar) — the reference-quality CAD look does not mean CAD precision
- Isometric requires the part to read as one clean extrusion (boxy/prismatic parts) — turned, curved, or compound-body parts show "PICTORIAL VIEW OMITTED" instead of a guessed drawing
- Complex curved/organic parts produce rougher orthographic projections too
- One generation at a time (single free-tier worker); a generation can't be resumed if the tab closes
- Cancel stops the browser download but the server-side model call runs to completion (tokens still billed)
- Refine/review/DXF only work on drawings generated after the v3 upgrade (pre-v3 sheets lack the composed-sheet structure they need)
- Review & fix is single-round only, by design — it catches gross visual defects (overlapping text, colliding views), not fine misalignment
