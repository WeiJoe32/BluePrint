"""
Dev script: generate the printable ArUco scale marker (id 0) as a PNG.

Run once to (re)create static/img/aruco-0.png. The saved image is the marker with
a white "quiet zone" border padded around it — ArUco detection needs that white
margin to find the marker reliably.

    python scripts/make_marker.py
"""

import os

import cv2
import numpy as np

# scripts/ lives one level under the project root; write into static/img there.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "static", "img")
OUT_PATH = os.path.join(OUT_DIR, "aruco-0.png")

MARKER_PX = 600   # rendered marker size
PADDED_PX = 740   # final image size (marker + white quiet-zone border)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
    marker = cv2.aruco.generateImageMarker(dictionary, 0, MARKER_PX)

    # Paste the marker onto a white canvas so it has a quiet-zone border.
    canvas = np.full((PADDED_PX, PADDED_PX), 255, dtype=np.uint8)
    offset = (PADDED_PX - MARKER_PX) // 2
    canvas[offset:offset + MARKER_PX, offset:offset + MARKER_PX] = marker

    cv2.imwrite(OUT_PATH, canvas)
    print(f"Wrote {OUT_PATH} ({PADDED_PX}x{PADDED_PX}px, marker {MARKER_PX}px)")


if __name__ == "__main__":
    main()
