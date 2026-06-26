"""
連續漲停板 + 法人首日：獨立新策略回測。

進場條件（同日全部成立）：
  - 過去 5 個交易日有 ≥ 3 天漲幅 ≥ 9%（漲停板代理；台股漲停 10%）
  - 進場當日法人淨買 > 0（首日進場確認）
  - 收盤價 > 5 日平均 × 1.05

出場：Trailing -25pp from peak（peak ≥ 5%），hard stop 200MA × 0.85
測試期：2019-01-01 ~ 2026-04-24
Universe：FinMind cache 全市場
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly_scanner import load_ohlcv_cache  # noqa: E402

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_FM = ROOT / "data" / "cache" / "finmind" / "finmind"

START = date(2019, 1, 1)
END = date(2026, 4, 24)
LIMIT_UP_PCT = 9.0          # 漲幅門檻（漲停 10%，給 1pp 容忍）
LOOKBACK_DAYS = 5
MIN_LIMIT_UP_DAYS = 3
TRAILING_PP = 25.0
HARD_STOP_MA_PCT = 0.85
MAX_HOLD_DAYS = 1500
MIN_PRICE = 10.0            # 避免抓到雞蛋水餃股
MIN_VOLUME = 1_000          # 千張/日


def detect_signals_for_ticker(ticker: str) -> list[dict]:
    """掃描整個歷史，找出符合條件的進場日。"""
    df = load_ohlcv_cache(ticker, CACHE_YF)
    if df.empty:
        return []
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df = df[(df["date"] >= START) & (df["date"] <= END)].reset_index(drop=True)
    if len(df) < LOOKBACK_DAYS + 1:
        return []

    df["pct_change"] = df["close"].pct_change() * 100
    df["ma5"] = df["close"].rolling(5).mean()

    # 法人淨買 cache（如有）
    inst_path = CACHE_FM / f"TaiwanStockInstitutionalInvestorsBuySell_{ticker}.parquet"
    if inst_path.exists():
        inst = pd.read_parquet(inst_path)
        inst["date"] = pd.to_datetime(inst["date"]).dt.date
        inst_daily = inst.groupby("date")["buy"].sum() - inst.groupby("date")["sell"].sum()
        inst_net = inst_daily.to_dict()
    else:
        inst_net = {}

    signals = []
    cooldown_until = date(1900, 1, 1)
    for i in range(LOOKBACK_DAYS, len(df) - 1):
        d = df.iloc[i]["date"]
        if d <= cooldown_until:
            continue
        c = float(df.iloc[i]["close"])
        if c < MIN_PRICE:
            continue
        v = float(df.iloc[i]["volume"])
        if v < MIN_VOLUME:
            continue

        # 過去 5 天漲停板數
        recent = df.iloc[i - LOOKBACK_DAYS + 1:i + 1]
        n_limit_up = (recent["pct_change"] >= LIMIT_UP_PCT).sum()
        if n_limit_up < MIN_LIMIT_UP_DAYS:
            continue

        ma5 = df.iloc[i]["ma5"]
        if pd.isna(ma5) or c <= ma5 * 1.05:
            continue

        # 法人首日確認（若無資料則跳過此 filter）
        if inst_net:
            net = inst_net.get(d, 0)
            if net <= 0:
                continue

        signals.append({
            "ticker": ticker,
            "entry_date": d,
            "entry_price": c,
            "n_limit_up_5d": int(n_limit_up),
        })
        cooldown_until = d + timedelta(days=60)

    return signals


def simulate_exit(ohlcv: pd.DataFrame, entry_date: date, entry_price: float) -> tuple[float, date, str]:
    df = ohlcv.sort_values("date").reset_index(drop=True).copy()
    df["ma200"] = df["close"].rolling(200).mean()
    after = df[df["date"] >= entry_date].reset_index(drop=True)
    if len(after) < 2:
        return 0.0, entry_date, "no_data"

    peak = 0.0
    for i in range(1, min(MAX_HOLD_DAYS, len(after))):
        c = float(after.iloc[i]["close"])
        ma = after.iloc[i]["ma200"]
        ret = (c / entry_price - 1) * 100
        if ret > peak:
            peak = ret
        if pd.notna(ma) and c < float(ma) * HARD_STOP_MA_PCT:
            return ret, after.iloc[i]["date"], "hard_stop_ma"
        if peak >= 5.0 and (peak - ret) >= TRAILING_PP:
            return ret, after.iloc[i]["date"], "trailing"
    last = after.iloc[-1]
    return (float(last["close"]) / entry_price - 1) * 100, last["date"], "end_of_data"


def main() -> None:
    print("Consecutive limit-up strategy backtest")
    print("=" * 60)
    universe_files = sorted(CACHE_YF.glob("*.parquet"))
    universe = [
        p.stem for p in universe_files
        if p.stem.isdigit() and len(p.stem) == 4
    ]
    print(f"Universe: {len(universe)} tickers")

    all_signals = []
    for i, tk in enumerate(universe, 1):
        sigs = detect_signals_for_ticker(tk)
        all_signals.extend(sigs)
        if i % 200 == 0:
            print(f"  [{i}/{len(universe)}] signals so far: {len(all_signals)}")

    print(f"\n總訊號數: {len(all_signals)}")

    if not all_signals:
        print("無訊號，退出")
        return

    # 計算每個 signal 的 trade outcome
    print("計算 trade outcomes ...")
    rows = []
    for sig in all_signals:
        df = load_ohlcv_cache(sig["ticker"], CACHE_YF)
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        ret, exit_d, reason = simulate_exit(df, sig["entry_date"], sig["entry_price"])
        rows.append({
            **sig,
            "exit_date": exit_d,
            "gross_return_pct": round(ret, 2),
            "exit_reason": reason,
            "hold_days": (exit_d - sig["entry_date"]).days,
        })

    out = pd.DataFrame(rows)
    out_path = ROOT / "logs" / "consecutive_limitup_trades.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"  → {out_path.relative_to(ROOT)}")

    # 統計
    print("\n=== 統計 ===")
    print(f"  N trades        : {len(out)}")
    print(f"  Win rate        : {(out['gross_return_pct'] > 0).mean() * 100:.1f}%")
    print(f"  Mean return     : {out['gross_return_pct'].mean():+.2f}%")
    print(f"  Median return   : {out['gross_return_pct'].median():+.2f}%")
    print(f"  Avg hold days   : {out['hold_days'].mean():.0f}")
    print(f"  Annualized exp  : {(((1 + out['gross_return_pct'].mean()/100) ** (365 / max(out['hold_days'].mean(), 1)) - 1) * 100):+.2f}%/yr")
    print(f"\n  Top 5 winners:")
    print(out.sort_values("gross_return_pct", ascending=False).head().to_string(index=False))
    print(f"\n  Bottom 5 losers:")
    print(out.sort_values("gross_return_pct").head().to_string(index=False))

    # 同時持倉統計
    import collections
    counter = collections.Counter()
    for _, t in out.iterrows():
        for d in pd.date_range(t["entry_date"], t["exit_date"]):
            counter[d.date()] += 1
    if counter:
        print(f"\n  同時最多持倉: {max(counter.values())}")
        print(f"  平均同時持倉: {sum(counter.values()) / len(counter):.1f}")

    # V2 Portfolio with max_concurrent + FIFO 排隊
    print("\n" + "=" * 60)
    print("V2 Portfolio 模擬（max=5 concurrent, 5%/筆，閒置在 0050）")
    print("=" * 60)
    v2_simulate(out)


def v2_simulate(trades: pd.DataFrame, max_concurrent: int = 5, per_trade_pct: float = 0.05) -> None:
    """以 max_concurrent gate + FIFO 模擬。優先順序：訊號當日漲停板數越多越優先。"""
    INITIAL = 100_000.0
    COST = 0.004

    # 0050 prices
    df_0050 = load_ohlcv_cache("0050", CACHE_YF)
    df_0050["date"] = pd.to_datetime(df_0050["date"]).dt.date
    prices_0050 = dict(zip(df_0050["date"], df_0050["close"].astype(float)))

    def near_p(d: date) -> float | None:
        for i in range(7):
            dd = d - timedelta(days=i)
            if dd in prices_0050:
                return prices_0050[dd]
        return None

    trades = trades.sort_values(["entry_date", "n_limit_up_5d"], ascending=[True, False]).copy()
    p_init = near_p(trades["entry_date"].min())
    if p_init is None:
        print("no init price")
        return
    shares_core = INITIAL / p_init

    open_pos = []
    closed_pnl = 0.0
    skipped = 0
    accepted = 0

    all_events = sorted(set(trades["entry_date"]) | set(trades["exit_date"]))
    for d in all_events:
        cur_p = near_p(d)
        if cur_p is None:
            continue
        # close
        still_open = []
        for pos in open_pos:
            if pos["exit_date"] <= d:
                exit_amt = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST)
                shares_core += exit_amt / cur_p
            else:
                still_open.append(pos)
        open_pos = still_open

        # open
        for _, t in trades[trades["entry_date"] == d].iterrows():
            if len(open_pos) >= max_concurrent:
                skipped += 1
                continue
            alloc = INITIAL * per_trade_pct
            if shares_core * cur_p < alloc:
                skipped += 1
                continue
            shares_core -= alloc / cur_p
            open_pos.append({
                "entry_amount": alloc * (1 - COST),
                "exit_date": t["exit_date"],
                "return_pct": float(t["gross_return_pct"]),
            })
            accepted += 1

    end_d = trades["exit_date"].max()
    end_p = near_p(end_d) or p_init
    portfolio_value = shares_core * end_p
    for pos in open_pos:
        portfolio_value += pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST)

    start_d = trades["entry_date"].min()
    years = (end_d - start_d).days / 365.25
    cagr = ((portfolio_value / INITIAL) ** (1 / years) - 1) * 100 if years > 0 else 0
    bh_cagr = ((end_p / p_init) ** (1 / years) - 1) * 100 if years > 0 else 0
    alpha = cagr - bh_cagr

    print(f"  接受 {accepted} 筆 / 跳過 {skipped} 筆 (queue 滿)")
    print(f"  最終價值     : ${portfolio_value:,.0f}")
    print(f"  CAGR         : {cagr:+.2f}%")
    print(f"  0050 同期    : {bh_cagr:+.2f}%")
    print(f"  Alpha        : {alpha:+.2f}pp/yr")
    print(f"  期間         : {start_d} ~ {end_d} ({years:.1f} 年)")

    # 不同 max_concurrent 試
    print("\n  不同 max_concurrent 對比:")
    for mc in [3, 5, 8, 10]:
        result_cagr = _try_mc(trades, prices_0050, mc, per_trade_pct, INITIAL, COST)
        print(f"    max={mc:>2}  CAGR {result_cagr:+.2f}%  alpha {result_cagr - bh_cagr:+.2f}pp")


def _try_mc(trades, prices_0050, max_concurrent, per_trade_pct, INITIAL, COST):
    def near_p(d):
        for i in range(7):
            dd = d - timedelta(days=i)
            if dd in prices_0050:
                return prices_0050[dd]
        return None
    p_init = near_p(trades["entry_date"].min())
    shares_core = INITIAL / p_init
    open_pos = []
    all_events = sorted(set(trades["entry_date"]) | set(trades["exit_date"]))
    for d in all_events:
        cur_p = near_p(d)
        if cur_p is None:
            continue
        still_open = []
        for pos in open_pos:
            if pos["exit_date"] <= d:
                exit_amt = pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST)
                shares_core += exit_amt / cur_p
            else:
                still_open.append(pos)
        open_pos = still_open
        for _, t in trades[trades["entry_date"] == d].iterrows():
            if len(open_pos) >= max_concurrent:
                continue
            alloc = INITIAL * per_trade_pct
            if shares_core * cur_p < alloc:
                continue
            shares_core -= alloc / cur_p
            open_pos.append({
                "entry_amount": alloc * (1 - COST),
                "exit_date": t["exit_date"],
                "return_pct": float(t["gross_return_pct"]),
            })
    end_d = trades["exit_date"].max()
    end_p = near_p(end_d) or p_init
    pv = shares_core * end_p
    for pos in open_pos:
        pv += pos["entry_amount"] * (1 + pos["return_pct"] / 100) * (1 - COST)
    years = (end_d - trades["entry_date"].min()).days / 365.25
    return ((pv / INITIAL) ** (1 / years) - 1) * 100 if years > 0 else 0


if __name__ == "__main__":
    main()
