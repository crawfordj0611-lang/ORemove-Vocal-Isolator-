"""
Draws the ORemove app icon (the interface's logo: waveform bars on a
green-to-blue rounded square) and writes it in the formats the builds need.

    python packaging/make_icon.py

Writes packaging/icon.png (1024 px), packaging/icon.ico (Windows) and, on
macOS, packaging/icon.icns.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
SIZE = 1024
GREEN, BLUE, DARK = (48, 209, 88), (100, 210, 255), (0, 42, 16)


def draw() -> Image.Image:
    # Diagonal green-to-blue gradient.
    gradient = Image.new("RGB", (SIZE, SIZE))
    px = gradient.load()
    for y in range(SIZE):
        for x in range(SIZE):
            t = (x + y) / (2 * (SIZE - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(GREEN, BLUE))

    # Rounded square with the macOS icon proportions (a margin around it).
    margin, radius = 100, 185
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, SIZE - margin, SIZE - margin), radius, fill=255)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    # Five waveform bars, like the logo in the interface (heights 4,12,18,10,4 of 24).
    draw = ImageDraw.Draw(icon)
    heights = [4, 12, 18, 10, 4]
    unit = (SIZE - 2 * margin) / 24
    bar = unit * 2.6
    cx, cy = SIZE / 2, SIZE / 2
    for i, h in enumerate(heights):
        x = cx + (i - 2) * unit * 4
        half = max(h, 2.6) * unit / 2
        draw.rounded_rectangle((x - bar / 2, cy - half, x + bar / 2, cy + half), bar / 2, fill=DARK)
    return icon


def main():
    icon = draw()
    icon.save(OUT / "icon.png")
    icon.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin":
        icon.save(OUT / "icon.icns")
    print("Icon written to", OUT)


if __name__ == "__main__":
    main()
