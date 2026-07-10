"""
Deterministic isometric view for BluePrint (the researched Q1 fix).

The model is bad at freehand 3D projection (a MEASURED weakness — see ROADMAP.md
"Drawing Quality research"). So the model never draws the isometric; this module
builds it from the machine-readable geometry group instead.

Idea: the front view (geo-front) gives the face outline; the side view (geo-side)
gives the part depth. If the part reads as ONE clean extrusion of the front face
(prismatic — brackets, plates, PCBs), we extrude that face by the depth and project
every vertex with a standard isometric transform. Non-prismatic parts (curved outer
boundary, non-rectangular side) fail the gate and we return None so the caller can
show "PICTORIAL VIEW OMITTED" instead.

Public surface:
    compute_isometric(geometry, target_box) -> str | None   # an SVG <g>, or None

`geometry` is either the <g id="geometry"> ElementTree element or the full SVG
text; `target_box` is the (x, y, w, h) slot on the sheet to fit the drawing into.
"""

import math
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# Isometric constants. In an isometric projection each axis is drawn at 30 degrees
# from the horizontal, so a model point (x, yup, z) projects to:
#     x' = (x - z) * cos30
#     y' = yup + (x + z) * sin30
# Here x/yup are the front-face axes (yup points UP) and z is depth into the part.
# Increasing z moves the point up and to the left, so the z=0 cap (the photographed
# front face) sits in front (down-right) and the z=depth cap sits behind (up-left).
COS30 = math.cos(math.radians(30))   # ~0.8660254
SIN30 = 0.5                          # sin(30 degrees) is exactly 0.5


def _iso_point(x, yup, z):
    """Project one model-space point to isometric screen space (y still points UP)."""
    return ((x - z) * COS30, yup + (x + z) * SIN30)


def _build_prism(front_pts, depth):
    """Project a prism: the front polygon at z=0 (near cap) and z=depth (far cap).

    `front_pts` are (x, yup) pairs in a math frame (y points up). Returns
    (near_pts, far_pts) as lists of projected (x', y') isometric points, index-aligned
    so near_pts[i] and far_pts[i] are the two ends of extrusion edge i.
    """
    near = [_iso_point(x, yup, 0.0) for (x, yup) in front_pts]
    far = [_iso_point(x, yup, depth) for (x, yup) in front_pts]
    return near, far


# ----------------------------------------------------------------------------
# Geometry-group parsing
# ----------------------------------------------------------------------------

def _local(tag):
    return tag.split("}")[-1]


def _find_id(root, target):
    for el in root.iter():
        if el.get("id") == target:
            return el
    return None


def _rect_points(el):
    x, y = float(el.get("x", 0)), float(el.get("y", 0))
    w, h = float(el.get("width", 0)), float(el.get("height", 0))
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _points_attr(el):
    raw = (el.get("points") or "").replace(",", " ").split()
    nums = [float(n) for n in raw]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


# Path commands that draw curves — their presence means the outline is not a clean
# straight-edged polygon, so the extrusion gate must reject the part.
_CURVE_CMDS = set("CcSsQqTtAa")


def _path_points(el):
    """Parse a straight-line-only path 'd' into points, or None if it has curves."""
    d = el.get("d", "")
    if any(c in _CURVE_CMDS for c in d):
        return None
    # Tokenise into commands and numbers.
    tokens = []
    num = ""
    for ch in d:
        if ch in "MmLlHhVvZz":
            if num:
                tokens.append(num); num = ""
            tokens.append(ch)
        elif ch in " ,\t\n\r":
            if num:
                tokens.append(num); num = ""
        elif ch in "-" and num and num[-1] not in "eE":
            tokens.append(num); num = "-"
        else:
            num += ch
    if num:
        tokens.append(num)

    pts = []
    cx = cy = 0.0
    cmd = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in "MmLlHhVvZz":
            cmd = t
            i += 1
            if cmd in "Zz":
                continue
        if cmd in "MmLl":
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
            if cmd.islower():
                x, y = cx + x, cy + y
            cx, cy = x, y
        elif cmd in "Hh":
            x = float(tokens[i]); i += 1
            cx = cx + x if cmd.islower() else x
        elif cmd in "Vv":
            y = float(tokens[i]); i += 1
            cy = cy + y if cmd.islower() else y
        else:
            i += 1
            continue
        pts.append((cx, cy))
    return pts


def _outline_points(el):
    """Return (points, is_circle). points is None if the shape has curves (reject)."""
    tag = _local(el.tag)
    if tag == "rect":
        return _rect_points(el), False
    if tag in ("polygon", "polyline"):
        return _points_attr(el), False
    if tag == "path":
        return _path_points(el), False   # None if curved
    if tag in ("circle", "ellipse"):
        return None, True                # holes / curved — handled separately
    return [], False                     # line, text, etc. — ignore as outline


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _poly_area(pts):
    """Absolute shoelace area of a polygon."""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _largest_outline(group):
    """Pick the biggest straight-edged outline in a subgroup, plus its hole circles.

    Returns (outline_pts, holes, ok). ok is False if any outline shape has curves
    (the extrusion gate must then fail). holes is a list of (cx, cy, r)."""
    best = None
    best_area = 0.0
    holes = []
    for el in group:
        pts, is_circle = _outline_points(el)
        if is_circle:
            if _local(el.tag) == "circle":
                holes.append((float(el.get("cx", 0)), float(el.get("cy", 0)),
                              float(el.get("r", 0))))
            continue
        if pts is None:
            return None, holes, False     # curved path in the outline -> reject
        if len(pts) >= 3:
            x0, y0, x1, y1 = _bbox(pts)
            area = (x1 - x0) * (y1 - y0)
            if area > best_area:
                best_area = area
                best = pts
    return best, holes, True


def _parse_geometry(geom):
    """Extract (front_outline, holes, depth) from the geometry group, or None if the
    extrudability gate fails."""
    front_g = _find_id(geom, "geo-front")
    side_g = _find_id(geom, "geo-side")
    if front_g is None or side_g is None:
        return None

    front, holes, ok = _largest_outline(front_g)
    if not ok or not front:
        return None

    side, _side_holes, side_ok = _largest_outline(side_g)
    if not side_ok or not side:
        return None

    # Gate: the side view must read as a rectangle-ish profile (one clean extrusion).
    # Fill ratio = polygon area / bounding-box area; a rectangle is 1.0, a triangle
    # ~0.5. Anything below ~0.85 is not a simple prism.
    sx0, sy0, sx1, sy1 = _bbox(side)
    bbox_area = (sx1 - sx0) * (sy1 - sy0)
    if bbox_area <= 0:
        return None
    if _poly_area(side) / bbox_area < 0.85:
        return None

    depth = sx1 - sx0        # side view's width in SVG units == part depth
    if depth <= 0:
        return None
    return front, holes, depth


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

def _svg_y_up(pts):
    """Convert SVG points (y grows DOWN) to a math frame (y grows UP) by negating y."""
    return [(x, -y) for (x, y) in pts]


def _circle_polyline(cx, cyup, r, z, n=28):
    """Sample a front-face circle into projected isometric points (an ellipse)."""
    out = []
    for k in range(n + 1):
        a = 2 * math.pi * k / n
        out.append(_iso_point(cx + r * math.cos(a), cyup + r * math.sin(a), z))
    return out


def compute_isometric(geometry, target_box):
    """Build an isometric-view SVG <g> from the geometry group, fitted into
    target_box = (x, y, w, h). Returns the <g> string, or None when the part is not a
    clean extrusion (caller shows a "PICTORIAL VIEW OMITTED" placeholder instead)."""
    if isinstance(geometry, str):
        root = ET.fromstring(geometry)
        geom = root if root.get("id") == "geometry" else _find_id(root, "geometry")
        if geom is None:
            return None
    else:
        geom = geometry

    parsed = _parse_geometry(geom)
    if parsed is None:
        return None
    front_svg, holes_svg, depth = parsed

    # Move to a y-up math frame so the isometric formula reads naturally.
    front = _svg_y_up(front_svg)
    holes = [(cx, -cy, r) for (cx, cy, r) in holes_svg]

    near, far = _build_prism(front, depth)
    hole_polys = [_circle_polyline(cx, cyup, r, 0.0) for (cx, cyup, r) in holes]

    # Collect every projected point to size the drawing.
    allpts = list(near) + list(far)
    for hp in hole_polys:
        allpts.extend(hp)
    minx = min(p[0] for p in allpts)
    maxx = max(p[0] for p in allpts)
    miny = min(p[1] for p in allpts)
    maxy = max(p[1] for p in allpts)
    span_x = maxx - minx or 1.0
    span_y = maxy - miny or 1.0

    tx, ty, tw, th = target_box
    pad = 24
    label_h = 26                       # room for the "ISOMETRIC VIEW" caption
    box_w = tw - 2 * pad
    box_h = th - 2 * pad - label_h
    scale = min(box_w / span_x, box_h / span_y)
    draw_w = span_x * scale
    draw_h = span_y * scale
    off_x = tx + (tw - draw_w) / 2.0
    off_y = ty + pad + (box_h - draw_h) / 2.0

    def screen(pt):
        # Map isometric (y-up) point into sheet coordinates (y-down).
        sx = off_x + (pt[0] - minx) * scale
        sy = off_y + (maxy - pt[1]) * scale
        return sx, sy

    g = ET.Element(f"{{{SVG_NS}}}g", {"id": "iso-view"})

    def poly(pts, closed, dash=None, width="2"):
        pairs = [screen(p) for p in pts]
        d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pairs)
        if closed:
            d += " Z"
        attrs = {"d": d, "fill": "none", "stroke": "black", "stroke-width": width}
        if dash:
            attrs["stroke-dasharray"] = dash
        ET.SubElement(g, f"{{{SVG_NS}}}path", attrs)

    # Painter's order: far (hidden) cap dashed first, then extrusion edges, then the
    # near (visible) front-face cap solid on top. NOTE: true hidden-line removal is not
    # done — every extrusion edge is drawn solid. This reads correctly for convex-ish
    # prisms (the common case); concave outlines may show an edge that should be hidden.
    poly(far, True, dash="6 5", width="1.2")
    for a, b in zip(near, far):
        sa, sb = screen(a), screen(b)
        ET.SubElement(g, f"{{{SVG_NS}}}line",
                      {"x1": f"{sa[0]:.2f}", "y1": f"{sa[1]:.2f}",
                       "x2": f"{sb[0]:.2f}", "y2": f"{sb[1]:.2f}",
                       "stroke": "black", "stroke-width": "2"})
    poly(near, True, width="2")
    # Holes project onto the near (front) face only.
    for hp in hole_polys:
        poly(hp, False, width="1.5")

    # Caption underneath.
    ET.SubElement(g, f"{{{SVG_NS}}}text",
                  {"x": f"{tx + tw / 2:.1f}", "y": f"{ty + th - 6:.1f}",
                   "font-family": "DejaVu Sans, sans-serif", "font-size": "18",
                   "fill": "black", "text-anchor": "middle"}).text = "ISOMETRIC VIEW"

    return ET.tostring(g, encoding="unicode")
