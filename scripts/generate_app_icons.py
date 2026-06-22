"""生成所有 App icon 尺寸 — PWA / Android / iOS / App Store 需要的全部。

跑法:
    python scripts/generate_app_icons.py

輸出:
    app/static/icon-192.png, icon-512.png (PWA)
    mobile/android/app/src/main/res/mipmap-*/ic_launcher*.png (Android)
    mobile/ios/App/App/Assets.xcassets/AppIcon.appiconset/ (iOS,需 macOS 同步)
    mobile/app-store-icon-1024.png (Apple App Store submission)
    mobile/play-store-icon-512.png (Google Play feature graphic)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PWA_DIR = ROOT / "app" / "static"
ANDROID_RES = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res"
STORE_DIR = ROOT / "mobile" / "store-assets"

FONT_PATH = "C:/Windows/Fonts/msjh.ttc"

# Android adaptive icon densities
ANDROID_MIPMAP = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}

# iOS icon sizes (single 1024 for submission,Xcode 會自動生其他尺寸)
IOS_SIZES = [1024]


def make_icon(size: int, with_padding: bool = False) -> Image.Image:
    """畫 icon。with_padding=True 給 Android adaptive icon foreground 用(內縮 25%)。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 漸層背景 — teal → navy
    for y in range(size):
        ratio = y / size
        r = int(15 * (1 - ratio) + 10 * ratio)
        g = int(118 * (1 - ratio) + 26 * ratio)
        b = int(110 * (1 - ratio) + 31 * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

    # 圓角(if 不是 Android adaptive)
    if not with_padding:
        radius = size // 8
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (size, size)], radius=radius, fill=255,
        )
        img.putalpha(mask)

    # 內容區
    content_size = size if not with_padding else int(size * 0.7)
    offset = (size - content_size) // 2

    # 主字「韭菜」
    try:
        font = ImageFont.truetype(FONT_PATH, int(content_size * 0.34))
    except Exception:
        font = ImageFont.load_default()
    text = "韭菜"
    draw2 = ImageDraw.Draw(img)
    bbox = draw2.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2 - int(size * 0.06)
    draw2.text((tx, ty), text, fill="white", font=font)

    # 副字 LEEK CHECK
    try:
        font2 = ImageFont.truetype(FONT_PATH, int(content_size * 0.09))
    except Exception:
        font2 = ImageFont.load_default()
    sub = "LEEK CHECK"
    bbox2 = draw2.textbbox((0, 0), sub, font=font2)
    sw = bbox2[2] - bbox2[0]
    sx = (size - sw) // 2
    sy = ty + th + int(size * 0.05)
    draw2.text((sx, sy), sub, fill=(94, 234, 212, 255), font=font2)

    return img


def generate_all():
    # 1. PWA
    PWA_DIR.mkdir(parents=True, exist_ok=True)
    for s in [192, 512]:
        icon = make_icon(s).convert("RGB")
        out = PWA_DIR / f"icon-{s}.png"
        icon.save(out, "PNG")
        print(f"✓ PWA  {out}")

    # 2. Android(legacy + adaptive icon)
    if ANDROID_RES.exists():
        for dir_name, size in ANDROID_MIPMAP.items():
            mipmap_dir = ANDROID_RES / dir_name
            mipmap_dir.mkdir(parents=True, exist_ok=True)
            # Legacy(圓角)
            icon = make_icon(size).convert("RGBA")
            icon.save(mipmap_dir / "ic_launcher.png", "PNG")
            # Round 版(Android 7+)
            round_mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(round_mask).ellipse(
                [(0, 0), (size, size)], fill=255,
            )
            round_icon = make_icon(size).convert("RGBA")
            round_icon.putalpha(round_mask)
            round_icon.save(mipmap_dir / "ic_launcher_round.png", "PNG")
            # Adaptive icon foreground(內縮 25%,系統會加圓 mask)
            fg = make_icon(size, with_padding=True)
            fg.save(mipmap_dir / "ic_launcher_foreground.png", "PNG")
            print(f"✓ Android {dir_name} ({size}px)")
    else:
        print(f"⚠️ Android res dir 不存在,跳過:{ANDROID_RES}")

    # 3. Store assets
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    apple_icon = make_icon(1024).convert("RGB")
    apple_icon.save(STORE_DIR / "app-store-icon-1024.png", "PNG")
    print(f"✓ Apple {STORE_DIR / 'app-store-icon-1024.png'}")

    play_icon = make_icon(512).convert("RGB")
    play_icon.save(STORE_DIR / "play-store-icon-512.png", "PNG")
    print(f"✓ Play {STORE_DIR / 'play-store-icon-512.png'}")


if __name__ == "__main__":
    generate_all()
    print("\n✅ 全部 icon 生成完成")
