"""
Generate BluePrint's PWA icons (192, 512, apple-touch-180) from the existing
favicon design (see templates/index.html's inline data: URI), scaled up.

Dev-only tool — needs Pillow (`pip install pillow`), which is NOT added to
requirements.txt since the running app never imports it.

Run:  python scripts/make_icons.py
"""

import os

from PIL import Image, ImageDraw

# Same palette as static/css/style.css --paper / --line / --accent.
BG = (12, 36, 64, 255)          # #0c2440
BORDER = (134, 184, 216, 255)   # #86b8d8
ACCENT = (255, 209, 102, 255)   # #ffd166

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "img"))

# {output filename: (pixel size, description)}
SIZES = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "apple-touch-icon.png": 180,
}


def draw_icon(size):
    """Draw the favicon's 16x16 design (deep-blue square, thin border, amber
    folded-sheet outline) scaled up to `size` pixels."""
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)
    scale = size / 16.0

    def pt(x, y):
        return (x * scale, y * scale)

    # Thin inner border rect, matching the favicon's <rect x="1.5" y="1.5" .../>.
    border_w = max(1, round(0.6 * scale))
    inset = 1.5 * scale
    draw.rectangle([inset, inset, size - inset, size - inset], outline=BORDER, width=border_w)

    # Folded-sheet outline (a page with its top-right corner folded down),
    # approximating the favicon's two amber <path> elements as polylines.
    accent_w = max(2, round(1.2 * scale))
    sheet = [pt(3.5, 13), pt(3.5, 3), pt(9.5, 3), pt(13, 6.5), pt(13, 13), pt(3.5, 13)]
    draw.line(sheet, fill=ACCENT, width=accent_w, joint="curve")
    fold = [pt(9.5, 3), pt(9.5, 6.5), pt(13, 6.5)]
    draw.line(fold, fill=ACCENT, width=accent_w, joint="curve")

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for filename, size in SIZES.items():
        icon = draw_icon(size)
        path = os.path.join(OUT_DIR, filename)
        icon.save(path, "PNG")
        print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    main()
