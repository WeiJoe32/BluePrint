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
Upload 1–4 photos of a physical object + type **one known dimension** (e.g. height = 120mm). Claude vision analyzes the photos and generates a professional-style engineering drawing sheet as a single SVG — front/top/side/isometric views, zone-grid border, title block, dimension lines, optional section + detail views. View it in the browser, zoom, print, or download the `.svg`.

Built for prototyping: get a dimensioned reference sheet of any part before modeling it in CAD.

## Honest Accuracy Bar (read this)
- Only the **given dimension is exact** — every other number is a proportional estimate (**±10–20%**)
- Faces not in any photo are **inferred** from symmetry (the sheet says so)
- Section views show **plausible guessed internals**, labeled "INFERRED"
- Thread callouts are guesses marked with `?` (e.g. `~M4?`)

→ Verify with calipers before manufacturing. This is a first-draft reference sheet, not a manufacturing drawing.

## Stack
Flask + gunicorn · Claude API vision (`claude-haiku-4-5` Draft / `claude-sonnet-5` Fast / `claude-opus-4-8` Best, streaming) · vanilla JS frontend · Render (free tier) · PIN login (protects API credits)

## Which quality tier?
| Tier | Model | ~Cost/drawing | Verdict from the chair test (2026-07-09) |
|------|-------|--------------|------------------------------------------|
| Draft | Haiku 4.5 | ~$0.03–0.08 | Structure OK, **ignored the cm unit** and wrote mm. Layout previews only |
| **Fast** | Sonnet 5 | ~$0.10–0.25 | **Default. Best value** — correct units, ~10 dimensions, correctly *omitted* the section view on a solid chair and said why |
| Best | Opus 4.8 | ~$0.30–0.60 | Finest linework + detail views. Reserve for drawings that matter |

Note: Haiku does not support adaptive thinking, so `app.py` omits that parameter for the Draft tier (it would 400).

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

> The key currently lives ONLY in `Fitness/fitness-tracker/.env`. BluePrint's `.env` was blanked after testing (stub mode on). To generate for real, paste your key into `BluePrint/.env` and remove the `BLUEPRINT_STUB=1` line, then restart. Tip: hard-refresh (Ctrl+F5) after any UI change — the browser caches `style.css`.

## Files
| File | Purpose |
|------|---------|
| `app.py` | Flask app: PIN auth, `/api/generate` NDJSON streaming endpoint, Claude call, SVG sanitizer, stub mode |
| `config.py` | Env vars + model constants (`MODEL_DRAFT`/`MODEL_FAST`/`MODEL_BEST`, `MAX_IMAGES=4`) |
| `templates/index.html` | Single page: upload → progress → viewer states |
| `templates/login.html` | PIN login card |
| `static/js/app.js` | Photo downscale (1568px JPEG) + encode, NDJSON stream reader, viewer (zoom/print/download) |
| `static/css/style.css` | Dark blueprint theme, print CSS |
| `Procfile` | `gunicorn --timeout 300` (generations run 30–90s) |
| `.env.example` | Template for secrets — copy to `.env`, never commit `.env` |

## API Contract
`POST /api/generate` (login required), JSON:
```json
{"images": ["<bare base64 jpeg>"], "dimension_label": "height", "dimension_value": 120,
 "unit": "mm", "description": "optional", "quality": "fast"}
```
`unit` is `mm`, `cm`, or `in` — the whole sheet is dimensioned in that unit.
Response: streamed NDJSON lines — `status` → `heartbeat`/`progress` → `done` (`{"svg": ...}`) or `error` (`{"message": ...}`). Heartbeats fire every 5s from a background thread so Render's proxy never sees an idle connection during the model's thinking phase. Pre-stream failures use normal HTTP codes (400 bad input, 401 auth, 503 no API key).

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

### Free-tier realities (verified 2026-07-09, see [[ROADMAP]])
- Spins down after **15 min** idle → first request then takes **~60s** to wake. Not broken, just cold.
- 750 instance-hours/month (one service fits comfortably).
- **Ephemeral filesystem** — nothing written to disk survives a restart. Irrelevant today (app is stateless), but it's why the roadmap says never put SQLite here.
- Auto-deploys on every push to `main`.

### After deploy
- Add the URL to `CLAUDE.md`'s GitHub list and to this file.
- Roadmap item 1.5 (PWA + wake-on-open ping) is what makes the cold start tolerable at the workbench — good first improvement.

## Future Improvements
Researched roadmap (4-agent fan-out, 2026-07-09) lives in [[ROADMAP]] (`ROADMAP.md`) — ranked Tier 1/2/3 improvements with evidence labels, sources, effort sizes, a skip-list of researched dead ends, and a suggested build order. **Future agents: start there, don't redo the research.** Headline: ArUco/credit-card reference scale detection gets ~2% dimensional error vs today's ±10–20%.

## Known Limitations
- Dimensions are estimates (see Accuracy Bar) — the reference-quality CAD look does not mean CAD precision
- Complex curved/organic parts produce rougher projections than boxy/cylindrical parts
- One generation at a time (single free-tier worker); a generation can't be resumed if the tab closes
- Cancel stops the browser download but the server-side model call runs to completion (tokens still billed)
