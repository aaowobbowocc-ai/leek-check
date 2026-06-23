"""生成 Play Store 用宣傳截圖(5 張,1080×1920 9:16 portrait).

跑法:
    PYTHONIOENCODING=utf-8 python scripts/generate_play_screenshots.py

輸出:
    mobile/store-assets/screenshot-phone-1.png  ~ screenshot-phone-5.png
    mobile/store-assets/screenshot-tablet7-1.png  ~ screenshot-tablet7-5.png  (1200×1920)
    mobile/store-assets/screenshot-tablet10-1.png ~ screenshot-tablet10-5.png (1600×2560)
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "mobile" / "store-assets"
FONT_PATH = "C:/Windows/Fonts/msjh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msjhbd.ttc"

W, H = 1080, 1920

# 漸層底色:深綠 → 黑
TOP = (15, 118, 110)
BOTTOM = (10, 26, 31)
TEAL_LIGHT = (94, 234, 212)
TEAL = (20, 184, 166)
ACCENT_RED = (239, 68, 68)
ACCENT_GREEN = (74, 222, 128)
ACCENT_AMBER = (245, 158, 11)


def gradient_bg(w, h, top=TOP, bottom=BOTTOM):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / h
        r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
        g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
        b = int(top[2] * (1 - ratio) + bottom[2] * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return img


def rounded_rect(img, xy, radius, fill, outline=None, width=1):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
    img.alpha_composite(overlay)


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_PATH, size)
    except Exception:
        return ImageFont.truetype(FONT_PATH, size)


def draw_text_centered(draw, text, y, font_obj, color=(255, 255, 255), width=W):
    bbox = draw.textbbox((0, 0), text, font=font_obj)
    tw = bbox[2] - bbox[0]
    x = (width - tw) // 2
    draw.text((x, y), text, fill=color, font=font_obj)
    return y + (bbox[3] - bbox[1])


def make_screenshot_1():
    """Hero: 韭菜健檢 主標題 + tagline"""
    img = gradient_bg(W, H)
    draw = ImageDraw.Draw(img)

    # Brand badge
    y = 200
    draw_text_centered(draw, "LEEK CHECK · v0.1", y, font(28),
                       color=TEAL_LIGHT)
    # Main emoji
    y = 280
    draw_text_centered(draw, "🩺", y, font(280))
    # Title
    y = 700
    draw_text_centered(draw, "韭菜健檢", y, font(130, bold=True))
    # Subtitle
    y = 880
    draw_text_centered(draw, "買進前,先做一次健檢", y, font(60),
                       color=TEAL_LIGHT)
    # Italic sub
    y = 980
    draw_text_centered(draw, "韭菜不是命,是健檢不夠勤", y, font(44),
                       color=(148, 163, 184))

    # Bottom CTA card
    card_y = 1400
    card_h = 360
    card_pad = 80
    rounded_rect(img, [(card_pad, card_y), (W - card_pad, card_y + card_h)],
                  radius=40, fill=(15, 118, 110, 200))

    draw_text_centered(draw, "🔍  4 面健檢", card_y + 50, font(52, bold=True))
    draw_text_centered(draw, "技術 · 籌碼 · 基本 · 新聞",
                       card_y + 130, font(38), color=TEAL_LIGHT)
    draw_text_centered(draw, "30 秒看完一檔股票體質",
                       card_y + 215, font(36), color=(203, 213, 225))

    return img.convert("RGB")


def make_screenshot_2():
    """4 面健檢分數展示"""
    img = gradient_bg(W, H)
    draw = ImageDraw.Draw(img)

    # Header
    draw_text_centered(draw, "🩺 4 面健檢", 160, font(72, bold=True))
    draw_text_centered(draw, "每檔股票全面體檢,一眼看出體質",
                       290, font(40), color=TEAL_LIGHT)

    # 4 cards
    cards = [
        ("📈 技術面", "趨勢 · 量價 · KD · MACD · RSI", "85", "健康", ACCENT_GREEN),
        ("📊 籌碼面", "三大法人 · 散戶比例 · 融資融券", "72", "良好", TEAL_LIGHT),
        ("📰 新聞面", "重大新聞 · 市場情緒摘要", "58", "警示", ACCENT_AMBER),
        ("📋 基本面", "月營收 YoY · 財報 · PE/PB", "78", "健康", ACCENT_GREEN),
    ]
    card_y = 460
    card_h = 280
    card_pad = 60
    for i, (label, sub, score, status, color) in enumerate(cards):
        y = card_y + i * (card_h + 24)
        rounded_rect(img, [(card_pad, y), (W - card_pad, y + card_h)],
                      radius=24, fill=(30, 41, 59, 220),
                      outline=(47, 52, 61), width=2)
        # Left: label + sub
        draw.text((card_pad + 40, y + 40), label, fill=(255, 255, 255),
                   font=font(52, bold=True))
        draw.text((card_pad + 40, y + 130), sub, fill=(148, 163, 184),
                   font=font(32))
        # Right: score + status
        score_x = W - card_pad - 280
        draw.text((score_x, y + 40), score, fill=color, font=font(120, bold=True))
        # Right status badge
        draw.text((score_x + 30, y + 200), status, fill=color, font=font(32, bold=True))

    return img.convert("RGB")


def make_screenshot_3():
    """7 個真 alpha 策略"""
    img = gradient_bg(W, H)
    draw = ImageDraw.Draw(img)

    draw_text_centered(draw, "📡 7 個真 alpha 策略",
                       160, font(64, bold=True))
    draw_text_centered(draw, "Backtest + OOS + MCPT 全通過",
                       280, font(38), color=TEAL_LIGHT)

    strategies = [
        ("💰", "月營收 YoY 高成長", "+5.10%", "60d"),
        ("👻", "散戶比例極端低位", "+11.3pp", "20d"),
        ("📉", "量縮跌停反彈", "+4.27%", "5d"),
        ("📈", "量縮漲停", "+4.83%", "20d"),
        ("🤝", "AB 雙重共識", "+8.78%", "60d"),
        ("🏦", "政府行庫反向", "+1.62pp", "60d"),
        ("⚠️", "法人 divergence 警示", "watch", "10d"),
    ]

    item_y = 440
    item_h = 160
    item_pad = 50
    for i, (emo, name, alpha, frame) in enumerate(strategies):
        y = item_y + i * (item_h + 14)
        rounded_rect(img, [(item_pad, y), (W - item_pad, y + item_h)],
                      radius=20, fill=(15, 118, 110, 80),
                      outline=(20, 184, 166, 120), width=2)
        draw.text((item_pad + 30, y + 38), emo, fill=(255, 255, 255), font=font(72))
        draw.text((item_pad + 150, y + 22), name, fill=(255, 255, 255),
                   font=font(40, bold=True))
        draw.text((item_pad + 150, y + 80), f"alpha {alpha} · {frame}",
                   fill=TEAL_LIGHT, font=font(32))
        # Right: green check
        draw.text((W - item_pad - 100, y + 50), "✓", fill=ACCENT_GREEN,
                   font=font(80, bold=True))

    return img.convert("RGB")


def make_screenshot_4():
    """觀察清單 + 記帳"""
    img = gradient_bg(W, H)
    draw = ImageDraw.Draw(img)

    draw_text_centered(draw, "⭐ 觀察清單 + 記帳",
                       160, font(68, bold=True))
    draw_text_centered(draw, "卡牌風格 · 集中度警示 · 跟券商一致",
                       290, font(36), color=TEAL_LIGHT)

    # Portfolio summary banner
    banner_y = 440
    banner_h = 220
    rounded_rect(img, [(60, banner_y), (W - 60, banner_y + banner_h)],
                  radius=24, fill=(20, 184, 166, 80),
                  outline=(94, 234, 212), width=2)
    draw.text((100, banner_y + 30), "💰 總市值",
               fill=(148, 163, 184), font=font(36))
    draw.text((100, banner_y + 90), "NT$ 487,320",
               fill=(255, 255, 255), font=font(78, bold=True))
    draw.text((W - 360, banner_y + 30), "📈 損益",
               fill=(148, 163, 184), font=font(36))
    draw.text((W - 360, banner_y + 90), "+ 12.4%",
               fill=ACCENT_GREEN, font=font(78, bold=True))

    # Stock cards
    stocks = [
        ("2330", "台積電", "1,025", "+1.8%", "↗", ACCENT_GREEN),
        ("0050", "元大台灣 50", "187.5", "+0.4%", "↗", ACCENT_GREEN),
        ("2454", "聯發科", "1,440", "-0.7%", "↘", ACCENT_RED),
        ("2412", "中華電", "128.5", "+0.2%", "→", TEAL_LIGHT),
        ("00878", "國泰永續", "23.6", "+0.5%", "↗", ACCENT_GREEN),
    ]

    card_y = 740
    card_h = 180
    card_pad = 60
    for i, (code, name, price, pct, arrow, color) in enumerate(stocks):
        y = card_y + i * (card_h + 16)
        rounded_rect(img, [(card_pad, y), (W - card_pad, y + card_h)],
                      radius=20, fill=(30, 41, 59, 200),
                      outline=(47, 52, 61), width=2)
        draw.text((card_pad + 30, y + 30), code, fill=TEAL_LIGHT,
                   font=font(50, bold=True))
        draw.text((card_pad + 30, y + 100), name, fill=(203, 213, 225),
                   font=font(34))
        draw.text((W - card_pad - 280, y + 30), price, fill=(255, 255, 255),
                   font=font(50, bold=True))
        draw.text((W - card_pad - 280, y + 100), pct + " " + arrow,
                   fill=color, font=font(40, bold=True))

    return img.convert("RGB")


def make_screenshot_5():
    """訪客模式 + 隱私"""
    img = gradient_bg(W, H)
    draw = ImageDraw.Draw(img)

    draw_text_centered(draw, "👻", 220, font(180))
    draw_text_centered(draw, "訪客模式", 460, font(80, bold=True))
    draw_text_centered(draw, "30 秒上手,不用註冊",
                       590, font(46), color=TEAL_LIGHT)

    # Feature bullets
    features = [
        ("✨", "不用 email 註冊"),
        ("🔒", "資料只存瀏覽器"),
        ("⚡", "全功能直接體驗"),
        ("🎯", "喜歡再回來建帳號"),
    ]
    by = 800
    for i, (icon, text) in enumerate(features):
        y = by + i * 140
        rounded_rect(img, [(120, y), (W - 120, y + 110)],
                      radius=24, fill=(15, 118, 110, 100),
                      outline=(20, 184, 166), width=2)
        draw.text((160, y + 25), icon, fill=(255, 255, 255), font=font(56))
        draw.text((280, y + 30), text, fill=(255, 255, 255),
                   font=font(46, bold=True))

    # Bottom: privacy
    by2 = 1480
    rounded_rect(img, [(80, by2), (W - 80, by2 + 280)],
                  radius=30, fill=(245, 158, 11, 30),
                  outline=ACCENT_AMBER, width=3)
    draw_text_centered(draw, "🔐 純客觀數據工具",
                       by2 + 35, font(48, bold=True), color=ACCENT_AMBER)
    draw_text_centered(draw, "不報明牌 · 不喊飆股",
                       by2 + 115, font(42), color=(255, 255, 255))
    draw_text_centered(draw, "不指示動作 · 盈虧自負",
                       by2 + 185, font(42), color=(255, 255, 255))

    return img.convert("RGB")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    makers = [
        make_screenshot_1,
        make_screenshot_2,
        make_screenshot_3,
        make_screenshot_4,
        make_screenshot_5,
    ]

    # Phone (1080×1920)
    for i, make in enumerate(makers, 1):
        img = make()
        out = OUT / f"screenshot-phone-{i}.png"
        img.save(out, "PNG")
        print(f"✓ Phone {i}/5: {out}")

    # Tablet 7-inch (resize to 1200×1920 — pad horizontally)
    for i in range(1, 6):
        src = Image.open(OUT / f"screenshot-phone-{i}.png").convert("RGB")
        canvas = Image.new("RGB", (1200, 1920), TOP)
        # Center the 1080-wide phone image on 1200 canvas
        canvas.paste(src, ((1200 - 1080) // 2, 0))
        out = OUT / f"screenshot-tablet7-{i}.png"
        canvas.save(out, "PNG")
        print(f"✓ Tablet7 {i}/5: {out}")

    # Tablet 10-inch (resize to 1600×2560 — scale up)
    for i in range(1, 6):
        src = Image.open(OUT / f"screenshot-phone-{i}.png").convert("RGB")
        # Scale to 1440×2560 then pad to 1600×2560
        target_h = 2560
        target_w = int(src.width * target_h / src.height)  # 1440
        scaled = src.resize((target_w, target_h), Image.LANCZOS)
        canvas = Image.new("RGB", (1600, 2560), TOP)
        canvas.paste(scaled, ((1600 - target_w) // 2, 0))
        out = OUT / f"screenshot-tablet10-{i}.png"
        canvas.save(out, "PNG")
        print(f"✓ Tablet10 {i}/5: {out}")

    print(f"\n✅ 全部 15 張截圖在 {OUT}")


if __name__ == "__main__":
    main()
