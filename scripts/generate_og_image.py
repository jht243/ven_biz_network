"""
Generate the social-share Open Graph image (1200x630 PNG) used by all
report pages. Run once at build time; output goes to static/og-image.png
and is served via the Flask /static/* route.

Re-run anytime the brand wording changes:
    python scripts/generate_og_image.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT = Path(__file__).resolve().parent.parent / "static" / "og-image.png"
WIDTH, HEIGHT = 1200, 630

BG_TOP = (0, 31, 68)
BG_BOTTOM = (0, 43, 94)
ACCENT = (204, 0, 0)
WHITE = (255, 255, 255)
MUTED = (200, 210, 225)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        [
            "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _gradient_background() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    pixels = img.load()
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * ratio)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * ratio)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * ratio)
        for x in range(WIDTH):
            pixels[x, y] = (r, g, b)
    return img


def main() -> None:
    img = _gradient_background()
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (WIDTH, 12)], fill=ACCENT)

    eyebrow = _font(28, bold=True)
    title = _font(78, bold=True)
    subtitle = _font(36)
    footer = _font(26)

    draw.text((80, 100), "CARACAS RESEARCH", font=eyebrow, fill=ACCENT)

    draw.text((80, 170), "Venezuelan Research", font=title, fill=WHITE)
    draw.text((80, 270), "for International Investors", font=title, fill=WHITE)

    draw.text(
        (80, 410),
        "OFAC tracker · Asamblea Nacional · BCV rates · sector analysis",
        font=subtitle,
        fill=MUTED,
    )

    draw.line([(80, HEIGHT - 90), (WIDTH - 80, HEIGHT - 90)], fill=ACCENT, width=2)
    draw.text(
        (80, HEIGHT - 70),
        "caracasresearch.com",
        font=footer,
        fill=MUTED,
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT, format="PNG", optimize=True)
    print(f"OG image written to {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
