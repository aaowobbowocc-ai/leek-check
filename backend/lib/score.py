"""4 面健檢分數計算 — 從 app/app.py 移植 (line 3020-3075).

純客觀,基於數據,0-100 分。
"""
from __future__ import annotations


def calc_technical_score(tech: dict | None) -> tuple[int, list[str]]:
    """技術面分數 + 解讀 bullet."""
    if not tech:
        return 50, ["資料不足"]
    s = 50
    notes = []
    price = tech.get("price", 0)
    ma5 = tech.get("ma5", 0)
    ma20 = tech.get("ma20", 0)
    ma60 = tech.get("ma60", 0)
    if price > ma5 > ma20 > ma60 > 0:
        s += 15
        notes.append("✓ MA 多頭排列 (5/20/60 順向)")
    elif price < ma60 and ma60 > 0:
        s -= 15
        notes.append("⚠️ 跌破 60 日均線")
    rsi = tech.get("rsi", 50)
    if 30 < rsi < 70:
        s += 5
        notes.append(f"✓ RSI {rsi:.0f} 健康區間")
    elif rsi > 80:
        s -= 10
        notes.append(f"⚠️ RSI {rsi:.0f} 過熱")
    elif rsi < 20:
        s += 5
        notes.append(f"✓ RSI {rsi:.0f} 超賣反彈機會")
    k = tech.get("k", 50)
    d = tech.get("d", 50)
    if k > d and k < 80:
        s += 5
        notes.append(f"✓ KD 黃金交叉 (K={k:.0f}, D={d:.0f})")
    elif k < d and k > 20:
        s -= 5
        notes.append(f"⚠️ KD 死亡交叉")
    return max(0, min(100, s)), notes


def calc_chip_score(chip: dict | None) -> tuple[int, list[str]]:
    """籌碼面分數 + 解讀 bullet."""
    if not chip:
        return 50, ["資料不足"]
    s = 50
    notes = []
    f_net = chip.get("foreign_20d", 0)
    i_net = chip.get("invtrust_20d", 0)
    total = f_net + i_net
    if total > 1000:
        s += 15
        notes.append(f"✓ 法人 20 日強買 (外+投 +{total:,.0f} 張)")
    elif total > 0:
        s += 5
        notes.append(f"✓ 法人小幅買進 (+{total:,.0f} 張)")
    elif total < -1000:
        s -= 15
        notes.append(f"⚠️ 法人 20 日強賣 ({total:,.0f} 張)")
    elif total < 0:
        s -= 5
        notes.append(f"⚠️ 法人小幅賣超 ({total:,.0f} 張)")
    retail_pct = chip.get("retail_pct")
    if retail_pct is not None:
        if retail_pct < 30:
            s += 5
            notes.append(f"✓ 散戶比例低 ({retail_pct:.0f}%) — 籌碼乾淨")
        elif retail_pct > 70:
            s -= 5
            notes.append(f"⚠️ 散戶比例高 ({retail_pct:.0f}%) — 籌碼凌亂")
    return max(0, min(100, s)), notes


def calc_fundamental_score(funda: dict | None) -> tuple[int, list[str]]:
    """基本面分數 + 解讀 bullet."""
    if not funda:
        return 50, ["資料不足"]
    s = 50
    notes = []
    per = funda.get("per")
    if per is not None and per > 0:
        if per < 15:
            s += 15
            notes.append(f"✓ 本益比 {per:.1f} 倍偏低 (價值區)")
        elif per < 25:
            s += 5
            notes.append(f"✓ 本益比 {per:.1f} 倍合理")
        elif per > 40:
            s -= 10
            notes.append(f"⚠️ 本益比 {per:.1f} 倍偏高")
    yoy = funda.get("rev_yoy") or 0
    if yoy > 20:
        s += 10
        notes.append(f"✓ 月營收 YoY +{yoy:.1f}% 強成長")
    elif yoy > 0:
        s += 5
        notes.append(f"✓ 月營收 YoY +{yoy:.1f}%")
    elif yoy < -10:
        s -= 10
        notes.append(f"⚠️ 月營收 YoY {yoy:.1f}% 衰退")
    yld = funda.get("yield") or 0
    if yld > 4:
        s += 5
        notes.append(f"✓ 殖利率 {yld:.1f}%")
    return max(0, min(100, s)), notes


def calc_news_score(news: dict | None) -> tuple[int, list[str]]:
    """新聞面分數 + 解讀 (暫時 placeholder, 之後接 sentiment API)."""
    if not news:
        return 50, ["新聞情緒分析尚未接入"]
    sentiment = news.get("sentiment", "neutral")
    if sentiment == "positive":
        return 70, ["近期新聞偏正面"]
    if sentiment == "negative":
        return 30, ["近期新聞偏負面"]
    return 50, ["近期新聞中性"]


def calc_composite_health(
    tech: dict | None,
    chip: dict | None,
    funda: dict | None,
    news: dict | None = None,
) -> dict:
    """完整 4 面健檢回傳 — 給 API 用."""
    t_score, t_notes = calc_technical_score(tech)
    c_score, c_notes = calc_chip_score(chip)
    f_score, f_notes = calc_fundamental_score(funda)
    n_score, n_notes = calc_news_score(news)

    # 加權 (技 40% / 籌 30% / 基 20% / 新 10%)
    composite = round(t_score * 0.4 + c_score * 0.3 + f_score * 0.2 + n_score * 0.1, 1)

    if composite >= 70:
        verdict = "健康"
        color = "green"
    elif composite >= 50:
        verdict = "良好"
        color = "teal"
    elif composite >= 35:
        verdict = "警示"
        color = "amber"
    else:
        verdict = "危險"
        color = "red"

    return {
        "composite": composite,
        "verdict": verdict,
        "color": color,
        "scores": {
            "technical": {"score": t_score, "notes": t_notes},
            "chip": {"score": c_score, "notes": c_notes},
            "fundamental": {"score": f_score, "notes": f_notes},
            "news": {"score": n_score, "notes": n_notes},
        },
    }
