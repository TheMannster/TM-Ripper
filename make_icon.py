"""Convert assets/icon.png into a multi-resolution assets/icon.ico for the .exe."""

import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PNG = os.path.join(HERE, "assets", "icon.png")
ICO = os.path.join(HERE, "assets", "icon.ico")


def main():
    if not os.path.exists(PNG):
        raise SystemExit(f"Missing {PNG}")
    img = Image.open(PNG).convert("RGBA")
    # Square-crop to be safe.
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ICO, format="ICO", sizes=sizes)
    print(f"Wrote {ICO}")


if __name__ == "__main__":
    main()
