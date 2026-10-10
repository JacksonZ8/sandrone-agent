"""生成 PWA 图标：酒红底色 + 奶白蝴蝶结（用户选定的 A 方案）。

scripts/assets/icon-bow.png 是从 AI 生成的图标方案中抠出的蝴蝶结（Real-ESRGAN x4plus-anime 放大后
按与底色的色差做软遮罩），透明底。这里把它居中铺到满版底色上；iOS 和 Android 会自己裁圆角。

用法：backend/.venv/bin/python scripts/make_pwa_icons.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BOW = ROOT / "scripts/assets/icon-bow.png"
OUT = ROOT / "frontend/public/icons"

BACKGROUND = (55, 23, 25)  # #371719，与方案图底色一致


def render(size: int, *, maskable: bool) -> Image.Image:
    bow = Image.open(BOW).convert("RGBA")
    # maskable 图标的安全区是中间 80% 的圆，蝴蝶结的对角线要落在圆内
    width = round(size * (0.6 if maskable else 0.76))
    height = round(bow.height * width / bow.width)
    bow = bow.resize((width, height), Image.LANCZOS)
    image = Image.new("RGBA", (size, size), BACKGROUND + (255,))
    image.alpha_composite(bow, ((size - width) // 2, (size - height) // 2))
    return image.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(192, maskable=False).save(OUT / "icon-192.png", optimize=True)
    render(512, maskable=False).save(OUT / "icon-512.png", optimize=True)
    render(512, maskable=True).save(OUT / "icon-maskable-512.png", optimize=True)
    render(180, maskable=False).save(OUT / "apple-touch-icon.png", optimize=True)
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
