"""
ArUco scale-marker detection for BluePrint.

The user prints an ArUco marker (see /marker), lays it flat beside the part, and
photographs it. This module finds that marker in a photo, works out how many
pixels represent one millimetre, and draws the measurement back onto the image so
Claude can read the calibration. Feeding Claude a *measured* scale turns "guess
the size by eye" into simple arithmetic — the big accuracy win in the roadmap.

Public function: detect_and_annotate(jpeg_bytes) -> dict.
"""

import base64

import config


def detect_and_annotate(jpeg_bytes: bytes) -> dict:
    """Find the first ArUco marker in a JPEG and measure its scale.

    Returns {"found": False} when no marker is visible. Otherwise returns:
      found            True
      px_per_mm        pixels per millimetre (rounded to 2 dp)
      marker_size_mm   the printed marker size in mm (config.MARKER_SIZE_MM)
      annotated_image  the photo with the marker outlined + a scale bar, base64
      angle_warning    True if the marker looks skewed (photo not square-on)
    """
    # cv2/numpy are heavy and only needed here, so import them lazily.
    import cv2
    import numpy as np

    # Decode the JPEG bytes into an OpenCV BGR image.
    buffer = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        return {"found": False}

    # Detect markers using the DICT_5X5_50 dictionary (cv2 >= 4.7 API).
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(image)
    if ids is None or len(corners) == 0:
        return {"found": False}

    # Use the first marker's four corners (shape: 1 x 4 x 2).
    pts = corners[0].reshape(4, 2)
    # Side lengths between consecutive corners (they wrap around the square).
    sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
    mean_px = sum(sides) / 4.0
    px_per_mm = mean_px / config.MARKER_SIZE_MM

    # A square marker photographed at an angle has uneven sides. If the longest
    # side is more than 15% longer than the shortest, warn the user.
    angle_warning = bool(max(sides) / min(sides) > 1.15) if min(sides) > 0 else True

    annotated = _annotate(cv2, np, image, pts, mean_px, px_per_mm)

    # Re-encode as JPEG (quality 85) and base64 it for the browser.
    ok, out = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    annotated_b64 = base64.b64encode(out.tobytes()).decode("ascii") if ok else ""

    return {
        "found": True,
        "px_per_mm": round(px_per_mm, 2),
        "marker_size_mm": 50,
        "annotated_image": annotated_b64,
        "angle_warning": angle_warning,
    }


def _annotate(cv2, np, image, pts, mean_px, px_per_mm):
    """Draw the marker outline, a readable label, and a 50 mm scale bar."""
    img = image.copy()
    green = (0, 200, 0)

    # 1) Green outline around the detected marker.
    poly = pts.astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(img, [poly], isClosed=True, color=green, thickness=3)

    # 2) Label with a dark background rectangle so it's readable on any photo.
    label = f"SCALE REF: 50mm = {mean_px:.0f}px ({px_per_mm:.2f} px/mm)"
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.7
    thickness = 2
    (tw, th), baseline = cv2.getTextSize(label, font, scale, thickness)

    # Place the label just above the marker's top-left corner, kept on-screen.
    x = int(min(pts[:, 0]))
    y = int(min(pts[:, 1])) - 12
    x = max(5, min(x, img.shape[1] - tw - 10))
    y = max(th + 12, y)
    cv2.rectangle(img, (x - 5, y - th - baseline - 5), (x + tw + 5, y + 5),
                  (0, 0, 0), thickness=-1)
    cv2.putText(img, label, (x, y), font, scale, green, thickness, cv2.LINE_AA)

    # 3) A horizontal green bar exactly 50 mm long near the bottom of the image,
    #    with a "50 mm" caption under it.
    bar_px = int(round(50 * px_per_mm))
    h, w = img.shape[:2]
    bx = 30
    by = h - 40
    bx2 = min(bx + bar_px, w - 30)  # keep the bar on-screen
    cv2.line(img, (bx, by), (bx2, by), green, thickness=4)
    cv2.line(img, (bx, by - 8), (bx, by + 8), green, thickness=4)
    cv2.line(img, (bx2, by - 8), (bx2, by + 8), green, thickness=4)
    cv2.putText(img, "50 mm", (bx, by + 28), font, 0.7, green, 2, cv2.LINE_AA)

    return img
