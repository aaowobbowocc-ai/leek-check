"""
統一 Paper Ledger 強化版 — 每天記錄所有策略狀態（不只觸發時）。

設計：
  data/paper_trades/
    daily_state.csv      — 每天記錄所有 ticker 的 spread / 連買天數 / ORB 狀態
    triggered_signals.csv — 只在觸發時記錄（用於後續評估）
    strategy_perf.csv    — 累計表現 vs backtest

每日盤後跑：
  1. 配對 spread z-score（每天記）
  2. 0050/006208/00881/2308 法人連買天數（每天記）
  3. ORB 狀態（觸發 / regime 暫停 / 條件不達）
  4. 觸發訊號 → 寫入 triggered_signals + 預定 hold 期
  5. 評估到期 open 訊號 → 計算實際 vs 預期
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE_YF = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
CACHE_INST = ROOT / "data" / "cache" / "finmind" / "institutional"
LEDGER_DIR = ROOT / "data" / "paper_trades"
LEDGER_DIR.mkdir(parents=True, exist_ok=True)
DAILY_STATE = LEDGER_DIR / "daily_state.csv"
TRIGGERED = LEDGER_DIR / "triggered_signals.csv"

PAIRS = [("2408", "2344", "DRAM"),
         ("2330", "3711", "半導體 2330-3711"),
         ("2454", "3711", "半導體 2454-3711")]
INST_TICKERS = [("0050", "Dealer_self", 3, 1.23),
                ("006208", "Foreign_Investor", 3, 4.15),
                ("00881", "Foreign_Investor", 3, 2.71),
                ("2308", "Foreign_Investor", 3, 7.73)]


def load_ohlcv(tk):
    p = CACHE_YF / f"{tk}.parquet"
    if not p.exists(): return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def get_pair_zscore(a, b):
    """配對最新 z-score"""
    a_df = load_ohlcv(a)[["date", "close"]] if not load_ohlcv(a).empty else pd.DataFrame()
    b_df = load_ohlcv(b)[["date", "close"]] if not load_ohlcv(b).empty else pd.DataFrame()
    if a_df.empty or b_df.empty: return None
    merged = pd.merge(a_df.rename(columns={"close": "a"}),
                      b_df.rename(columns={"close": "b"}),
                      on="date").sort_values("date").reset_index(drop=True)
    if len(merged) < 60: return None
    merged["log_a"] = np.log(merged["a"])
    merged["log_b"] = np.log(merged["b"])
    merged["spread"] = merged["log_a"] - merged["log_b"]
    merged["spread_mean"] = merged["spread"].rolling(60).mean()
    merged["spread_std"] = merged["spread"].rolling(60).std()
    merged["z"] = (merged["spread"] - merged["spread_mean"]) / merged["spread_std"]
    return float(merged["z"].iloc[-1])


def get_inst_consec(ticker, name_col):
    """法人連買天數"""
    p = CACHE_INST / f"{ticker}.parquet"
    if not p.exists(): return 0, 0
    inst = pd.read_parquet(p)
    inst["date"] = pd.to_datetime(inst["date"]).dt.date
    pivot = inst.pivot_table(index="date", columns="name", values="net_buy",
                              aggfunc="sum").reset_index()
    pivot.columns.name = None
    pivot = pivot.sort_values("date").reset_index(drop=True)
    if name_col not in pivot.columns: return 0, 0
    pivot["is_buy"] = pivot[name_col] > 0
    consec_buy = 0
    for is_b in reversed(pivot["is_buy"].tolist()):
        if is_b: consec_buy += 1
        else: break
    consec_sell = 0
    for is_b in reversed(pivot["is_buy"].tolist()):
        if not is_b: consec_sell += 1
        else: break
    return consec_buy, consec_sell


def get_orb_status():
    """ORB regime 狀態"""
    try:
        from src.risk.strategy_regime_gate import detect_current_regime
        r = detect_current_regime()
        if r.cycle == "late_bull":
            return "🟠 LATE_BULL 過熱 → 暫停"
        if r.trend == "bear":
            return "🔴 熊市 → 暫停"
        return f"🟢 {r.cycle.upper()} → 啟用"
    except Exception:
        return "❓ regime check error"


def append_daily_state(today: date) -> dict:
    """寫入今日所有訊號狀態"""
    row = {"date": today.isoformat(),
           "logged_at": datetime.now().isoformat(timespec="seconds")}

    # 配對
    for a, b, label in PAIRS:
        z = get_pair_zscore(a, b)
        if z is not None:
            row[f"pair_{a}_{b}_z"] = round(z, 3)
            if z > 2.5:
                row[f"pair_{a}_{b}_signal"] = f"short_{a}_long_{b}"
            elif z < -2.5:
                row[f"pair_{a}_{b}_signal"] = f"long_{a}_short_{b}"
            elif abs(z) > 1.5:
                row[f"pair_{a}_{b}_signal"] = "approach"
            else:
                row[f"pair_{a}_{b}_signal"] = "calm"

    # 法人
    for tk, name_col, _, _ in INST_TICKERS:
        consec_buy, consec_sell = get_inst_consec(tk, name_col)
        row[f"inst_{tk}_consec_buy"] = consec_buy
        row[f"inst_{tk}_consec_sell"] = consec_sell

    # ORB regime
    row["orb_regime_status"] = get_orb_status()

    # Append CSV
    if DAILY_STATE.exists():
        df = pd.read_csv(DAILY_STATE)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        # 同日 dedup
        df = df.drop_duplicates(subset=["date"], keep="last")
    else:
        df = pd.DataFrame([row])
    df.to_csv(DAILY_STATE, index=False, encoding="utf-8-sig")
    return row


def detect_triggers(today: date, today_state: dict) -> list:
    """根據 today_state 抽出觸發訊號 → 寫 triggered_signals.csv"""
    triggers = []

    # 配對訊號
    for a, b, label in PAIRS:
        sig_key = f"pair_{a}_{b}_signal"
        z_key = f"pair_{a}_{b}_z"
        if sig_key not in today_state: continue
        sig = today_state[sig_key]
        if sig in ("short_a_long_b",) or sig.startswith("short_") or sig.startswith("long_"):
            if sig == "approach" or sig == "calm": continue
            ohlcv_a = load_ohlcv(a)
            ohlcv_b = load_ohlcv(b)
            if ohlcv_a.empty or ohlcv_b.empty: continue
            today_a = ohlcv_a[ohlcv_a["date"] == today]
            today_b = ohlcv_b[ohlcv_b["date"] == today]
            if today_a.empty or today_b.empty: continue
            triggers.append({
                "logged_at": datetime.now().isoformat(timespec="seconds"),
                "signal_date": today.isoformat(),
                "strategy": f"pair_{a}_{b}",
                "direction": sig,
                "z_score": today_state[z_key],
                "entry_price_a": float(today_a.iloc[-1]["close"]),
                "entry_price_b": float(today_b.iloc[-1]["close"]),
                "hold_days": 20,
                "expected_alpha": 3.16,
                "status": "open",
            })

    # 法人連買訊號（0050/006208/00881/2308）
    existing_open_strategies = set()
    if TRIGGERED.exists():
        try:
            _ex = pd.read_csv(TRIGGERED, dtype=str)
            existing_open_strategies = set(
                _ex[_ex["status"] == "open"]["strategy"].tolist()
            )
        except Exception:
            pass

    STRATEGY_MAP = {
        "0050":   ("0050_dealer_buy_3d",  1.23),
        "006208": ("006208_foreign_buy_3d", 4.15),
        "00881":  ("00881_foreign_buy_3d",  2.71),
        "2308":   ("2308_foreign_buy_3d",   7.73),
    }
    for tk, name_col, threshold, _ in INST_TICKERS:
        strat_name, exp_alpha = STRATEGY_MAP[tk]
        consec = int(today_state.get(f"inst_{tk}_consec_buy", 0))
        # 剛好越過 threshold，或已逾 threshold 但尚無 open 訊號（補抓遺漏）
        if consec < threshold:
            continue
        if strat_name in existing_open_strategies:
            continue
        ohlcv = load_ohlcv(tk)
        today_o = ohlcv[ohlcv["date"] == today]
        if today_o.empty:
            continue
        triggers.append({
            "logged_at": datetime.now().isoformat(timespec="seconds"),
            "signal_date": today.isoformat(),
            "strategy": strat_name,
            "ticker": tk,
            "direction": "long",
            "entry_price_a": float(today_o.iloc[-1]["close"]),
            "hold_days": 20,
            "expected_alpha": exp_alpha,
            "status": "open",
        })

    # 量縮跌停反彈 — 短線 5d strategy (audit alpha +4.27%/5d, win 71%)
    scanner_hits_path = LEDGER_DIR / "scanner_hits.csv"
    if scanner_hits_path.exists():
        try:
            df_hits = pd.read_csv(scanner_hits_path)
            qld = df_hits[(df_hits["signal"] == "quiet_limitdown_reversal") &
                          (df_hits["scan_date"] == today.isoformat())]
            for _, h in qld.iterrows():
                tk = str(h["ticker"])
                strat_name = f"quiet_ld_5d_{tk}"
                if strat_name in existing_open_strategies:
                    continue
                # alpha_5d ≈ 0.4 × 20d alpha (rough split); fallback to 2.13% (audit non-overlap)
                alpha_5d = float(h.get("expected_alpha_20d_net", 5.0)) * 0.4
                if pd.isna(alpha_5d) or alpha_5d == 0:
                    alpha_5d = 2.13
                triggers.append({
                    "logged_at":     datetime.now().isoformat(timespec="seconds"),
                    "signal_date":   today.isoformat(),
                    "strategy":      strat_name,
                    "ticker":        tk,
                    "direction":     "long",
                    "entry_price_a": float(h["close"]),
                    "hold_days":     5,
                    "expected_alpha": round(alpha_5d, 2),
                    "status":        "open",
                })
        except Exception as e:
            print(f"  ⚠️ quiet_limitdown 訊號讀取失敗: {e}")

    # Append
    if triggers:
        df_old = pd.read_csv(TRIGGERED, dtype=str) if TRIGGERED.exists() else pd.DataFrame()
        df_new = pd.DataFrame(triggers)
        combined = pd.concat([df_old, df_new], ignore_index=True)
        combined.to_csv(TRIGGERED, index=False, encoding="utf-8-sig")
    return triggers


def evaluate_open_triggers(today: date):
    """檢查 open 訊號是否到期 → 計算實際表現"""
    if not TRIGGERED.exists(): return
    df = pd.read_csv(TRIGGERED, dtype=str)
    if df.empty: return
    open_sigs = df[df["status"] == "open"]
    closed_count = 0
    for idx, row in open_sigs.iterrows():
        sig_date = pd.Timestamp(row["signal_date"]).date()
        hold = int(row["hold_days"])
        # 實際 hold_days 後的交易日
        elapsed = (today - sig_date).days
        if elapsed < hold * 1.4:  # 含週末
            continue

        strategy = row["strategy"]
        if strategy.startswith("pair_"):
            tickers = strategy.replace("pair_", "").split("_")
            if len(tickers) != 2: continue
            a, b = tickers
            ohlcv_a = load_ohlcv(a); ohlcv_b = load_ohlcv(b)
            o_dates = list(ohlcv_a["date"])
            if sig_date not in o_dates: continue
            sig_idx = o_dates.index(sig_date)
            if sig_idx + hold + 1 >= len(o_dates): continue
            exit_date = o_dates[sig_idx + hold + 1]
            exit_a = float(ohlcv_a[ohlcv_a["date"] == exit_date].iloc[-1]["close"])
            ent_a = float(row["entry_price_a"])
            exit_b_row = ohlcv_b[ohlcv_b["date"] == exit_date]
            if exit_b_row.empty: continue
            exit_b = float(exit_b_row.iloc[-1]["close"])
            ent_b = float(row["entry_price_b"])
            a_ret = (exit_a / ent_a - 1) * 100
            b_ret = (exit_b / ent_b - 1) * 100
            direction = row.get("direction", "")
            if "long_" + a in direction:
                gross = a_ret - b_ret
            else:
                gross = b_ret - a_ret
            net = gross - 0.34 * 2
            df.loc[idx, "exit_date"] = exit_date.isoformat()
            df.loc[idx, "net_pct"] = f"{net:+.2f}"
            df.loc[idx, "status"] = "closed"
            closed_count += 1
        elif "0050" in strategy:
            ohlcv = load_ohlcv("0050")
            o_dates = list(ohlcv["date"])
            if sig_date not in o_dates: continue
            sig_idx = o_dates.index(sig_date)
            if sig_idx + hold + 1 >= len(o_dates): continue
            exit_date = o_dates[sig_idx + hold + 1]
            exit_p = float(ohlcv[ohlcv["date"] == exit_date].iloc[-1]["close"])
            ent_p = float(row["entry_price_a"])
            net = (exit_p / ent_p - 1) * 100 - 0.34
            df.loc[idx, "exit_date"] = exit_date.isoformat()
            df.loc[idx, "net_pct"] = f"{net:+.2f}"
            df.loc[idx, "status"] = "closed"
            closed_count += 1
        elif strategy.startswith("quiet_ld_5d_"):
            # 量縮跌停反彈 5d hold — 個股
            ticker = strategy.replace("quiet_ld_5d_", "")
            ohlcv = load_ohlcv(ticker)
            if ohlcv.empty: continue
            o_dates = list(ohlcv["date"])
            if sig_date not in o_dates: continue
            sig_idx = o_dates.index(sig_date)
            if sig_idx + hold + 1 >= len(o_dates): continue
            exit_date = o_dates[sig_idx + hold + 1]
            exit_p = float(ohlcv[ohlcv["date"] == exit_date].iloc[-1]["close"])
            ent_p = float(row["entry_price_a"])
            net = (exit_p / ent_p - 1) * 100 - 0.585  # 個股 RT cost
            df.loc[idx, "exit_date"] = exit_date.isoformat()
            df.loc[idx, "net_pct"] = f"{net:+.2f}"
            df.loc[idx, "status"] = "closed"
            closed_count += 1
        elif "_div_predrift" in strategy:
            # ETF dividend pre-drift: hold N trading days then close
            # strategy 格式: "{ticker}_div_predrift_D{X}" e.g. "00850_div_predrift_D9"
            ticker = strategy.split("_div_predrift")[0]
            ohlcv = load_ohlcv(ticker)
            if ohlcv.empty: continue
            o_dates = list(ohlcv["date"])
            # Find first trading date >= sig_date for entry (handle weekends)
            entry_idx = None
            for i, d in enumerate(o_dates):
                if d >= sig_date:
                    entry_idx = i
                    break
            if entry_idx is None or entry_idx + hold >= len(o_dates):
                continue
            entry_date = o_dates[entry_idx]
            exit_date = o_dates[entry_idx + hold]
            entry_p = float(ohlcv[ohlcv["date"] == entry_date].iloc[-1]["close"])
            exit_p = float(ohlcv[ohlcv["date"] == exit_date].iloc[-1]["close"])
            net = (exit_p / entry_p - 1) * 100 - 0.34   # ETF cost 0.34%
            df.loc[idx, "exit_date"] = exit_date.isoformat()
            df.loc[idx, "net_pct"] = f"{net:+.2f}"
            # entry_price_a 更新為實際入場價(原本是 signal_date 時點的預估價)
            df.loc[idx, "entry_price_a"] = f"{entry_p:.2f}"
            df.loc[idx, "status"] = "closed"
            closed_count += 1
    df.to_csv(TRIGGERED, index=False, encoding="utf-8-sig")
    if closed_count > 0:
        print(f"  ✅ 平倉 {closed_count} 筆")


def push_triggers_to_discord(triggers: list) -> bool:
    """推送觸發訊號到 Discord"""
    if not triggers:
        return True
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        try:
            webhook_url = (ROOT / ".discord_webhook").read_text(encoding="utf-8").strip()
        except Exception:
            print("  ⚠️ DISCORD_WEBHOOK_URL 未設，跳過 Discord 推送")
            return False
    try:
        from src.notify.discord_client import DiscordNotifier
        notifier = DiscordNotifier(webhook_url)
        for trigger in triggers:
            strategy = trigger["strategy"]
            ticker = trigger.get("ticker", "?")
            entry = trigger.get("entry_price_a", 0)
            msg = f"[Paper] 🚨 **{strategy}** 觸發\n"
            msg += f"  • 標的: {ticker}\n"
            msg += f"  • 進場: {entry:.2f}\n"
            msg += f"  • 預期: +{trigger.get('expected_alpha', 0):.2f}%\n"
            msg += f"  • Hold: {trigger.get('hold_days', 0)}d"
            notifier.send(msg)
        print(f"  ✅ Discord 推送 {len(triggers)} 筆訊號成功")
        return True
    except Exception as e:
        print(f"  ⚠️ Discord 推送失敗: {e}")
        return False


def close_of_day_summary(today: date) -> bool:
    """收盤後總結：平倉結果 + 次日前景 → 推 Discord"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        try:
            webhook_url = (ROOT / ".discord_webhook").read_text(encoding="utf-8").strip()
        except Exception:
            return False

    if not TRIGGERED.exists():
        return False

    try:
        df = pd.read_csv(TRIGGERED, dtype=str)
        if df.empty:
            return False

        # 今日平倉
        today_str = today.isoformat()
        today_closed = df[(df["status"] == "closed") & (df["exit_date"] == today_str)]

        # 構造訊息
        lines = ["## [Paper] 📊 收盤後總結"]

        if not today_closed.empty:
            lines.append(f"\n✅ **[Paper] 今日平倉 {len(today_closed)} 筆**")
            today_closed["net_pct_num"] = pd.to_numeric(today_closed["net_pct"], errors="coerce")
            for _, row in today_closed.iterrows():
                strategy = row["strategy"]
                net = float(row["net_pct_num"])
                sign = "+" if net > 0 else ""
                lines.append(f"  • {strategy}: {sign}{net:.2f}%")
            # Avg per-trade,不是 sum(sum 對混 hold period 沒意義且會誇大)
            avg_net = today_closed["net_pct_num"].mean()
            wins = (today_closed["net_pct_num"] > 0).sum()
            lines.append(f"  **平均: {avg_net:+.2f}% / 筆**  (勝 {wins}/{len(today_closed)})")
        else:
            lines.append("\n⚪ [Paper] 今日無平倉")

        # Open 訊號
        open_sigs = df[df["status"] == "open"]
        lines.append(f"\n📈 **[Paper] Open 訊號: {len(open_sigs)} 筆**")

        # 明天前景
        if DAILY_STATE.exists():
            state_df = pd.read_csv(DAILY_STATE)
            if not state_df.empty:
                last = state_df.iloc[-1]
                lines.append(f"\n🔮 **明日訊號狀態**")
                for col in ["pair_2408_2344_signal", "pair_2330_3711_signal",
                           "pair_2454_3711_signal", "orb_regime_status"]:
                    if col in state_df.columns:
                        val = last.get(col, "—")
                        label = col.replace("_signal", "").replace("pair_", "")
                        lines.append(f"  • {label}: {val}")

        # 推送
        msg = "\n".join(lines)
        from src.notify.discord_client import DiscordNotifier
        notifier = DiscordNotifier(webhook_url)
        notifier.send(msg)
        print(f"  ✅ 收盤總結推送成功")
        return True
    except Exception as e:
        print(f"  ⚠️ 收盤總結推送失敗: {e}")
        return False


def report_summary():
    print(f"\n{'='*70}")
    print(f"📊 Unified Ledger Summary")
    print(f"{'='*70}")

    if DAILY_STATE.exists():
        df = pd.read_csv(DAILY_STATE)
        print(f"\n每日 state 紀錄: {len(df)} 天")
        if not df.empty:
            last = df.iloc[-1]
            print(f"\n  最新 ({last['date']}):")
            for col in df.columns:
                if col in ("date", "logged_at"): continue
                if "_z" in col or "consec" in col or "regime" in col:
                    print(f"    {col}: {last[col]}")

    if TRIGGERED.exists():
        df = pd.read_csv(TRIGGERED, dtype=str)
        print(f"\n觸發訊號: {len(df)}")
        if not df.empty:
            for status, sub in df.groupby("status"):
                print(f"  {status}: {len(sub)} 筆")
            closed = df[df["status"] == "closed"]
            if not closed.empty:
                closed["net_pct_num"] = pd.to_numeric(closed["net_pct"], errors="coerce")
                print(f"\n  Closed 表現:")
                for strat, sub in closed.groupby("strategy"):
                    mean = sub["net_pct_num"].mean()
                    n = len(sub)
                    expected = float(sub.iloc[0].get("expected_alpha", 0))
                    print(f"    {strat}: n={n}, 實際 mean={mean:+.2f}%, 預期 {expected:+.2f}%")


def main():
    today = date.today()
    print(f"=== Unified Paper Ledger ({today}) ===")

    print(f"\n[1/3] 寫入今日 state...")
    state = append_daily_state(today)
    print(f"  ✅ {DAILY_STATE.name}")

    print(f"\n[2/3] 偵測觸發訊號...")
    triggers = detect_triggers(today, state)
    if triggers:
        for t in triggers:
            print(f"  🚨 {t['strategy']}: {t['direction']}")
        push_triggers_to_discord(triggers)
    else:
        print(f"  ⚪ 無訊號觸發")

    print(f"\n[3/3] 評估到期 open 訊號...")
    evaluate_open_triggers(today)

    print(f"\n[4/4] 收盤後總結...")
    close_of_day_summary(today)

    report_summary()


if __name__ == "__main__":
    main()
