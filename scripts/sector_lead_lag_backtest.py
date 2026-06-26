"""
族群跟漲 (Sector Lead-Lag) Backtest — 龍頭爆量領漲，落後股跟進策略。

假設：散戶/法人看到龍頭異動 → 補買「便宜版」族群成員（落後 5-30 分鐘）。

策略邏輯：
  1. 對每個族群，定義 leader（流動性最強）+ followers
  2. 每日掃 09:00-11:00：找 leader 第一次累積報酬 ≥ pump_threshold 的時點 = sig_minute
  3. 對 follower 在 sig_minute + lag 進場，但只有當：
     - follower 至 sig_minute 累積報酬 < follow_cap（避免追高）
  4. 13:20 強制出場
  5. 扣 0.34% 摩擦成本

變體（4 × 3 × 3 = 36 組合）:
  pump_threshold: +1.0% / +1.5% / +2.0% / +2.5%（leader 觸發閾值）
  lag_minutes:    5 / 15 / 30（領先延遲）
  follow_cap:     +0.5% / +1.0% / +1.5%（follower 追高上限）

族群（用既有 cache 的 ticker 配對）:
  ABF 載板:     leader=3037 欣興, followers=[8046 南電, 3189 景碩]
  DRAM:         leader=2408 南亞科, followers=[2344 華邦電, 2337 旺宏]
  面板:         leader=3481 群創, followers=[2409 友達]
  晶圓代工:     leader=2303 聯電, followers=[6770 力積電]
  航運:         leader=2603 長榮, followers=[2609 陽明, 2615 萬海]
  散熱:         leader=3017 奇鋐, followers=[3324 雙鴻, 3338 泰碩]
  IC 通路/封測: leader=2382 廣達, followers=[2376 技嘉, 3036 文曄, 3231 緯創]

驗收：
  Tier A — pair × variant: OOS mean>0 AND CI low>0 AND test_n>=10
  Tier B — OOS mean>0 但 CI 跨 0 或 sample<10
"""
from __future__ import annotations

import io
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategy.volume_anomaly_scanner import lookup_ticker_name  # noqa: E402

CACHE = ROOT / "data" / "cache" / "finmind" / "minute"
COST = 0.34
CUTOFF = pd.Timestamp("2025-06-01")
SEED = 42
N_BOOT = 500

EXIT_TIME = "13:20"
SIG_WINDOW_END = "11:00"  # leader 觸發必須在此之前

# 族群定義（v2 — 加入 53 ticker universe 後的新 cluster）
SECTORS: dict[str, dict] = {
    # 原有 7 族群
    "ABF 載板":   {"leader": "3037", "followers": ["8046", "3189", "8358"]},   # +金居
    "DRAM":       {"leader": "2408", "followers": ["2344", "2337", "3260"]},   # +威剛
    "面板":       {"leader": "3481", "followers": ["2409"]},
    "晶圓代工":   {"leader": "2303", "followers": ["6770"]},
    "航運":       {"leader": "2603", "followers": ["2609", "2615"]},
    "散熱":       {"leader": "3017", "followers": ["3324", "3338", "3653"]},   # +健策
    "電子通路":   {"leader": "2382", "followers": ["2376", "3036", "3231"]},
    # 新增 6 族群（用 22 新 ticker）
    "AI server":  {"leader": "6669", "followers": ["3596", "2308", "5274"]},   # 緯穎/智易/台達電/信驊
    "PA 半導體":  {"leader": "3105", "followers": ["2455"]},                    # 穩懋/全新
    "重電":       {"leader": "1519", "followers": ["1503", "1513"]},            # 華城/士電/中興電
    "封測":       {"leader": "3711", "followers": ["6515"]},                    # 日月光投控/穎崴
    "Apple 供應": {"leader": "2308", "followers": ["3149", "1597"]},            # 台達電/正達/直得
    "矽光 / 測試":{"leader": "2360", "followers": ["6488", "4966", "6533"]},   # 致茂/環球晶/譜瑞/晶心科
}

PUMP_THRESHOLDS = [0.010, 0.015, 0.020, 0.025]
LAGS = [5, 15, 30]
FOLLOW_CAPS = [0.005, 0.010, 0.015]


def load_minute(ticker: str) -> pd.DataFrame:
    files = sorted(CACHE.glob(f"{ticker}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"]) if "dt" in df.columns else pd.to_datetime(
        df["date"].astype(str) + " " + df["minute"].astype(str)
    )
    df["date_only"] = df["dt"].dt.date
    df["minute_str"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt").reset_index(drop=True)


def compute_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    """每筆 minute bar 加 cum_return_from_open（從 09:00 開盤算）。"""
    out = []
    for d, sub in df.groupby("date_only"):
        sub = sub.sort_values("dt").reset_index(drop=True)
        first = sub[sub["minute_str"] >= "09:00"]
        if first.empty:
            continue
        open_price = float(first.iloc[0]["open"])
        sub = sub.copy()
        sub["cum_ret"] = sub["close"].astype(float) / open_price - 1
        out.append(sub)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def find_leader_signal(leader_day: pd.DataFrame, pump: float) -> str | None:
    """回傳 leader 第一次 cum_ret >= pump 的 minute_str（09:00-11:00 內）。"""
    sub = leader_day[(leader_day["minute_str"] >= "09:00") &
                     (leader_day["minute_str"] <= SIG_WINDOW_END)]
    hit = sub[sub["cum_ret"] >= pump]
    if hit.empty:
        return None
    return str(hit.iloc[0]["minute_str"])


def add_minutes(minute_str: str, delta: int) -> str:
    h, m = map(int, minute_str.split(":"))
    total = h * 60 + m + delta
    return f"{total // 60:02d}:{total % 60:02d}"


def get_bar(day_df: pd.DataFrame, minute_str: str) -> dict | None:
    bar = day_df[day_df["minute_str"] == minute_str]
    if bar.empty:
        # nearby fallback ±2
        h, m = map(int, minute_str.split(":"))
        for delta in [-1, 1, -2, 2]:
            mm = m + delta
            adj = f"{h + mm // 60:02d}:{mm % 60:02d}"
            bar = day_df[day_df["minute_str"] == adj]
            if not bar.empty:
                break
        if bar.empty:
            return None
    r = bar.iloc[0]
    return {"close": float(r["close"]), "cum_ret": float(r["cum_ret"]),
            "minute_str": str(r["minute_str"])}


def simulate_pair(
    leader_data: pd.DataFrame,
    follower_data: pd.DataFrame,
    pump: float,
    lag: int,
    cap: float,
) -> pd.DataFrame:
    """單對 (leader, follower) × 一組參數的所有交易。"""
    rows = []
    leader_by_day = {d: g for d, g in leader_data.groupby("date_only")}
    follower_by_day = {d: g for d, g in follower_data.groupby("date_only")}
    common_days = sorted(set(leader_by_day.keys()) & set(follower_by_day.keys()))

    for d in common_days:
        ld = leader_by_day[d]
        sig_min = find_leader_signal(ld, pump)
        if sig_min is None:
            continue
        # follower 在 sig_min 的 cum_ret
        fd = follower_by_day[d]
        f_at_sig = get_bar(fd, sig_min)
        if f_at_sig is None:
            continue
        if f_at_sig["cum_ret"] >= cap:
            continue  # follower 已先漲過 cap，不追高
        # follower 進場時點 = sig_min + lag
        entry_min = add_minutes(sig_min, lag)
        if entry_min > "13:00":
            continue  # 進場太晚
        f_entry = get_bar(fd, entry_min)
        if f_entry is None:
            continue
        # 出場 13:20
        f_exit = get_bar(fd, EXIT_TIME)
        if f_exit is None:
            continue
        gross = (f_exit["close"] / f_entry["close"] - 1) * 100
        net = gross - COST
        rows.append({
            "date": pd.Timestamp(d),
            "sig_min": sig_min,
            "entry_min": entry_min,
            "leader_cum_at_sig": ld[ld["minute_str"] == sig_min]["cum_ret"].iloc[0]
                if not ld[ld["minute_str"] == sig_min].empty else pump,
            "follower_cum_at_sig": f_at_sig["cum_ret"],
            "entry_price": f_entry["close"],
            "exit_price": f_exit["close"],
            "gross_pct": gross,
            "net_pct": net,
        })
    return pd.DataFrame(rows)


def stats_walk_forward(df_trades: pd.DataFrame) -> dict:
    n = len(df_trades)
    if n == 0 or "net_pct" not in df_trades.columns:
        return {
            "n": 0, "full_mean": np.nan, "full_win": np.nan,
            "train_n": 0, "train_mean": np.nan,
            "test_n": 0, "test_mean": np.nan, "test_win": np.nan,
            "ci_low": np.nan, "ci_high": np.nan,
        }
    rets = df_trades["net_pct"].values
    train = df_trades[df_trades["date"] < CUTOFF]
    test = df_trades[df_trades["date"] >= CUTOFF]
    rng = np.random.default_rng(SEED)
    if n >= 5:
        boot = np.array([rng.choice(rets, size=n, replace=True).mean() for _ in range(N_BOOT)])
        ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    else:
        ci_low, ci_high = np.nan, np.nan
    return {
        "n": n,
        "full_mean": rets.mean() if n else np.nan,
        "full_win": (rets > 0).mean() * 100 if n else np.nan,
        "train_n": len(train),
        "train_mean": train["net_pct"].mean() if len(train) else np.nan,
        "test_n": len(test),
        "test_mean": test["net_pct"].mean() if len(test) else np.nan,
        "test_win": (test["net_pct"] > 0).mean() * 100 if len(test) else np.nan,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


def main() -> None:
    print("=" * 90)
    print(f"Sector Lead-Lag Backtest | {len(SECTORS)} 族群 × "
          f"{len(PUMP_THRESHOLDS)*len(LAGS)*len(FOLLOW_CAPS)} 變體")
    print("=" * 90)

    # 載入所有需要的 ticker
    needed = set()
    for s in SECTORS.values():
        needed.add(s["leader"])
        needed.update(s["followers"])
    print(f"\n[1/3] 載入 minute cache + 計算 intraday cum_ret ({len(needed)} ticker)...")
    t0 = time.time()
    data: dict[str, pd.DataFrame] = {}
    for tk in sorted(needed):
        df = load_minute(tk)
        if df.empty:
            print(f"  ❌ {tk}: 無資料")
            continue
        df = compute_intraday_features(df)
        data[tk] = df
        print(f"  ✅ {tk} {lookup_ticker_name(tk)}: {len(df):,} rows / "
              f"{df['date_only'].nunique()} days")
    print(f"  載入 {time.time()-t0:.1f}s")

    # 跑 sector × variant
    print(f"\n[2/3] 跑 {len(SECTORS)} 族群 × 變體...")
    all_rows = []
    for sector, cfg in SECTORS.items():
        leader_tk = cfg["leader"]
        if leader_tk not in data:
            print(f"  ❌ {sector}: leader {leader_tk} 無資料"); continue
        ldata = data[leader_tk]
        for follower_tk in cfg["followers"]:
            if follower_tk not in data:
                continue
            fdata = data[follower_tk]
            for pump in PUMP_THRESHOLDS:
                for lag in LAGS:
                    for cap in FOLLOW_CAPS:
                        trades = simulate_pair(ldata, fdata, pump, lag, cap)
                        st = stats_walk_forward(trades)
                        all_rows.append({
                            "sector": sector,
                            "leader": leader_tk,
                            "leader_name": lookup_ticker_name(leader_tk),
                            "follower": follower_tk,
                            "follower_name": lookup_ticker_name(follower_tk),
                            "pump_pct": pump * 100,
                            "lag_min": lag,
                            "follow_cap_pct": cap * 100,
                            **st,
                        })
        print(f"  ✅ {sector}: leader={leader_tk}")

    res = pd.DataFrame(all_rows)
    if res.empty:
        print("❌ 無結果"); return

    # Tier 分級
    def tier(r):
        if r["test_n"] >= 10 and r["test_mean"] > 0 and r["ci_low"] > 0:
            return "A"
        elif r["test_n"] >= 5 and r["test_mean"] > 0:
            return "B"
        elif r["full_mean"] > 0 and r["full_win"] > 50:
            return "B-"
        return "C"

    res["tier"] = res.apply(tier, axis=1)

    out_csv = ROOT / "logs" / "sector_lead_lag.csv"
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[3/3] 寫入 {out_csv.relative_to(ROOT)} ({len(res)} rows)")

    # Summary
    for t in ["A", "B"]:
        sub = res[res["tier"] == t].sort_values("test_mean", ascending=False)
        if sub.empty:
            continue
        labels = {"A": "✅ Tier A (統計顯著 + OOS≥10)", "B": "⚠️ Tier B (OOS mean>0 但 CI 跨 0 或 n 小)"}
        print(f"\n{labels[t]} ({len(sub)})")
        print(f"  {'sector':<10} {'L':<5} {'F':<5} {'pump':>5} {'lag':>4} {'cap':>5} "
              f"{'n':>4} {'OOS m/w':>14} {'CI':>18}")
        for _, r in sub.head(20).iterrows():
            print(f"  {r['sector']:<10} {r['leader']:<5} {r['follower']:<5} "
                  f"{r['pump_pct']:>4.1f}% {r['lag_min']:>3}m {r['follow_cap_pct']:>4.1f}% "
                  f"{r['n']:>4} {r['test_mean']:>+5.2f}%/{r['test_win']:>4.0f}% "
                  f"[{r['ci_low']:>+5.2f}, {r['ci_high']:>+5.2f}]")

    # Pairs that pass Tier A (deduped)
    a_pairs = res[res["tier"] == "A"][["sector", "leader", "follower"]].drop_duplicates()
    print(f"\n通過 Tier A 的 pair: {len(a_pairs)}")
    for _, p in a_pairs.iterrows():
        best = res[(res["tier"] == "A") & (res["leader"] == p["leader"]) &
                   (res["follower"] == p["follower"])].sort_values("test_mean", ascending=False).iloc[0]
        print(f"  {p['sector']}: {p['leader']} → {p['follower']} "
              f"(best pump={best['pump_pct']:.1f}%, lag={best['lag_min']}m, "
              f"cap={best['follow_cap_pct']:.1f}%, OOS {best['test_mean']:+.2f}%/{best['test_win']:.0f}%)")

    # Build markdown summary
    md = ["# Sector Lead-Lag Backtest — Whitelist\n",
          f"Universe: {len(SECTORS)} 族群 × 36 變體 | Cost: {COST}% / 筆 | Cutoff: 2025-06-01\n"]
    for t in ["A", "B"]:
        sub = res[res["tier"] == t].sort_values("test_mean", ascending=False)
        if sub.empty:
            continue
        md.append(f"\n## Tier {t} ({len(sub)})\n")
        md.append("| sector | leader | follower | pump | lag | cap | n | OOS mean/win | CI | full mean |")
        md.append("|---|---|---|---|---|---|---|---|---|---|")
        for _, r in sub.head(40).iterrows():
            md.append(
                f"| {r['sector']} | {r['leader']} {r['leader_name']} | "
                f"{r['follower']} {r['follower_name']} | "
                f"{r['pump_pct']:.1f}% | {r['lag_min']}m | {r['follow_cap_pct']:.1f}% | "
                f"{r['n']} | {r['test_mean']:+.2f}%/{r['test_win']:.0f}% (n={r['test_n']}) | "
                f"[{r['ci_low']:+.2f}, {r['ci_high']:+.2f}] | {r['full_mean']:+.2f}% |"
            )
    out_md = ROOT / "logs" / "sector_lead_lag_summary.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\n寫入 {out_md.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
