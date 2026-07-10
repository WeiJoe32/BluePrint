"""
Server-composed drawing sheets for BluePrint (the researched Q3 + Q4 fixes).

The model no longer draws the whole A3 sheet. It draws ONLY the "content": the
three orthographic views + dimensions + notes, inside a fixed content rectangle.
This module then builds the final 1600x1131 sheet around it:

  - sheet furniture (double border, zone grid, title block) drawn by code, so it
    is pixel-perfect every time and costs the model zero output tokens (Q3)
  - the model content, hard-clipped to the content rectangle via <clipPath>
  - a computed isometric view (iso_projection.py, Q1) — never model-drawn
  - detail views rendered by code from tiny JSON specs the model emits (Q4)

Layout map (sheet coordinates, viewBox 0 0 1600 1131):

    (20,20)  outer border ......................... (1580,1111)
    (40,40)  inner border ......................... (1560,1091)
    (60,60)  CONTENT AREA 1080x840 ................ (1140,900)
    (1150,60)   isometric slot 400x360
    (1150,440)  detail slot A 400x220
    (1150,670)  detail slot B 400x220
    (1180,950)  title block 380x135

The model is told to use viewBox "0 0 1080 840"; the server translates its
content to (60,60) and clips it, so nothing the model draws can ever spill over
the furniture.

Public surface:
    CONTENT_VIEWBOX / CONTENT_X / CONTENT_Y / CONTENT_W / CONTENT_H
    compose_sheet(model_content_svg, meta) -> str        # full sheet SVG
    extract_content_fragment(composed_svg) -> str | None # inverse of compose
"""

import copy
import json
import math
import xml.etree.ElementTree as ET
from html import escape

import iso_projection

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# --- layout constants -------------------------------------------------------
CONTENT_X, CONTENT_Y = 60, 60
CONTENT_W, CONTENT_H = 1080, 840
CONTENT_VIEWBOX = f"0 0 {CONTENT_W} {CONTENT_H}"

ISO_SLOT = (1150, 60, 400, 360)                       # x, y, w, h
DETAIL_SLOTS = [(1150, 440, 400, 220), (1150, 670, 400, 220)]
TITLE_X, TITLE_Y, TITLE_W, TITLE_H = 1180, 950, 380, 135

# On paper an A3 landscape sheet is 420 mm wide and our viewBox is 1600 units,
# so one SVG unit prints at 420/1600 = 0.2625 mm. Comparing that with the real
# millimetres per unit (data-mm-per-unit) gives the drawing scale ratio.
_PAPER_MM_PER_UNIT = 420.0 / 1600.0

_FONT = "DejaVu Sans, sans-serif"


def _local(tag):
    return tag.split("}")[-1]


def _find_id(root, target):
    for el in root.iter():
        if el.get("id") == target:
            return el
    return None


def _extract_svg(text):
    start = text.find("<svg")
    end = text.rfind("</svg>")
    if start == -1 or end == -1:
        return None
    return text[start:end + 6]


# ----------------------------------------------------------------------------
# Furniture (border, zone grid, title block) — plain string building so a
# reader can see exactly what ends up on the sheet.
# ----------------------------------------------------------------------------

def _zone_grid():
    """Double border with zone letters F..A down both sides and numbers 8..1
    across top and bottom, with tick marks — like a professional CAD sheet."""
    parts = [
        '<rect x="20" y="20" width="1560" height="1091" fill="none" stroke="black" stroke-width="3"/>',
        '<rect x="40" y="40" width="1520" height="1051" fill="none" stroke="black" stroke-width="1.5"/>',
    ]
    letters = "FEDCBA"                      # top-to-bottom
    row_h = 1091 / 6.0
    for i, letter in enumerate(letters):
        cy = 20 + (i + 0.5) * row_h + 5      # +5 nudges text to visual center
        for x in (30, 1570):
            parts.append(f'<text x="{x}" y="{cy:.0f}" font-family="{_FONT}" '
                         f'font-size="14" fill="black" text-anchor="middle">{letter}</text>')
    for i in range(1, 6):                    # row boundary ticks
        y = 20 + i * row_h
        parts.append(f'<line x1="20" y1="{y:.1f}" x2="40" y2="{y:.1f}" stroke="black" stroke-width="1"/>')
        parts.append(f'<line x1="1560" y1="{y:.1f}" x2="1580" y2="{y:.1f}" stroke="black" stroke-width="1"/>')
    col_w = 1560 / 8.0
    for j in range(8):                       # numbers 8..1 left-to-right
        cx = 20 + (j + 0.5) * col_w
        num = 8 - j
        for y in (36, 1106):
            parts.append(f'<text x="{cx:.0f}" y="{y}" font-family="{_FONT}" '
                         f'font-size="14" fill="black" text-anchor="middle">{num}</text>')
    for j in range(1, 8):                    # column boundary ticks
        x = 20 + j * col_w
        parts.append(f'<line x1="{x:.1f}" y1="20" x2="{x:.1f}" y2="40" stroke="black" stroke-width="1"/>')
        parts.append(f'<line x1="{x:.1f}" y1="1091" x2="{x:.1f}" y2="1111" stroke="black" stroke-width="1"/>')
    return parts


def _scale_line(mm_per_unit):
    """Human-readable drawing scale derived from data-mm-per-unit, e.g. 'SCALE 1:2'."""
    if mm_per_unit is None or mm_per_unit <= 0:
        return "SCALE: NTS"                  # not to scale (no calibration known)
    ratio = mm_per_unit / _PAPER_MM_PER_UNIT  # real mm : printed mm
    if ratio >= 1:
        return f"SCALE 1:{ratio:.3g}"
    return f"SCALE {1 / ratio:.3g}:1"


def _title_block(part_name, unit, mm_per_unit):
    x, y, w, h = TITLE_X, TITLE_Y, TITLE_W, TITLE_H
    name = escape(part_name.upper()[:34])
    return [
        f'<text x="{x + 15}" y="{y - 10}" font-family="{_FONT}" font-size="14" fill="black">* GIVEN DIMENSION</text>',
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="black" stroke-width="1.5"/>',
        f'<line x1="{x}" y1="{y + 40}" x2="{x + w}" y2="{y + 40}" stroke="black" stroke-width="1"/>',
        f'<text x="{x + 15}" y="{y + 27}" font-family="{_FONT}" font-size="18" fill="black">PART: {name}</text>',
        f'<text x="{x + 15}" y="{y + 68}" font-family="{_FONT}" font-size="15" fill="black">{escape(_scale_line(mm_per_unit))}   UNITS: {escape(unit)}</text>',
        f'<text x="{x + 15}" y="{y + 96}" font-family="{_FONT}" font-size="15" fill="black">DRAWN BY: BLUEPRINT AI</text>',
        f'<text x="{x + 15}" y="{y + 124}" font-family="{_FONT}" font-size="15" fill="black">SHEET 1/1</text>',
    ]


# ----------------------------------------------------------------------------
# Detail views (Q4) — the model emits <desc id="detail-specs">[JSON]</desc>;
# the server clones the named geo-* subgroup, magnifies, clips to a circle.
# ----------------------------------------------------------------------------

def _parse_detail_specs(content_root):
    """Read up to 2 valid detail specs from the content's desc element.
    Malformed input is skipped silently (logged) — never breaks the sheet."""
    desc = _find_id(content_root, "detail-specs")
    if desc is None or not (desc.text or "").strip():
        return []
    try:
        raw = json.loads(desc.text)
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"[BluePrint] detail-specs JSON invalid, skipping: {exc}")
        return []
    if not isinstance(raw, list):
        print("[BluePrint] detail-specs is not a list, skipping")
        return []
    specs = []
    for item in raw[:2]:
        try:
            view = item["view"]
            cx, cy, r = float(item["cx"]), float(item["cy"]), float(item["r"])
            scale = float(item.get("scale", 2))
            label = str(item.get("label", "A"))[:1] or "A"
            if view not in ("front", "top", "side") or r <= 0 or scale <= 0:
                raise ValueError("bad values")
        except (KeyError, TypeError, ValueError) as exc:
            print(f"[BluePrint] detail spec skipped: {item!r} ({exc})")
            continue
        specs.append({"view": view, "cx": cx, "cy": cy, "r": r,
                      "scale": scale, "label": label})
    return specs


def _detail_views(specs, content_root):
    """Render each detail spec into its reserved slot. Returns SVG strings."""
    parts = []
    for i, spec in enumerate(specs):
        if i >= len(DETAIL_SLOTS):
            break
        geo = _find_id(content_root, f"geo-{spec['view']}")
        if geo is None:
            print(f"[BluePrint] detail spec skipped: no geo-{spec['view']} group")
            continue

        sx, sy, sw, sh = DETAIL_SLOTS[i]
        label_h = 26
        # Slot center and the largest circle that fits above the caption.
        ccx = sx + sw / 2.0
        ccy = sy + (sh - label_h) / 2.0
        max_r = min(sw, sh - label_h) / 2.0 - 8

        s = spec["scale"]
        ring_r = min(s * spec["r"], max_r)   # keep the true magnification (2:1);
        clip_r = ring_r / s                  # if too big, show a smaller AREA instead
        # Place the clone so that content point (cx, cy) lands on the slot center.
        tx = ccx - s * spec["cx"]
        ty = ccy - s * spec["cy"]

        clone = copy.deepcopy(geo)
        clone.attrib.pop("id", None)         # ids must stay unique in the sheet
        clone_svg = ET.tostring(clone, encoding="unicode")
        clip_id = f"clip-detail-{spec['label']}-{i}"
        ratio = f"{s:g}:1"

        parts.append(
            f'<clipPath id="{clip_id}"><circle cx="{ccx:.1f}" cy="{ccy:.1f}" r="{ring_r:.1f}"/></clipPath>')
        parts.append(
            f'<g clip-path="url(#{clip_id})"><g transform="translate({tx:.2f} {ty:.2f}) scale({s:g})">{clone_svg}</g></g>')
        # Ring + caption around the magnified view.
        parts.append(
            f'<circle cx="{ccx:.1f}" cy="{ccy:.1f}" r="{ring_r:.1f}" fill="none" stroke="black" stroke-width="1.5"/>')
        parts.append(
            f'<text x="{ccx:.1f}" y="{sy + sh - 6}" font-family="{_FONT}" font-size="18" fill="black" '
            f'text-anchor="middle">DETAIL {escape(spec["label"])} ({ratio})</text>')

        # Callout ring on the parent view (content coordinates -> +60/+60 on sheet)
        # plus a leader line from the callout toward the detail circle.
        pcx = CONTENT_X + spec["cx"]
        pcy = CONTENT_Y + spec["cy"]
        parts.append(
            f'<circle cx="{pcx:.1f}" cy="{pcy:.1f}" r="{spec["r"]:.1f}" fill="none" stroke="black" '
            f'stroke-width="1" stroke-dasharray="6 4"/>')
        parts.append(
            f'<text x="{pcx + spec["r"] + 4:.1f}" y="{pcy - spec["r"] - 4:.1f}" font-family="{_FONT}" '
            f'font-size="16" fill="black">{escape(spec["label"])}</text>')
        # Leader: from the callout edge toward the detail slot center (stops short).
        dx, dy = ccx - pcx, ccy - pcy
        dist = math.hypot(dx, dy) or 1.0
        ux, uy = dx / dist, dy / dist
        x1, y1 = pcx + ux * spec["r"], pcy + uy * spec["r"]
        x2, y2 = ccx - ux * ring_r, ccy - uy * ring_r
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="black" '
            f'stroke-width="0.8" stroke-dasharray="10 6"/>')
    return parts


# ----------------------------------------------------------------------------
# Compose / extract
# ----------------------------------------------------------------------------

def compose_sheet(model_content_svg, meta):
    """Build the full 1600x1131 sheet: furniture + clipped model content +
    computed isometric + code-rendered detail views.

    meta: {"description": str|None, "unit": str}
    Raises ValueError when the model content is not parseable SVG.
    """
    raw = _extract_svg(model_content_svg)
    if raw is None:
        raise ValueError("model content has no <svg> document")
    try:
        content_root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"model content SVG is malformed: {exc}")

    geometry = _find_id(content_root, "geometry")
    mm_per_unit = None
    if geometry is not None:
        try:
            mm_per_unit = float(geometry.get("data-mm-per-unit", ""))
        except (TypeError, ValueError):
            mm_per_unit = None

    part_name = (meta.get("description") or "").strip() or "UNTITLED PART"
    unit = meta.get("unit", "mm")

    # Serialize the model's children as-is (keeps its <defs>, arrow markers,
    # and the detail-specs <desc> so the fragment can be reconstructed later).
    inner = "".join(ET.tostring(child, encoding="unicode")
                    for child in list(content_root))

    parts = [
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 1600 1131" width="100%">',
        '<rect x="0" y="0" width="1600" height="1131" fill="white"/>',
    ]
    parts += _zone_grid()
    parts += _title_block(part_name, unit, mm_per_unit)

    # The clip guarantees model output can never overlap the furniture.
    # NOTE: clip-path is resolved in the clipped group's LOCAL coordinates
    # (i.e. after its translate), so the rect is 0,0 .. CONTENT_W,CONTENT_H —
    # not the sheet-space 60,60 .. 1140,900.
    parts.append(f'<clipPath id="clip-content">'
                 f'<rect x="0" y="0" width="{CONTENT_W}" height="{CONTENT_H}"/>'
                 f'</clipPath>')
    parts.append(f'<g id="model-content" clip-path="url(#clip-content)" '
                 f'transform="translate({CONTENT_X} {CONTENT_Y})">{inner}</g>')

    # Isometric view (Q1) — computed, never model-drawn.
    iso = None
    if geometry is not None:
        try:
            iso = iso_projection.compute_isometric(geometry, ISO_SLOT)
        except Exception as exc:   # never let iso math break the sheet
            print(f"[BluePrint] isometric computation failed: {exc!r}")
            iso = None
    if iso is not None:
        parts.append(iso)
    else:
        ix, iy, iw, ih = ISO_SLOT
        parts.append(f'<text x="{ix + iw / 2}" y="{iy + ih / 2}" font-family="{_FONT}" '
                     f'font-size="15" fill="black" text-anchor="middle">'
                     f'PICTORIAL VIEW OMITTED (NON-PRISMATIC PART)</text>')

    # Detail views (Q4).
    parts += _detail_views(_parse_detail_specs(content_root), content_root)

    parts.append("</svg>")
    return "".join(parts)


def extract_content_fragment(composed_svg):
    """Recover the model's content SVG from a composed sheet, or None.

    This is how refine and review reconstruct what the model authored: the
    composed sheet keeps the content byte-preserved (structurally) inside
    <g id="model-content">, so wrapping those children back in an <svg> with the
    content viewBox reproduces the model's own document. Deterministic, so the
    same sheet always yields the same fragment (keeps prompt-cache prefixes
    stable across refine turns).
    """
    raw = _extract_svg(composed_svg)
    if raw is None:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    holder = _find_id(root, "model-content")
    if holder is None:
        return None
    inner = "".join(ET.tostring(child, encoding="unicode") for child in list(holder))
    return (f'<svg xmlns="{SVG_NS}" viewBox="{CONTENT_VIEWBOX}" width="100%">'
            f'{inner}</svg>')
