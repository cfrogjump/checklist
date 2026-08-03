#!/usr/bin/env python3
"""Regenerate the site icons.

Only needed if you want to change the icon — the server itself still has no
dependencies, it just serves the committed files. Requires Pillow:

    pip install Pillow
    python3 tools/make_icons.py

Writes favicon.svg, favicon-32.png and apple-touch-icon.png next to
index.html. The checkmark geometry lives in CHECK below and is shared by the
SVG and the PNGs so they can't drift apart.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent

BG = "#b0592f"      # --accent from index.html
MARK = "#faf7f2"    # --paper from index.html

# Checkmark as fractions of the canvas, so it scales to any size. Sized to
# stay legible at the ~60px iOS renders a home screen icon at, while leaving
# room for the squircle mask to cut the corners.
CHECK = [(0.23, 0.52), (0.42, 0.70), (0.78, 0.31)]
STROKE = 0.12
# iOS masks the home screen icon to a squircle itself, so apple-touch-icon
# stays a full-bleed square; only the tab favicon gets rounded corners.
TAB_RADIUS = 0.20


def draw_png(size, radius, path, supersample=4):
    px = size * supersample
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if radius:
        d.rounded_rectangle([0, 0, px - 1, px - 1], radius=radius * px, fill=BG)
    else:
        d.rectangle([0, 0, px, px], fill=BG)

    pts = [(x * px, y * px) for x, y in CHECK]
    width = int(STROKE * px)
    d.line(pts, fill=MARK, width=width, joint="curve")
    # Round off the two open ends; joint="curve" only rounds the elbow.
    for x, y in (pts[0], pts[-1]):
        r = width / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=MARK)

    out = img.resize((size, size), Image.LANCZOS)
    if not radius:
        # Drop the alpha channel entirely for the iOS icon — it ignores
        # transparency and composites on black, so don't hand it any.
        out = out.convert("RGB")
    out.save(path)
    print(f"wrote {path.name} ({size}x{size})")


def write_svg(path, size=180):
    pts = " ".join(f"{'M' if i == 0 else 'L'}{x * size:.0f} {y * size:.0f}"
                   for i, (x, y) in enumerate(CHECK))
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">\n'
        f'  <rect width="{size}" height="{size}" rx="{TAB_RADIUS * size:.0f}" fill="{BG}"/>\n'
        f'  <path d="{pts}" fill="none" stroke="{MARK}" stroke-width="{STROKE * size:.0f}"\n'
        f'        stroke-linecap="round" stroke-linejoin="round"/>\n'
        f'</svg>\n'
    )
    print(f"wrote {path.name}")


if __name__ == "__main__":
    draw_png(180, 0, OUT / "apple-touch-icon.png")
    draw_png(32, TAB_RADIUS, OUT / "favicon-32.png")
    write_svg(OUT / "favicon.svg")
