"""Derive web brand assets from the supplied logo without modifying it.

Source: ../Elements/Logo.png (icon-only mark on a cream margin; supplied by the owner).
Output: static/brand/mark-{size}.png, RGBA, red rounded tile with a transparent outside.

The ant's cream body touches the tile edge, so the margin cannot be flood-filled away.
Instead, the red tile's bounding box and corner radius are measured and a rounded-rectangle
alpha mask is applied (drawn at 4x and downsampled for anti-aliasing).

Vector exports supplied later in Elements/ are reused, not redrawn:
  wordmark_dark.svg        -> static/brand/wordmark-ink.svg + wordmark-ai.svg (same geometry, split by
                              colour: the ink layer is a CSS mask painted in the theme text colour, the
                              red "AI" layer keeps the supplied #D93829)
  favicon_ant_face_{32,64}.png -> static/brand/favicon-{32,64}.png (simplified face for <= 64 px use)

Run from ProjectWatchAI/:  python scripts/derive_brand_assets.py
"""
from pathlib import Path

import re
import shutil

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent
ELEMENTS = HERE.parent / "Elements"
SOURCE = ELEMENTS / "Logo.png"
OUTPUT = HERE / "static" / "brand"
SIZES = (32, 64, 128, 256, 512)


def is_brand_red(pixel):
    r, g, b = pixel[:3]
    return r > 150 and g < 90 and b < 80


def red_bounds(image):
    width, height = image.size
    pixels = image.load()
    xs, ys = [], []
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            if is_brand_red(pixels[x, y]):
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def corner_radius(image, box):
    """Distance from the tile's left edge to the first red pixel on its top row."""
    left, top, right, _ = box
    pixels = image.load()
    row = top + 1
    first = next(x for x in range(left, right) if is_brand_red(pixels[x, row]))
    return first - left


def main():
    source = Image.open(SOURCE).convert("RGB")
    box = red_bounds(source)
    radius = corner_radius(source, box)
    # Inset past the source's anti-aliased cream edge so no light rim shows on dark backgrounds.
    inset = 8
    box = (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset)
    radius -= inset // 2
    tile = source.crop(box)
    side = max(tile.size)
    square = Image.new("RGB", (side, side), (201, 41, 27))  # #C9291B, owner-confirmed brand red
    square.paste(tile, ((side - tile.width) // 2, (side - tile.height) // 2))
    scale = 4
    mask = Image.new("L", (side * scale, side * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side * scale - 1, side * scale - 1), radius=radius * scale, fill=255)
    mask = mask.resize((side, side), Image.LANCZOS)
    mark = square.convert("RGBA")
    mark.putalpha(mask)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        mark.resize((size, size), Image.LANCZOS).save(OUTPUT / f"mark-{size}.png", optimize=True)
    print(f"tile box={box} side={side} radius={radius}px -> {', '.join(f'mark-{s}.png' for s in SIZES)}")
    derive_vector_assets()


def derive_vector_assets():
    """Theme-following wordmark and the supplied small favicon. Originals are only read."""
    svg = (ELEMENTS / "wordmark_dark.svg").read_text(encoding="utf-8")
    ink = "#1E1E1E"
    assert svg.count(f'fill="{ink}"') == 12 and svg.count('fill="#D93829"') == 2, "unexpected wordmark source"
    svg = re.sub(r' width="[0-9]+" height="[0-9]+"', "", svg, count=1)  # size comes from CSS
    svg = re.sub(r"<title>.*?</title>\s*<desc>.*?</desc>", "", svg, flags=re.S)
    svg = svg.replace(' role="img" aria-label="BantAIProyekto wordmark"', ' aria-hidden="true"', 1)
    head, body = svg.split("<g ", 1)
    group_open, paths = body.split(">", 1)
    paths = paths.rsplit("</g>", 1)[0]
    path_list = re.findall(r"<path .*?/>", paths, flags=re.S)
    ink_paths = [p for p in path_list if f'fill="{ink}"' in p]
    red_paths = [p for p in path_list if 'fill="#D93829"' in p]

    def layer(selected):
        return f"{head}<g {group_open}>\n{''.join(selected)}\n</g>\n</svg>\n"

    # st.html strips inline SVG, so the wordmark is two same-geometry files stacked in CSS: the ink layer is
    # used as a mask over currentColor (follows the theme); the AI layer is an <img> in the supplied red.
    (OUTPUT / "wordmark-ink.svg").write_text(layer(ink_paths), encoding="utf-8")
    (OUTPUT / "wordmark-ai.svg").write_text(layer(red_paths), encoding="utf-8")
    for size in (32, 64):
        shutil.copyfile(ELEMENTS / f"favicon_ant_face_{size}.png", OUTPUT / f"favicon-{size}.png")
    print("wordmark-ink.svg, wordmark-ai.svg, favicon-32.png, favicon-64.png")


if __name__ == "__main__":
    main()
