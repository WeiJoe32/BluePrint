"""
SVG -> DXF export for BluePrint (geometry only).

Claude wraps the part linework of the three orthographic views in
<g id="geometry" data-mm-per-unit="K"> with <g id="geo-front|geo-top|geo-side">
subgroups. This module pulls that geometry out and writes a millimetre-scaled DXF
(R2010) that opens in Fusion 360 etc. Dimensions, text, and the isometric view are
NOT exported — no SVG->DXF path preserves dimension annotations, so we export the
pure geometry and let the CAD tool re-dimension.

SVG y grows downward; DXF y grows upward. So every point maps (x, y) -> (x*K, -y*K).

Public surface:
    class NoGeometryError
    svg_to_dxf(svg_text: str) -> bytes
"""

import io
import math
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"

# geometry subgroup id -> DXF layer name.
_LAYER_MAP = {"geo-front": "FRONT", "geo-top": "TOP", "geo-side": "SIDE"}
_LAYERS = ("FRONT", "TOP", "SIDE", "GEOMETRY")


class NoGeometryError(Exception):
    """Raised when the SVG has no <g id="geometry"> block (older drawings)."""


def _local(tag):
    """Local tag name without its XML namespace ('{ns}g' -> 'g')."""
    return tag.split("}")[-1]


def _t(x, y, K):
    """Map an SVG point to DXF space: scale by K mm/unit and flip Y."""
    return (float(x) * K, -float(y) * K)


def svg_to_dxf(svg_text: str) -> bytes:
    """Convert the geometry group of an SVG drawing sheet into DXF bytes."""
    import ezdxf

    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg_text)  # ParseError propagates -> caller maps to 400

    # Find the geometry group anywhere in the tree.
    geom = None
    for el in root.iter():
        if el.get("id") == "geometry":
            geom = el
            break
    if geom is None:
        raise NoGeometryError("drawing has no <g id='geometry'> block")

    # Real millimetres per SVG unit (defaults to 1.0 if unlabelled).
    try:
        K = float(geom.get("data-mm-per-unit", "1.0"))
    except (TypeError, ValueError):
        K = 1.0

    # Split geometry children into named subgroups vs loose shapes.
    subgroups = []       # (layer, <g> element)
    direct_shapes = []   # shapes drawn straight under <g id="geometry">
    for child in list(geom):
        if _local(child.tag) == "g":
            subgroups.append((_LAYER_MAP.get(child.get("id"), "GEOMETRY"), child))
        else:
            direct_shapes.append(child)

    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for name in _LAYERS:
        if not doc.layers.has_entry(name):
            doc.layers.add(name)

    for layer, group in subgroups:
        _add_elements(msp, [group], K, layer)
    if direct_shapes:
        _add_elements(msp, direct_shapes, K, "GEOMETRY")

    out = io.StringIO()
    doc.write(out)
    return out.getvalue().encode("utf-8")


def _add_elements(msp, elements, K, layer):
    """Serialize SVG elements to a standalone SVG, parse with svgelements (so any
    transforms are honoured), and add each shape to the DXF modelspace."""
    from svgelements import SVG

    inner = "".join(ET.tostring(e, encoding="unicode") for e in elements)
    wrapper = f'<svg xmlns="{SVG_NS}">{inner}</svg>'
    parsed = SVG.parse(io.StringIO(wrapper))
    for shape in parsed.elements():
        _shape_to_dxf(shape, K, msp, layer)


def _shape_to_dxf(shape, K, msp, layer):
    """Add one svgelements shape to the modelspace on the given layer.
    Groups, text, and anything unrecognised are silently skipped."""
    from svgelements import (SimpleLine, Rect, Circle, Ellipse, Polyline,
                             Polygon, Path)

    attribs = {"layer": layer}

    if isinstance(shape, SimpleLine):
        msp.add_line(_t(shape.x1, shape.y1, K), _t(shape.x2, shape.y2, K),
                     dxfattribs=attribs)

    elif isinstance(shape, Rect):
        x, y = float(shape.x), float(shape.y)
        w, h = float(shape.width), float(shape.height)
        pts = [_t(x, y, K), _t(x + w, y, K), _t(x + w, y + h, K), _t(x, y + h, K)]
        msp.add_lwpolyline(pts, close=True, dxfattribs=attribs)

    elif isinstance(shape, (Polygon, Polyline)):
        pts = [_t(p[0], p[1], K) for p in shape.points]
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, close=isinstance(shape, Polygon),
                               dxfattribs=attribs)

    elif isinstance(shape, Circle):
        radius = ((float(shape.rx) + float(shape.ry)) / 2.0) * K
        msp.add_circle(_t(shape.cx, shape.cy, K), radius, dxfattribs=attribs)

    elif isinstance(shape, Ellipse):
        # Near-circular ellipse -> circle; otherwise flatten to a polyline.
        rx, ry = float(shape.rx), float(shape.ry)
        if abs(rx - ry) < 1e-6 * max(rx, ry, 1.0):
            msp.add_circle(_t(shape.cx, shape.cy, K), rx * K, dxfattribs=attribs)
        else:
            _path_to_dxf(Path(shape), K, msp, attribs)

    elif isinstance(shape, Path):
        _path_to_dxf(shape, K, msp, attribs)


def _path_to_dxf(path, K, msp, attribs):
    """Walk a path's segments: straight runs become LWPOLYLINEs, circular arcs
    become DXF ARCs, and other curves are flattened to short polylines."""
    from svgelements import Move, Line, Close, Arc, QuadraticBezier, CubicBezier

    run = []  # points accumulated for the current polyline

    def flush(closed=False):
        if len(run) >= 2:
            msp.add_lwpolyline(list(run), close=closed, dxfattribs=attribs)
        run.clear()

    for seg in path:
        # Close subclasses Line in svgelements, so test it first.
        if isinstance(seg, Move):
            flush()
            run.append(_t(seg.end[0], seg.end[1], K))
        elif isinstance(seg, Close):
            flush(closed=True)
        elif isinstance(seg, Arc):
            if _is_circular(seg):
                flush()
                _emit_arc(seg, K, msp, attribs)
                run.append(_t(seg.end[0], seg.end[1], K))
            else:
                run.extend(_flatten(seg, K))
        elif isinstance(seg, (QuadraticBezier, CubicBezier)):
            run.extend(_flatten(seg, K))
        elif isinstance(seg, Line):
            run.append(_t(seg.end[0], seg.end[1], K))

    flush()


def _is_circular(arc):
    """True if an SVG elliptical arc is effectively circular (rx ~= ry)."""
    rx, ry = float(arc.rx), float(arc.ry)
    return abs(rx - ry) < 1e-6 * max(rx, ry, 1.0)


def _emit_arc(arc, K, msp, attribs):
    """Emit a circular SVG arc as a DXF ARC. Angles are derived from the mapped
    start/end points, and the arc that passes through the midpoint is chosen so
    the Y-flip can't reverse the sweep."""
    cx, cy = _t(arc.center[0], arc.center[1], K)
    sx, sy = _t(arc.start[0], arc.start[1], K)
    ex, ey = _t(arc.end[0], arc.end[1], K)
    mid = arc.point(0.5)
    mx, my = _t(mid[0], mid[1], K)

    radius = math.hypot(sx - cx, sy - cy)
    a0 = math.degrees(math.atan2(sy - cy, sx - cx)) % 360
    a1 = math.degrees(math.atan2(ey - cy, ex - cx)) % 360
    am = math.degrees(math.atan2(my - cy, mx - cx)) % 360

    # DXF arcs sweep counter-clockwise from a0 to a1. Keep the order that sweeps
    # through the midpoint.
    if (am - a0) % 360 > (a1 - a0) % 360:
        a0, a1 = a1, a0
    msp.add_arc((cx, cy), radius, a0, a1, dxfattribs=attribs)


def _flatten(seg, K, n=20):
    """Sample a curved segment into n points (SVG y-flipped)."""
    import numpy as np

    points = seg.npoint(np.linspace(0.0, 1.0, n))
    return [_t(row[0], row[1], K) for row in points]
