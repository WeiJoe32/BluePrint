"""
Offline tests for BluePrint — runs entirely in STUB_MODE (no Claude API calls).

    python test_blueprint.py

Every check is a plain assert. Stub mode fakes the AI, so this exercises routing,
validation, the NDJSON stream shape, SVG sanitising, the SERVER-COMPOSED sheet
pipeline (furniture + clipped content + computed isometric + detail views),
review validation/stream shape, and real DXF export — without spending a cent.
"""

import base64
import io
import json
import math
import os

# Must be set BEFORE importing app (config reads env at import time).
os.environ["BLUEPRINT_STUB"] = "1"
os.environ["APP_PIN"] = "1234"

import app as blueprint  # noqa: E402
import iso_projection    # noqa: E402
import sheet_compose     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# A valid bare-base64 string (no data: prefix). Content doesn't matter in stub mode.
IMG = base64.b64encode(b"pretend-this-is-a-jpeg").decode("ascii")

VALID_BODY = {
    "images": [IMG],
    "dimensions": [{"label": "overall width", "value": 60}],
    "unit": "mm",
    "quality": "fast",
}


def authed_client():
    """A test client that has logged in with the PIN."""
    client = blueprint.app.test_client()
    resp = client.post("/login", data={"pin": "1234"})
    assert resp.status_code in (302, 303), resp.status_code
    return client


def read_ndjson(resp):
    """Collect a streaming NDJSON response into a list of dicts."""
    body = resp.get_data(as_text=True)
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def composed_stub_svg():
    """The composed sample sheet, exactly as the stub generate endpoint emits it."""
    return blueprint.sanitize_svg(
        sheet_compose.compose_sheet(blueprint.SAMPLE_CONTENT_SVG, blueprint._stub_meta()))


def test_healthz_no_auth():
    client = blueprint.app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200, resp.status_code
    assert resp.get_json() == {"ok": True}
    print("PASS  healthz 200 no-auth")


def test_auth_gate():
    client = blueprint.app.test_client()
    # API returns 401 JSON
    resp = client.post("/api/generate", json=VALID_BODY)
    assert resp.status_code == 401, resp.status_code
    resp = client.post("/api/review", json=VALID_BODY)
    assert resp.status_code == 401, resp.status_code
    # Browser page redirects to login
    resp = client.get("/")
    assert resp.status_code in (301, 302, 303), resp.status_code
    assert "/login" in resp.headers.get("Location", "")
    print("PASS  auth gate (401 API / redirect page)")


def test_login():
    client = blueprint.app.test_client()
    bad = client.post("/login", data={"pin": "0000"})
    assert bad.status_code == 200 and b"Wrong PIN" in bad.data
    good = client.post("/login", data={"pin": "1234"})
    assert good.status_code in (302, 303)
    # Now authed: the home page renders
    home = client.get("/")
    assert home.status_code == 200
    print("PASS  login")


def test_generate_validation():
    client = authed_client()

    def expect_400(body, label):
        resp = client.post("/api/generate", json=body)
        assert resp.status_code == 400, f"{label}: got {resp.status_code}"

    expect_400({**VALID_BODY, "images": []}, "no images")
    expect_400({**VALID_BODY, "images": [IMG] * 5}, "too many images")
    expect_400({**VALID_BODY, "images": ["data:image/jpeg;base64,AAAA"]}, "data: prefix")
    expect_400({**VALID_BODY, "unit": "furlong"}, "bad unit")
    expect_400({**VALID_BODY, "quality": "turbo"}, "bad quality")
    expect_400({**VALID_BODY, "scale": {"px_per_mm": -8, "marker_photo": 0, "marker_size_mm": 50}},
               "negative px_per_mm")
    expect_400({**VALID_BODY, "scale": {"px_per_mm": 8, "marker_photo": 3, "marker_size_mm": 50}},
               "marker_photo out of range")
    print("PASS  generate validation 400s")


def test_multi_dimension_validation():
    client = authed_client()

    def expect_400(body, label):
        resp = client.post("/api/generate", json=body)
        assert resp.status_code == 400, f"{label}: got {resp.status_code}"

    dim = {"label": "width", "value": 60}
    expect_400({**VALID_BODY, "dimensions": []}, "0 dimensions")
    expect_400({**VALID_BODY, "dimensions": [dim] * 7}, "7 dimensions")
    expect_400({**VALID_BODY, "dimensions": "width=60"}, "dimensions not a list")
    expect_400({**VALID_BODY, "dimensions": [{"label": "w", "value": 0}]}, "zero value")
    expect_400({**VALID_BODY, "dimensions": [{"label": "w", "value": -3}]}, "negative value")
    expect_400({**VALID_BODY, "dimensions": [{"label": "w", "value": "abc"}]}, "string value")
    expect_400({**VALID_BODY, "dimensions": [{"label": "w", "value": True}]}, "bool value")
    expect_400({**VALID_BODY, "dimensions": [{"label": "  ", "value": 60}]}, "blank label")
    expect_400({**VALID_BODY, "dimensions": [{"value": 60}]}, "missing label")
    # Old single-dimension fields no longer count as dimensions.
    expect_400({"images": [IMG], "dimension_label": "width", "dimension_value": 60,
                "unit": "mm", "quality": "fast"}, "legacy single-dimension payload")

    # 6 dimensions is the maximum and must be accepted.
    ok = client.post("/api/generate", json={**VALID_BODY, "dimensions": [dim] * 6})
    assert ok.status_code == 200, ok.status_code
    print("PASS  multi-dimension validation")


def test_generate_stub_stream():
    client = authed_client()
    resp = client.post("/api/generate", json=VALID_BODY)
    assert resp.status_code == 200, resp.status_code
    events = read_ndjson(resp)
    assert events[0]["type"] == "status", events[0]
    assert any(e["type"] == "progress" for e in events), "no progress event"
    done = events[-1]
    assert done["type"] == "done", done
    svg = done["svg"]
    # Composed sheet = furniture markers AND the machine-readable geometry.
    assert 'id="geometry"' in svg
    assert "data-mm-per-unit" in svg
    assert "DRAWN BY: BLUEPRINT AI" in svg                # title block
    assert "SHEET 1/1" in svg
    assert "* GIVEN DIMENSION" in svg
    assert ">F<" in svg and ">A<" in svg                  # zone letters
    assert ">8<" in svg and ">1<" in svg                  # zone numbers
    assert 'id="model-content"' in svg                    # clipped content group
    assert "clip-content" in svg
    assert "ISOMETRIC VIEW" in svg                        # computed iso (box passes gate)
    assert "DETAIL A (2:1)" in svg                        # detail spec consumed
    print("PASS  generate stub stream (composed sheet)")


def test_sanitize_svg():
    dirty = (
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        '<script>alert(2)</script>'
        '<a href="https://evil.example.com">x</a>'
        '<use href="#ok"/>'
        '<rect x="0" y="0" width="10" height="10"/>'
        '</svg>'
    )
    clean = blueprint.sanitize_svg(dirty)
    assert clean is not None
    assert "script" not in clean.lower()
    assert "onload" not in clean.lower()
    assert "evil.example.com" not in clean
    assert 'href="#ok"' in clean or "#ok" in clean
    print("PASS  sanitize_svg regression")


def test_detect_scale_stub():
    client = authed_client()
    resp = client.post("/api/detect-scale", json={"image": IMG})
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()
    assert data["found"] is True
    assert data["px_per_mm"] == 8.0
    assert data["marker_size_mm"] == blueprint.config.MARKER_SIZE_MM
    assert data["annotated_image"] == IMG  # stub echoes the input
    assert data["angle_warning"] is False
    # Validation: bad base64
    bad = client.post("/api/detect-scale", json={"image": "not base64!!!"})
    assert bad.status_code == 400, bad.status_code
    print("PASS  detect-scale stub shape")


def _turn(instruction, svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>'):
    return {"instruction": instruction, "svg": svg}


def test_refine_validation():
    client = authed_client()

    def expect_400(body, label):
        resp = client.post("/api/refine", json=body)
        assert resp.status_code == 400, f"{label}: got {resp.status_code}"

    base = {**VALID_BODY, "turns": [_turn("")], "instruction": "make it bigger"}
    expect_400({**base, "instruction": "   "}, "empty instruction")
    expect_400({**base, "turns": [_turn("")] + [_turn("x") for _ in range(9)]}, ">8 turns")
    expect_400({**base, "turns": [{"instruction": "", "svg": "no markup here"}]},
               "turn missing <svg")
    print("PASS  refine validation 400s")


def test_refine_stub_stream():
    client = authed_client()
    body = {**VALID_BODY, "turns": [_turn("")], "instruction": "make the side view bigger"}
    resp = client.post("/api/refine", json=body)
    assert resp.status_code == 200, resp.status_code
    events = read_ndjson(resp)
    assert events[0]["type"] == "status", events[0]
    done = events[-1]
    assert done["type"] == "done", done
    assert "REFINED (STUB)" in done["svg"]
    assert "make the side view bigger" in done["svg"]
    # The refined stub is also a full composed sheet.
    assert "DRAWN BY: BLUEPRINT AI" in done["svg"]
    assert 'id="geometry"' in done["svg"]
    print("PASS  refine stub stream (composed sheet)")


def test_review_validation():
    client = authed_client()
    good_svg = composed_stub_svg()

    def expect(code, body, label):
        resp = client.post("/api/review", json=body)
        assert resp.status_code == code, f"{label}: got {resp.status_code}"

    base = {**VALID_BODY, "svg": good_svg}
    expect(400, {**VALID_BODY}, "missing svg")
    expect(400, {**VALID_BODY, "svg": "no markup"}, "svg without <svg")
    expect(400, {**base, "dimensions": []}, "review inherits dimension validation")
    expect(413, {**base, "svg": "<svg" + "x" * (2 * 1024 * 1024)}, "oversized svg")
    print("PASS  review validation 400s/413")


def test_review_stub_stream():
    client = authed_client()
    resp = client.post("/api/review", json={**VALID_BODY, "svg": composed_stub_svg()})
    assert resp.status_code == 200, resp.status_code
    events = read_ndjson(resp)
    assert events[0]["type"] == "status", events[0]
    progresses = [e for e in events if e["type"] == "progress"]
    assert len(progresses) == 2, f"expected 2 progress lines, got {len(progresses)}"
    done = events[-1]
    assert done["type"] == "done", done
    assert "REVIEWED (STUB)" in done["svg"]
    assert isinstance(done["defects"], list) and len(done["defects"]) == 2
    print("PASS  review stub stream shape")


def test_parse_review_reply():
    # NONE case → no defects, no SVG needed.
    defects, svg = blueprint._parse_review_reply("DEFECTS: NONE")
    assert defects == [] and svg is None
    # Defect list + corrected SVG.
    reply = ("DEFECTS:\n- Text overlaps the front view\n- Side view collides\n"
             '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    defects, svg = blueprint._parse_review_reply(reply)
    assert defects == ["Text overlaps the front view", "Side view collides"], defects
    assert svg is not None and svg.startswith("<svg")
    print("PASS  review reply parsing")


# ----------------------------------------------------------------------------
# iso_projection unit tests
# ----------------------------------------------------------------------------

def test_iso_vertex_math():
    """Verify the projection formula against hand-computed values.
    x' = (x - z)*cos30, y' = yup + (x + z)*sin30."""
    cos30 = math.cos(math.radians(30))
    x, y = iso_projection._iso_point(10, 0, 5)
    assert abs(x - 5 * cos30) < 1e-9, x          # (10-5)*cos30 = 4.3301...
    assert abs(y - 7.5) < 1e-9, y                # 0 + (10+5)*0.5 = 7.5
    x, y = iso_projection._iso_point(0, 20, 0)
    assert abs(x - 0) < 1e-9 and abs(y - 20) < 1e-9
    # A unit box: near cap at z=0 keeps yup, far cap rises by depth*sin30.
    near, far = iso_projection._build_prism([(0, 0), (10, 0), (10, 10), (0, 10)], 4)
    assert abs(near[1][0] - 10 * cos30) < 1e-9   # (10,0) at z=0 -> x' = 10*cos30
    assert abs(near[1][1] - 5.0) < 1e-9          # y' = 0 + 10*0.5
    assert abs(far[0][0] - (-4 * cos30)) < 1e-9  # (0,0) at z=4 -> x' = -4*cos30
    assert abs(far[0][1] - 2.0) < 1e-9           # y' = 0 + 4*0.5
    print("PASS  iso vertex math (hand-computed)")


_GEO_BOX = """<g xmlns="http://www.w3.org/2000/svg" id="geometry" data-mm-per-unit="1">
  <g id="geo-front"><rect x="0" y="0" width="100" height="60"/>
    <circle cx="50" cy="30" r="10"/></g>
  <g id="geo-top"><rect x="0" y="-80" width="100" height="40"/></g>
  <g id="geo-side"><rect x="120" y="0" width="40" height="60"/></g>
</g>"""

_GEO_LSHAPE = """<g xmlns="http://www.w3.org/2000/svg" id="geometry" data-mm-per-unit="1">
  <g id="geo-front"><polygon points="0,0 60,0 60,30 30,30 30,60 0,60"/></g>
  <g id="geo-top"><rect x="0" y="-80" width="60" height="40"/></g>
  <g id="geo-side"><rect x="80" y="0" width="40" height="60"/></g>
</g>"""

# Side view is a triangle (bbox fill ratio 0.5 < 0.85) -> not one clean extrusion.
_GEO_NON_PRISM = """<g xmlns="http://www.w3.org/2000/svg" id="geometry" data-mm-per-unit="1">
  <g id="geo-front"><rect x="0" y="0" width="100" height="60"/></g>
  <g id="geo-top"><rect x="0" y="-80" width="100" height="40"/></g>
  <g id="geo-side"><polygon points="120,0 160,0 140,60"/></g>
</g>"""


def test_iso_box():
    out = iso_projection.compute_isometric(_GEO_BOX, (0, 0, 400, 360))
    assert out is not None and "ISOMETRIC VIEW" in out
    assert "path" in out and "line" in out       # caps + extrusion edges drawn
    print("PASS  iso box accepted + drawn")


def test_iso_l_shape():
    out = iso_projection.compute_isometric(_GEO_LSHAPE, (0, 0, 400, 360))
    assert out is not None and "ISOMETRIC VIEW" in out
    print("PASS  iso L-shape accepted")


def test_iso_gate_rejects_non_prism():
    out = iso_projection.compute_isometric(_GEO_NON_PRISM, (0, 0, 400, 360))
    assert out is None
    print("PASS  iso gate rejects non-prismatic part")


# ----------------------------------------------------------------------------
# sheet_compose unit tests
# ----------------------------------------------------------------------------

def test_compose_sheet():
    svg = sheet_compose.compose_sheet(blueprint.SAMPLE_CONTENT_SVG,
                                      {"description": "test bracket", "unit": "mm"})
    assert svg.startswith("<svg")
    assert "PART: TEST BRACKET" in svg           # title block, uppercased
    assert "DRAWN BY: BLUEPRINT AI" in svg
    assert "SHEET 1/1" in svg
    assert "SCALE" in svg
    assert ">F<" in svg and ">8<" in svg         # zone grid
    assert 'id="model-content"' in svg           # content group present...
    assert 'clip-path="url(#clip-content)"' in svg   # ...and hard-clipped
    # Regression: the clip rect lives in the group's LOCAL (post-translate)
    # coordinates — a 60,60 rect here would wrongly clip the content's edges.
    assert '<clipPath id="clip-content"><rect x="0" y="0" width="1080" height="840"/></clipPath>' in svg
    assert "ISOMETRIC VIEW" in svg               # computed iso
    assert "DETAIL A (2:1)" in svg               # detail spec consumed
    assert svg.count('id="geometry"') == 1       # detail clone dropped its ids

    # Missing description -> UNTITLED PART; iso falls back on a curvy part.
    svg2 = sheet_compose.compose_sheet(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 840"></svg>',
        {"description": None, "unit": "in"})
    assert "UNTITLED PART" in svg2
    assert "PICTORIAL VIEW OMITTED" in svg2      # no geometry group at all

    # Malformed detail specs are skipped silently, sheet still composes.
    bad = blueprint.SAMPLE_CONTENT_SVG.replace(
        '[{"view": "front", "cx": 590, "cy": 490, "r": 50, "scale": 2, "label": "A"}]',
        "not json at all")
    svg3 = sheet_compose.compose_sheet(bad, {"description": "x", "unit": "mm"})
    assert "DETAIL" not in svg3.replace("DETAIL A", "") or "DETAIL A" not in svg3
    print("PASS  compose_sheet (furniture + clip + iso + detail)")


def test_extract_content_fragment():
    composed = sheet_compose.compose_sheet(blueprint.SAMPLE_CONTENT_SVG,
                                           blueprint._stub_meta())
    frag = sheet_compose.extract_content_fragment(composed)
    assert frag is not None
    assert frag.startswith("<svg")
    assert 'viewBox="0 0 1080 840"' in frag
    assert 'id="geometry"' in frag               # the model's authored geometry
    assert "DRAWN BY: BLUEPRINT AI" not in frag  # furniture stays out
    # Deterministic: same sheet -> same fragment (prompt-cache stability).
    assert frag == sheet_compose.extract_content_fragment(composed)
    # A pre-v3 sheet (no model-content group) extracts to None.
    assert sheet_compose.extract_content_fragment(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>') is None
    print("PASS  extract_content_fragment round-trip")


def test_export_dxf():
    import ezdxf
    client = authed_client()

    # The composed sheet still carries the geometry group -> DXF must work.
    resp = client.post("/api/export-dxf", json={"svg": composed_stub_svg()})
    assert resp.status_code == 200, resp.status_code
    assert resp.mimetype == "application/dxf"
    doc = ezdxf.read(io.StringIO(resp.get_data(as_text=True)))
    assert doc.units == 4, doc.units  # 4 == millimetres
    msp = doc.modelspace()
    assert len(list(msp)) > 0, "empty modelspace"
    assert "FRONT" in doc.layers

    # Geometry-free SVG -> 422 no_geometry
    resp = client.post("/api/export-dxf", json={
        "svg": '<svg xmlns="http://www.w3.org/2000/svg"><rect x="0" y="0" width="5" height="5"/></svg>'})
    assert resp.status_code == 422, resp.status_code
    assert resp.get_json()["error"] == "no_geometry"
    print("PASS  export-dxf round-trip on composed sheet + no_geometry 422")


def test_review_rasterizer():
    """Render the composed stub sheet to PNG via resvg + bundled font.
    Skipped with a reason if resvg-py is not installed locally."""
    try:
        import resvg_py  # noqa: F401
    except ImportError:
        print("SKIP  review rasterizer (resvg-py not installed)")
        return
    assert os.path.exists(os.path.join(HERE, "static", "fonts", "DejaVuSans.ttf")), \
        "bundled font missing — review renders would have no text"
    png = blueprint.render_svg_to_png(composed_stub_svg())
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert len(png) > 5000, "suspiciously small render"
    print("PASS  review rasterizer (resvg + bundled DejaVu Sans)")


def test_marker_page():
    client = authed_client()
    resp = client.get("/marker")
    assert resp.status_code == 200, resp.status_code
    assert b"aruco-0.png" in resp.data
    print("PASS  marker page authed 200")


def test_frontend_optional():
    # Only checked when the frontend has shipped these files.
    client = authed_client()
    sw = os.path.join(HERE, "static", "js", "sw.js")
    if os.path.exists(sw):
        resp = client.get("/sw.js")
        assert resp.status_code == 200, resp.status_code
        assert resp.mimetype == "application/javascript"
        print("PASS  /sw.js served")
    else:
        print("SKIP  /sw.js (frontend file not present yet)")

    manifest = os.path.join(HERE, "static", "manifest.webmanifest")
    if os.path.exists(manifest):
        resp = client.get("/static/manifest.webmanifest")
        assert resp.status_code == 200
        print("PASS  manifest served")
    else:
        print("SKIP  manifest (frontend file not present yet)")


def main():
    tests = [
        test_healthz_no_auth,
        test_auth_gate,
        test_login,
        test_generate_validation,
        test_multi_dimension_validation,
        test_generate_stub_stream,
        test_sanitize_svg,
        test_detect_scale_stub,
        test_refine_validation,
        test_refine_stub_stream,
        test_review_validation,
        test_review_stub_stream,
        test_parse_review_reply,
        test_iso_vertex_math,
        test_iso_box,
        test_iso_l_shape,
        test_iso_gate_rejects_non_prism,
        test_compose_sheet,
        test_extract_content_fragment,
        test_export_dxf,
        test_review_rasterizer,
        test_marker_page,
        test_frontend_optional,
    ]
    for t in tests:
        t()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
