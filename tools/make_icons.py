"""Draws the app icons in app/static: a white house on terracotta. Run again after changing the colours.

    python tools/make_icons.py   (needs Pillow, not an app dependency)
"""

from pathlib import Path

from PIL import Image, ImageDraw

ACC = (196, 87, 46)
OUT = Path(__file__).parent.parent / "app" / "static"


def draw(size: int, maskable: bool, square: bool = False) -> Image.Image:
    """square: full-bleed background, for iOS, which rounds the corners itself (and fills transparency with black)."""
    s = 4 * size  # draw large, then shrink: smooth edges
    img = Image.new("RGBA", (s, s), ACC if maskable or square else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if not (maskable or square):
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.22, fill=ACC)
    # Maskable icons get cropped to a circle by some launchers: keep the house inside the middle 60%.
    k = 0.6 if maskable else 0.78
    u = s * k
    ox, oy = (s - u) / 2, (s - u) / 2
    p = lambda x, y: (ox + x * u, oy + y * u)
    white = (255, 255, 255, 255)
    d.polygon([p(.5, .08), p(.95, .46), p(.86, .46), p(.86, .92), p(.14, .92), p(.14, .46), p(.05, .46)], fill=white)
    d.rounded_rectangle((*p(.41, .6), *p(.59, .92)), radius=u * 0.03, fill=ACC)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for name, size, maskable, square in [("icon-180.png", 180, False, True), ("icon-192.png", 192, False, False),
                                         ("icon-512.png", 512, False, False), ("icon-maskable-512.png", 512, True, False)]:
        draw(size, maskable, square).save(OUT / name, optimize=True)
        print(OUT / name)
