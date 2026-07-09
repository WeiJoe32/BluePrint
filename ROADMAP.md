---
title: BluePrint Roadmap
date: 2026-07-09
tags:
  - makerlabs
  - blueprint
  - roadmap
  - research
aliases:
  - BluePrint Roadmap
---

# BluePrint — Researched Improvement Roadmap

> Produced 2026-07-09 by a 4-agent research fan-out (measurement accuracy, photo→CAD landscape, Claude API levers, export/ops), each with adversarial verification. **Every claim carries a label**: **Confirmed** = primary source or 2+ independent sources · **Plausible** = one credible source, unrefuted · **Contested** = sources disagree. Sources listed were actually fetched on the date shown.
>
> **For future agents:** pick an item, read its evidence, build it. No code was changed when this was written — the app works as documented in [[instruction-blueprint]]. Do NOT redo this research; do check dated facts (pricing, API limits) if months have passed.

## The one-line strategy

The research consensus across all four angles: **photos give shape, humans/references give numbers.** BluePrint's "one known dimension + human in the loop" design is already on the right side of the hype line — the wins come from feeding Claude *measured* scale instead of asking it to eyeball, and making the output land in CAD.

---

## Tier 1 — small effort, big wins (do these first)

### 1.1 Reference-object scale detection (ArUco marker / credit card) — THE big accuracy win
**Confirmed · Effort S–M · replaces ±10–20% error with ~2%**
User puts a printed ArUco marker (or credit card) next to the object. OpenCV `cv2.aruco.detectMarkers` gives pixels-per-mm with no camera calibration; a validated insurance-industry implementation measured 48mm vs actual 47mm (~2% error) across 40 casual-angle photos. Then **draw the detected scale onto the photo and tell Claude the numbers** — research shows VLMs are genuinely bad at metric estimation (GPT-5 scores <0.4 δ₁ on DepthLM) but rendering markers/scale onto the image beats text coordinates (+0.15 δ₁), and reference-object prompting improved VLM spatial accuracy 20–40pp (SpatialPrompt). This converts Claude's job from estimation to arithmetic. A 3D-printing hobbyist can print an ArUco card once — very on-brand.
Caveats: photo must be near-perpendicular (<~75°), marker in the object's plane.
Sources (fetched 2026-07-09): profil-software.com/blog/.../measuring-the-size-of-objects... (2024, upd. 2026-06-17) · pysource.com/2021/05/28/measure-size-of-an-object... · arxiv.org/html/2509.25413 (DepthLM, 2025-09) · arxiv.org/abs/2409.09788 (SpatialPrompt, 2024-09)

### 1.2 Raise the image downscale target 1568px → 2576px
**Confirmed · Effort S · ~+$0.02/drawing (Sonnet intro)**
The 1568px cap in `static/js/app.js` targets the *old standard tier*. Sonnet 5 and Opus 4.8 are high-res tier: max 2576px long edge, 4,784 tokens/image, automatic, coordinates 1:1 with pixels. For a measurement task, resolution is directly load-bearing. Four images ≈ 19.1K tokens vs 9.4K — cost delta ~5–10% of a drawing. Free extras from the same doc: keep images **before** text in the content array (already done) and label them `Image 1:`, `Image 2:` in the prompt.
Source (fetched 2026-07-09): platform.claude.com/docs/en/build-with-claude/vision.md

### 1.3 Dimension snapping + user override
**Confirmed (practitioner consensus) · Effort S–M**
Round AI-estimated dimensions to sensible values (2.97→3.0; recognize standard sizes M3/M4/M5) and let the user edit any dimension, then re-render. Practitioner reverse-engineering consensus: "if you measure a hole at 2.98mm, it's supposed to be 3mm." Highest value-per-effort UX idea found.
Source (fetched 2026-07-09): hackster.io/news/...zack-freedman...reverse-engineering-for-3d-printing (2024)

### 1.4 Capture coaching + glossy-object warning
**Confirmed · Effort S**
All photogrammetry apps coach capture. Add to the upload UI: "one photo per face + one 3/4 view; matte lighting; avoid reflective surfaces; shoot square-on from as far as zoom allows (kills perspective distortion)."
Source (fetched 2026-07-09): swiftwand.com smartphone-3D-scanning comparison (upd. 2026-07-04)

### 1.5 PWA + localStorage drawing gallery + wake-on-open ping
**Confirmed · Effort S (all three compound)**
- **Gallery:** save `{name, date, dimension, svg}` to localStorage/IndexedDB after each generation (~15KB/SVG → 300+ drawings in 5MB). Zero backend; survives Render restarts by definition. **SQLite on Render free tier is dead on arrival** — Render docs verbatim: local files/SQLite "are lost every time the service redeploys, restarts, or spins down" (15-min idle spin-down; no free persistent disk).
- **PWA:** manifest + minimal service worker (MUST pass through the streaming `/api/generate` POST — never cache/intercept it; cache-first for static shell only). iOS installs via Share > Add to Home Screen; installed apps are exempt from Safari storage eviction — which protects the gallery.
- **Wake ping:** free tier cold-starts ~60s after 15-min idle; fire a ping when the PWA opens so the server wakes while the user frames the photo, and show a "waking the server…" state.
Sources (fetched 2026-07-09): render.com/docs/free · webkit.org/blog/14403 · supabase.com/pricing

### 1.6 Try `effort: "medium"` on the Fast (Sonnet) tier
**Confirmed (mechanism) / Plausible (magnitude) · Effort S (one line)**
Output/thinking tokens are ~85–90% of every drawing's cost. Sonnet 5 defaults to effort `high`; `medium` ≈ Sonnet 4.6 at `high` per migration guidance. A/B a few drawings and compare `usage.output_tokens` before committing. Keep `high` on Best (Opus).

---

## Tier 2 — medium effort, the product-shaping features

### 2.1 Multi-turn refinement ("make the side view bigger") — the killer feature
**Confirmed · Effort M · ~$0.13/refinement turn with caching**
Standard multi-turn: append the full assistant SVG + user instruction, resend everything (do NOT attempt diff-based history — the model needs the verbatim SVG to revise interdependent coordinates). Put a `cache_control: {"type":"ephemeral"}` breakpoint on the last block each turn: refinement turns arrive within the 5-min TTL (which refreshes free on every use), so turn-2+ input reads at 0.1× — ~90% off input. Pair with the **Files API** (upload photos once, reference by `file_id` — same token cost, much smaller request payloads on Render). Don't change model/tools/output_config mid-conversation (cache invalidation).
Sources (fetched 2026-07-09): platform.claude.com prompt-caching.md, vision.md, pricing.md

### 2.2 DXF export → Fusion 360 — the real CAD payoff
**Confirmed · Effort M (S if 2.3 ships first)**
The only good path: **svgelements** (parses SVG paths/arcs/transforms/real units) → **ezdxf** (writes LINE/ARC/LWPOLYLINE, `doc.units = units.MM`, R2010). Pure Python, ~100 lines, no binaries on Render. Critical design fact (**Confirmed negative**): NO converter turns SVG dimension annotations into editable DXF DIMENSION entities — so export **geometry only**. Prompt Claude to put part outlines in `<g id="geometry">` (mm coordinates) separate from annotations. Fusion: imported DXF is treated as unitless-assumed-cm — instruct users "Insert > Insert DXF, set units to mm."
Skip: Inkscape CLI (spline-broken output, own devs disavow it), abandonware converters, LLM-direct DXF (CAD-Coder research: even fine-tuned LLMs hit 40% pass@1 generating DXF; the field generates *code* instead).
Sources (fetched 2026-07-09): github.com/mozman/ezdxf · ezdxf.readthedocs.io units concepts · github.com/meerk40t/svgelements · Autodesk KB DXF-scale article · arxiv.org/html/2505.08686v1 (CAD-Coder)

### 2.3 Parametric SVG (the Adam/CADAM pattern)
**Confirmed · Effort M**
Have Claude emit a parameter dict (widths, heights, hole positions) + an SVG built from those parameters, exposed as editable fields in the UI. One Claude call, infinite user corrections at zero API cost. Also makes 2.2's geometry group trivial and 1.3's override natural.
Source (fetched 2026-07-09): news.ycombinator.com/item?id=48572553 (Adam YC W25 launch)

### 2.4 Standard-part identification pre-check
**Confirmed (pattern exists) · Effort S–M**
If the photographed object IS a standard part (M5 bolt, 608 bearing), the right answer is the datasheet drawing, not a reconstruction. Cheap Claude vision pre-check before drawing ("is this a standard catalogue part?"). Pattern proven by Leo AI.
Source (fetched 2026-07-09): getleo.ai blog (2025-07)

---

## Tier 3 — large effort / watch list

### 3.1 Photo → 3D mesh → deterministic orthographic projection (the ceiling-raiser)
**Confirmed (tech) / Contested (readiness for mechanical parts) · Effort L**
TRELLIS.2-4B (Microsoft, MIT license) or Meshy API ($20/mo, ~$0.40/gen): generate a mesh, then front/top/side views become *deterministic geometry* (trimesh projection + hidden-line removal) — guaranteed mutually consistent, which direct SVG generation can never promise. Claude's role shrinks to labeling/dimensioning. **Honest verdict from the landscape sweep: image-to-3D is real for shape, hype for engineering** — documented failure modes are exactly the mechanical ones (filled holes, hallucinated depth, lost thin features), and dimensional accuracy is not a design goal of any of these models. Needs GPU/hosted inference (Replicate/HF). Revisit when a project genuinely needs consistent views.
Sources (fetched 2026-07-09): github.com/microsoft/TRELLIS.2 · ideate.xyz 5-model mechanical comparison (2025-04) · tripo3d.ai mesh-repair guides · voxelmatters.com Backflip analysis (2025-03)

### 3.2 SVG edit-script refinement (patch, don't regenerate)
**Plausible · Effort L**
For small tweaks, have the model emit search/replace pairs applied server-side — output drops ~13K→1–2K tokens (~$0.10 saved/tweak). Risks: SVG edits cascade; needs full-regen fallback. Only after 2.1 ships and only if refinement volume justifies it.

### 3.3 WebXR AR measure (Android Chrome only)
**Confirmed (iOS gap) / Plausible (accuracy) · Effort M**
iOS Safari has zero WebXR support through v26.5 (caniuse, fetched 2026-07-09) — iPhone LiDAR is unreachable from a web app, period. Android hit-test gets ~±1cm. Mostly duplicated by 1.1 at lower cost; do later, if ever.

---

## Skip list — researched dead ends (don't re-litigate without new evidence)

| Idea | Why not (label) |
|---|---|
| Caching the system prompt alone (single-shot use) | Net **negative** at a few calls/day: 5-min TTL expires between calls → every call pays the 1.25× write premium; best case saves ~1% of drawing cost (**Confirmed**, math in research) |
| 1-hour cache TTL | 2× write premium needs ≥2 reads/hour; usage pattern won't deliver (**Confirmed**) |
| LLM generates DXF/STEP directly | Research consensus: generate code/SVG instead; 40% pass@1 even fine-tuned (**Confirmed**, CAD-Coder) |
| SQLite on Render free tier | Filesystem wiped on every restart/spin-down, multiple times daily (**Confirmed**, Render docs) |
| Inkscape CLI as server-side SVG→DXF | Broken splines/text; Inkscape devs disavow the exporter; huge dependency (**Confirmed**) |
| Wrapping the whole SVG in structured-output JSON | Escaping overhead, all-or-nothing truncation, breaks streaming preview, cache invalidation on toggle (**Confirmed**) |
| Raw edge-map (Canny) overlays to help Claude measure | Zero published evidence for edges alone; annotated *scale references* have evidence (**Confirmed absence** — fine as a free A/B, don't build around it) |
| More photos to fix accuracy | **Contested/unbenchmarked** — multi-view helps only via structured 3D fusion; some evidence LVLMs degrade with multiple images. Keep 1–4 for face coverage, not precision |
| Photogrammetry app integration (Polycam etc.) | Needs 20–200 photos; can't hit sub-0.1mm on small objects anyway; different problem (**Confirmed**) |
| Batch API for the live flow | Up-to-1h latency kills the streaming UX; only for a future async "drawing pack" tier (**Confirmed**) |
| Fast mode | 2× cost for speed nobody asked for (**Confirmed** pricing) |
| Waiting for Backflip / commercial scan-to-CAD | Invite-only, scan-input (not photos), pro pricing signaled (**Confirmed**) |
| 24/7 keep-alive pinging Render | Wasteful; wake-on-open ping (1.5) does the job (**Confirmed**) |

## Dated facts to re-check before building (they expire)

- **Sonnet 5 intro pricing ($2/$10 per MTok) ends 2026-08-31** → all Sonnet cost figures rise 50% after (Confirmed, pricing doc 2026-07-09)
- Minimum cacheable prefix: **1,024 tokens on Sonnet 5 AND Opus 4.8** (corrects older skill data claiming 2,048/4,096). BluePrint's ~1.1K system prompt clears it with only ~76 tokens of margin — trimming the prompt below 1,024 silently disables caching (Confirmed, caching doc 2026-07-09)
- Render free tier: 750 instance-hrs/mo, 15-min spin-down, ~60s cold start, no persistent disk (Confirmed, 2026-07-09)
- Supabase free: 2-active-project cap — Wei Jie is likely AT the cap; reuse an existing project's DB if cross-device history is ever built (Confirmed, 2026-07-09)

## Suggested build order for a future agent

1. **1.2 + 1.6** (two one-liners: 2576px, effort medium) → 2. **1.5** (PWA + gallery + wake ping) → 3. **1.1 + 1.3** (ArUco scale + snapping/override — the accuracy leap) → 4. **2.3 then 2.2** (parametric SVG, then DXF export) → 5. **2.1** (multi-turn refinement) → 6. reassess Tier 3.

## Linked
- [[instruction-blueprint]]
- [[MakerLabs]]
