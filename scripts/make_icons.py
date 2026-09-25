"""Draw the app icon and write it in every format the packages need.

    uv run python scripts/make_icons.py

A ticket on benchhud's teal: intake.png (1024), intake.icns (macOS), intake.ico
(Windows) and intake-<size>.png (Linux), all in packaging/icons/. Pillow is
already a dependency (photo uploads), so this adds nothing.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "packaging" / "icons"
TEAL = (79, 151, 168, 255)  # benchhud's border colour, so the apps read as a family
INK = (244, 251, 252, 255)


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.rounded_rectangle((64 * s, 64 * s, 960 * s, 960 * s), radius=200 * s, fill=TEAL)
    # The ticket: an outline with two lines of "writing".
    w = round(44 * s)
    d.rounded_rectangle((232 * s, 272 * s, 792 * s, 752 * s), radius=56 * s, outline=INK, width=w)
    d.line((336 * s, 432 * s, 688 * s, 432 * s), fill=INK, width=w)
    d.line((336 * s, 592 * s, 584 * s, 592 * s), fill=INK, width=w)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.save(OUT / "intake.png")
    big.save(OUT / "intake.icns")
    big.save(OUT / "intake.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    for s in (16, 32, 64, 128, 256, 512):
        big.resize((s, s), Image.LANCZOS).save(OUT / f"intake-{s}.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
