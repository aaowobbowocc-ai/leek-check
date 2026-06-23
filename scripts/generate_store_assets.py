"""生成 Play Store / App Store 行銷圖 + Capacitor splash 圖。

跑法:
    PYTHONIOENCODING=utf-8 python scripts/generate_store_assets.py

輸出:
    mobile/store-assets/play-feature-graphic-1024x500.png  (Play 必填)
    mobile/android/app/src/main/res/drawable/splash.png    (Capacitor splash)
    mobile/android/app/src/main/res/drawable/ic_launcher_background.xml  (adaptive icon bg)
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "mobile" / "store-assets"
ANDROID_DRAWABLE = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "drawable"
ANDROID_VALUES = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "values"
FONT_PATH = "C:/Windows/Fonts/msjh.ttc"


def gradient_bg(size, top_color=(15, 118, 110), bottom_color=(10, 26, 31)):
    """畫漸層背景。size = (w, h)"""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / h
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return img


def make_play_feature_graphic():
    """Play Store feature graphic — 1024×500 橫式,必填。"""
    W, H = 1024, 500
    img = gradient_bg((W, H), top_color=(15, 118, 110), bottom_color=(22, 24, 29))
    draw = ImageDraw.Draw(img)

    # 主標題
    title_font = ImageFont.truetype(FONT_PATH, 90)
    title = "韭菜健檢"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    title_x = 80
    title_y = (H - th) // 2 - 40
    draw.text((title_x, title_y), title, fill=(255, 255, 255), font=title_font)

    # 副標題
    sub_font = ImageFont.truetype(FONT_PATH, 36)
    subtitle = "買進前先做一次韭菜健檢"
    draw.text((title_x, title_y + th + 16), subtitle,
                fill=(94, 234, 212), font=sub_font)

    # Tagline
    tag_font = ImageFont.truetype(FONT_PATH, 22)
    tagline = "4 面分析 · 不報明牌 · 純客觀數據"
    draw.text((title_x, title_y + th + 70), tagline,
                fill=(148, 163, 184), font=tag_font)

    # 右側 emoji icon
    icon_font_size = 240
    try:
        # 嘗試 emoji font(Windows 不一定有)
        emoji_font = ImageFont.truetype("seguiemj.ttf", icon_font_size)
    except Exception:
        emoji_font = ImageFont.truetype(FONT_PATH, icon_font_size)
    emoji = "🩺"
    bbox_e = draw.textbbox((0, 0), emoji, font=emoji_font)
    ew = bbox_e[2] - bbox_e[0]
    ex = W - ew - 80
    ey = (H - icon_font_size) // 2 - 20
    draw.text((ex, ey), emoji, fill=(94, 234, 212), font=emoji_font)

    # LEEK CHECK label 底部
    label_font = ImageFont.truetype(FONT_PATH, 18)
    label = "LEEK CHECK · v0.1"
    draw.text((title_x, H - 50), label,
                fill=(94, 234, 212), font=label_font)

    STORE_DIR.mkdir(parents=True, exist_ok=True)
    out = STORE_DIR / "play-feature-graphic-1024x500.png"
    img.convert("RGB").save(out, "PNG")
    print(f"✓ Play feature graphic: {out}")


def make_splash():
    """Capacitor splash 圖 — 2732×2732 中心 logo + 漸層背景。"""
    SIZE = 2732
    img = gradient_bg((SIZE, SIZE), top_color=(15, 118, 110), bottom_color=(10, 26, 31))
    draw = ImageDraw.Draw(img)

    # 中心圓形 logo 卡片
    card_size = 800
    card_x = (SIZE - card_size) // 2
    card_y = (SIZE - card_size) // 2 - 80
    # 圓角矩形
    radius = 120
    overlay = Image.new("RGBA", (card_size, card_size), (0, 0, 0, 0))
    ovd = ImageDraw.Draw(overlay)
    ovd.rounded_rectangle(
        [(0, 0), (card_size, card_size)],
        radius=radius,
        fill=(15, 118, 110, 220),
    )
    img.alpha_composite(overlay, (card_x, card_y))

    # 韭菜主字
    title_font = ImageFont.truetype(FONT_PATH, 280)
    text = "韭菜"
    bbox = draw.textbbox((0, 0), text, font=title_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (SIZE - tw) // 2
    ty = card_y + (card_size - th) // 2 - 60
    draw.text((tx, ty), text, fill=(255, 255, 255), font=title_font)

    # LEEK CHECK 副字
    sub_font = ImageFont.truetype(FONT_PATH, 80)
    sub = "LEEK CHECK"
    bbox2 = draw.textbbox((0, 0), sub, font=sub_font)
    sw = bbox2[2] - bbox2[0]
    sx = (SIZE - sw) // 2
    sy = ty + th + 40
    draw.text((sx, sy), sub, fill=(94, 234, 212), font=sub_font)

    # 底部 tagline
    tag_font = ImageFont.truetype(FONT_PATH, 60)
    tag = "買進前先做一次韭菜健檢"
    bbox3 = draw.textbbox((0, 0), tag, font=tag_font)
    tagw = bbox3[2] - bbox3[0]
    tagx = (SIZE - tagw) // 2
    tagy = card_y + card_size + 120
    draw.text((tagx, tagy), tag, fill=(94, 234, 212), font=tag_font)

    ANDROID_DRAWABLE.mkdir(parents=True, exist_ok=True)
    out = ANDROID_DRAWABLE / "splash.png"
    img.convert("RGB").save(out, "PNG")
    print(f"✓ Android splash: {out}")


def make_adaptive_icon_background():
    """Android adaptive icon background — solid color XML 跟 PNG 兩種都生。"""
    ANDROID_VALUES.mkdir(parents=True, exist_ok=True)
    out_xml = ANDROID_VALUES / "ic_launcher_background.xml"
    out_xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        '    <color name="ic_launcher_background">#0f766e</color>\n'
        '</resources>\n',
        encoding="utf-8",
    )
    print(f"✓ Adaptive icon bg color: {out_xml}")


if __name__ == "__main__":
    make_play_feature_graphic()
    make_splash()
    make_adaptive_icon_background()
    print("\n✅ Store assets 全部生成完成")
