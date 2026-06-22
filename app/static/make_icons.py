"""產生 PWA icon (192/512) — teal 漸層 + 🩺 emoji."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT_DIR = Path(__file__).parent


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), "#0f766e")
    draw = ImageDraw.Draw(img)
    # 模擬 hero 漸層感(對角從 teal 到 navy)
    for y in range(size):
        ratio = y / size
        # 漸層: #0f766e → #0a1a1f
        r = int(15 * (1 - ratio) + 10 * ratio)
        g = int(118 * (1 - ratio) + 26 * ratio)
        b = int(110 * (1 - ratio) + 31 * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    # 圓角效果(輪廓)
    radius = size // 8
    # mask 圓角
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([(0, 0), (size, size)], radius=radius, fill=255)
    rounded = Image.new("RGB", (size, size), "#0f766e")
    rounded.paste(img, (0, 0))
    rounded.putalpha(mask)
    # 中間放白色「健檢」二字 (簡化版,emoji 在 PIL 支援有限)
    try:
        # Windows 系統字體
        font_path = "C:/Windows/Fonts/msjh.ttc"
        font = ImageFont.truetype(font_path, int(size * 0.35))
    except Exception:
        font = ImageFont.load_default()
    text = "韭菜"
    draw_final = ImageDraw.Draw(rounded)
    bbox = draw_final.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - int(size * 0.04)
    draw_final.text((x, y), text, fill="white", font=font)
    # 副 LC 字樣
    try:
        font2 = ImageFont.truetype(font_path, int(size * 0.10))
    except Exception:
        font2 = ImageFont.load_default()
    sub = "LEEK CHECK"
    bbox2 = draw_final.textbbox((0, 0), sub, font=font2)
    sw = bbox2[2] - bbox2[0]
    sx = (size - sw) // 2
    sy = y + th + int(size * 0.06)
    draw_final.text((sx, sy), sub, fill=(94, 234, 212), font=font2)
    return rounded


for s in [192, 512]:
    icon = make_icon(s)
    out = OUT_DIR / f"icon-{s}.png"
    icon.save(out, "PNG")
    print(f"Created {out}")
