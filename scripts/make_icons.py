"""Erzeugt die App-Icons (PWA) als PNG.

Wird nur bei Bedarf ausgeführt, die Bilder liegen im Repository:

    uv run python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "oenergy" / "static" / "icons"

BG_TOP = (11, 15, 20)
BG_BOTTOM = (18, 38, 32)
BODY = (63, 185, 80)
BODY_DARK = (45, 160, 62)
EYE = (245, 248, 250)
PUPIL = (11, 15, 20)


def background(size: int) -> Image.Image:
    """Verlauf als Hintergrund."""
    image = Image.new("RGB", (size, size), BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        ratio = y / max(size - 1, 1)
        colour = tuple(int(top + (bottom - top) * ratio) for top, bottom in zip(BG_TOP, BG_BOTTOM))
        draw.line([(0, y), (size, y)], fill=colour)
    return image


def octopus(image: Image.Image, scale: float = 1.0, offset: float = 0.0) -> None:
    """Zeichnet einen Kraken in die Mitte."""
    draw = ImageDraw.Draw(image)
    s = image.width
    cx = s / 2
    cy = s / 2 + s * 0.06 * offset

    head_r = s * 0.20 * scale
    body_top = cy - head_r * 1.05
    body_bottom = cy + head_r * 1.25

    # Tentakel
    for index, offset_x in enumerate((-0.72, -0.42, -0.12, 0.18, 0.48, 0.76)):
        x = cx + head_r * offset_x
        width = head_r * 0.34
        top = cy + head_r * 0.35
        length = head_r * (1.25 - abs(offset_x) * 0.35)
        draw.ellipse(
            [x - width, top, x + width, top + length * 2],
            fill=BODY_DARK if index % 2 else BODY,
        )

    # Kopf
    draw.ellipse([cx - head_r, body_top, cx + head_r, body_top + head_r * 2.1], fill=BODY)

    # Augen
    eye_r = head_r * 0.30
    pupil_r = head_r * 0.14
    for direction in (-1, 1):
        ex = cx + direction * head_r * 0.42
        ey = body_top + head_r * 0.85
        draw.ellipse([ex - eye_r, ey - eye_r, ex + eye_r, ey + eye_r], fill=EYE)
        draw.ellipse([ex - pupil_r, ey - pupil_r, ex + pupil_r, ey + pupil_r], fill=PUPIL)


def make_icon(size: int, maskable: bool = False) -> Image.Image:
    image = background(size)
    # bei maskable Icons muss das Motiv in der Mitte bleiben (safe zone)
    octopus(image, scale=0.86 if maskable else 1.0)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        make_icon(size).save(OUT / f"icon-{size}.png", optimize=True)
    make_icon(512, maskable=True).save(OUT / "icon-maskable-512.png", optimize=True)
    make_icon(180).save(OUT / "apple-touch-icon.png", optimize=True)
    print(f"Icons geschrieben nach {OUT}")


if __name__ == "__main__":
    main()
