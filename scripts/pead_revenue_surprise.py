"""
PEAD with Revenue Acceleration + 法人立刻買 confirm — 新-3 backtest

Hypothesis:
  Memory says "EPS YoY level" failed (alpha ~0). Revenue YoY level deployed but
  cross-year robustness inconsistent. This script tests SURPRISE (not level):
    A. Revenue YoY surprise: current_yoy - median(yoy[-6:6m]) > 20pp
    B. 法人 confirm: post-publish 5d window, foreign+trust net-buy 3 consecutive days
    AB combo: A triggers AND B occurs in 5d window → entry on B-day close
  Hold: 60 trading days

Look-ahead protection:
  - Revenue 'date' col = 1st of publish month → actual publish ≈ +10 days
  - Use signal_date = date + 10 days (publish day, MOPS deadline)
  - For surprise calc, use only revenue rows where revenue_year/month <= row.year/month-1
    (median over PRIOR 6 months YoY, computed via shift)
  - Entry = day AFTER B-confirm trigger close (T+1 open, modeled by next-day close)
  - Institutional 'date' is T+0 close; safe to use as confirm signal post-publish

Validation gates (HARD):
  - N >= 50 events (each arm)
  - vs same-ticker random entry baseline
  - MCPT p < 0.05
  - OOS split 2017-2020 vs 2021-2025 both t > 1.5
  - Cost 0.585% round-trip subtracted from signal returns
  - Cluster-by-month SE (events in same month → ε correlated)

Output: logs/pead_revenue_surprise.csv
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "finmind" / "finmind"
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True, parents=True)

HOLD_DAYS = 60
COST_RT = 0.585  # 0.585% round-trip (memory: TW stock cost basis)
SURPRISE_THR = 20.0  # pp
PUBLISH_LAG_DAYS = 10
CONFIRM_WINDOW = 5  # trading days after publish
CONSEC_NETBUY = 3
N_PERMUTE = 500


# ─── Data loaders ──────────────────────────────────────────────────────────
def load_universe() -> list[str]:
    return sorted(
        p.stem.replace("TaiwanStockInstitutionalInvestorsBuySell_", "")
        for p in CACHE.glob("TaiwanStockInstitutionalInvestorsBuySell_*.parquet")
    )


def load_price(tk: str) -> pd.DataFrame:
    p = TW_CACHE / f"{tk}.parquet"
    if not p.exists() or p.stat().st_size < 500:
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_revenue(tk: str) -> pd.DataFrame:
    p = CACHE / f"TaiwanStockMonthRevenue_{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        rev = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if rev.empty or len(rev) < 24:
        return pd.DataFrame()
    rev = rev.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
    rev["prior_revenue"] = rev["revenue"].shift(12)
    rev["yoy"] = (rev["revenue"] / rev["prior_revenue"] - 1) * 100
    # rolling median of PRIOR 6 months YoY (shift(1) so current row excluded)
    rev["yoy_med6"] = rev["yoy"].shift(1).rolling(6, min_periods=4).median()
    rev["surprise"] = rev["yoy"] - rev["yoy_med6"]
    rev["date"] = pd.to_datetime(rev["date"])
    # publish_date ≈ 1st-of-month + 10 days
    rev["publish_date"] = rev["date"] + pd.Timedelta(days=PUBLISH_LAG_DAYS)
    return rev


def load_inst(tk: str) -> pd.DataFrame:
    p = CACHE / f"TaiwanStockInstitutionalInvestorsBuySell_{tk}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    # Aggregate Foreign + Trust net-buy
    df["net"] = df["buy"].fillna(0) - df["sell"].fillna(0)
    keep = df["name"].isin(["Foreign_Investor", "Investment_Trust"])
    g = df[keep].groupby("date")["net"].sum().rename("ft_net").reset_index()
    return g.sort_values("date").reset_index(drop=True)


# ─── Event collection ──────────────────────────────────────────────────────
def find_confirm_day(inst_g: pd.DataFrame, publish_d: pd.Timestamp) -> pd.Timestamp | None:
    """Within next CONFIRM_WINDOW trading days after publish_d, find first day
    where there's been ≥ CONSEC_NETBUY consecutive net-buy days ending that day.
    Returns the day index (publish_d itself NOT used; window starts > publish_d)."""
    window = inst_g[(inst_g["date"] > publish_d)
                    & (inst_g["date"] <= publish_d + pd.Timedelta(days=CONFIRM_WINDOW * 2))]
    if window.empty:
        return None
    window = window.head(CONFIRM_WINDOW + CONSEC_NETBUY)  # need extra rows for streak lookback
    # Walk through window dates; check streak ending at each day
    nets = window["ft_net"].values
    dates = window["date"].values
    for i in range(CONSEC_NETBUY - 1, len(window)):
        if all(nets[j] > 0 for j in range(i - CONSEC_NETBUY + 1, i + 1)):
            # only count if confirm day is within first CONFIRM_WINDOW days post-publish
            if i < CONFIRM_WINDOW + CONSEC_NETBUY - 1:
                return pd.Timestamp(dates[i])
    return None


def fwd_return(prices_idx: pd.Series, entry_d: pd.Timestamp, hold: int) -> float | None:
    """Entry = next trading day's close after entry_d. Hold = HOLD_DAYS bars."""
    future = prices_idx[prices_idx.index > entry_d]
    if len(future) <= hold:
        return None
    entry = future.iloc[0]
    exit_p = future.iloc[hold]
    if entry <= 0:
        return None
    return (exit_p / entry - 1) * 100


def collect_events(universe: list[str]) -> dict[str, pd.DataFrame]:
    """Return three event sets: A_only, B_only, AB."""
    print(f"  Universe: {len(universe)} tickers")
    print("  Collecting events (A=surprise, B=confirm, AB=combo)...")
    a_events, b_events, ab_events = [], [], []

    for i, tk in enumerate(universe):
        rev = load_revenue(tk)
        if rev.empty:
            continue
        prices = load_price(tk)
        if prices.empty or len(prices) < HOLD_DAYS + 60:
            continue
        prices_idx = prices.set_index("date")["close"]
        inst_g = load_inst(tk)

        # Same-ticker baseline (fixed seed by ticker for reproducibility)
        rng = np.random.RandomState(hash(tk) % (2**32))
        n_base = min(50, len(prices_idx) - HOLD_DAYS - 60)
        if n_base <= 0:
            continue
        base_idx = rng.choice(range(60, len(prices_idx) - HOLD_DAYS), size=n_base, replace=False)
        baseline_returns = []
        for j in base_idx:
            entry = prices_idx.iloc[j]
            exit_p = prices_idx.iloc[j + HOLD_DAYS]
            if entry > 0:
                baseline_returns.append((exit_p / entry - 1) * 100)
        b_mean = float(np.mean(baseline_returns)) if baseline_returns else 0.0
        b_std = float(np.std(baseline_returns)) if baseline_returns else 0.0

        # liquidity gate (kept loose)
        rev_v = rev[
            rev["surprise"].notna()
            & (rev["prior_revenue"] > 1e7)
            & (rev["yoy"].abs() < 500)
        ]

        # ----- A: surprise alone -----
        a_trig = rev_v[rev_v["surprise"] > SURPRISE_THR]
        for _, row in a_trig.iterrows():
            pub_d = row["publish_date"]
            fwd = fwd_return(prices_idx, pub_d, HOLD_DAYS)
            if fwd is None:
                continue
            a_events.append({
                "ticker": tk, "signal_date": pub_d,
                "fwd_60d": fwd, "baseline_mean": b_mean, "baseline_std": b_std,
                "surprise": row["surprise"], "yoy": row["yoy"],
                "year": pub_d.year, "ym": pd.Timestamp(pub_d).to_period("M"),
            })

        # ----- B: confirm-only (every revenue release, see if confirm fires) -----
        # iterate over ALL release dates as opportunities (not just surprise)
        if not inst_g.empty:
            for _, row in rev_v.iterrows():
                pub_d = row["publish_date"]
                cd = find_confirm_day(inst_g, pub_d)
                if cd is None:
                    continue
                fwd = fwd_return(prices_idx, cd, HOLD_DAYS)
                if fwd is None:
                    continue
                b_events.append({
                    "ticker": tk, "signal_date": cd,
                    "fwd_60d": fwd, "baseline_mean": b_mean, "baseline_std": b_std,
                    "surprise": row["surprise"], "yoy": row["yoy"],
                    "year": cd.year, "ym": pd.Timestamp(cd).to_period("M"),
                })

        # ----- AB combo: A AND B confirm in 5d window -----
        if not inst_g.empty:
            for _, row in a_trig.iterrows():
                pub_d = row["publish_date"]
                cd = find_confirm_day(inst_g, pub_d)
                if cd is None:
                    continue
                fwd = fwd_return(prices_idx, cd, HOLD_DAYS)
                if fwd is None:
                    continue
                ab_events.append({
                    "ticker": tk, "signal_date": cd,
                    "fwd_60d": fwd, "baseline_mean": b_mean, "baseline_std": b_std,
                    "surprise": row["surprise"], "yoy": row["yoy"],
                    "year": cd.year, "ym": pd.Timestamp(cd).to_period("M"),
                })

        if (i + 1) % 300 == 0:
            print(f"  [{i+1}/{len(universe)}]  A={len(a_events)} B={len(b_events)} AB={len(ab_events)}")

    return {
        "A_only": pd.DataFrame(a_events),
        "B_only": pd.DataFrame(b_events),
        "AB":     pd.DataFrame(ab_events),
    }


# ─── Stats ─────────────────────────────────────────────────────────────────
def cluster_by_month_se(events: pd.DataFrame, col: str = "alpha_net") -> tuple[float, float]:
    """Cluster-robust SE over month buckets. Returns (mean, se)."""
    if events.empty or col not in events.columns:
        return (0.0, 0.0)
    g = events.groupby("ym")[col].mean()
    mean = events[col].mean()
    G = len(g)
    if G < 2:
        return (float(mean), 0.0)
    # Liang-Zeger style cluster SE on month means
    se = float(g.std(ddof=1) / np.sqrt(G))
    return (float(mean), se)


def summarize(events: pd.DataFrame, label: str) -> dict:
    if events.empty:
        print(f"  {label}: n=0 (no events)")
        return {"label": label, "n": 0}
    df = events.copy()
    df["alpha_gross"] = df["fwd_60d"] - df["baseline_mean"]
    df["alpha_net"] = df["alpha_gross"] - COST_RT
    n = len(df)
    sig = df["fwd_60d"].mean()
    base = df["baseline_mean"].mean()
    alpha_g = df["alpha_gross"].mean()
    alpha_n = df["alpha_net"].mean()
    win_g = (df["alpha_gross"] > 0).mean() * 100
    win_n = (df["alpha_net"] > 0).mean() * 100
    bs = df["baseline_std"].mean()
    t_naive = alpha_n / (bs / np.sqrt(n)) if bs > 0 else None
    cluster_mean, cluster_se = cluster_by_month_se(df, "alpha_net")
    t_cluster = cluster_mean / cluster_se if cluster_se > 0 else None
    t_n_str = f"{t_naive:+.2f}" if t_naive else "n/a"
    t_c_str = f"{t_cluster:+.2f}" if t_cluster else "n/a"
    print(f"  {label}: n={n}, sig={sig:+.2f}%, base={base:+.2f}%, "
          f"alpha_gross={alpha_g:+.2f}%, alpha_net={alpha_n:+.2f}%, "
          f"win_net={win_n:.1f}%, t_naive={t_n_str}, t_cluster={t_c_str}")
    return {
        "label": label, "n": n, "sig_mean": round(sig, 3),
        "baseline_mean": round(base, 3), "alpha_gross": round(alpha_g, 3),
        "alpha_net": round(alpha_n, 3), "win_pct_gross": round(win_g, 1),
        "win_pct_net": round(win_n, 1),
        "t_naive": round(t_naive, 2) if t_naive else None,
        "t_cluster": round(t_cluster, 2) if t_cluster else None,
        "n_months": int(df["ym"].nunique()),
    }


def oos_2way(events: pd.DataFrame, label: str) -> list[dict]:
    """OOS 2-way split: 2017-2020 vs 2021-2025"""
    if events.empty:
        return []
    print(f"\n  {label} — OOS 2-way split (2017-2020 vs 2021-2025):")
    rows = []
    for period, mask in [
        ("2017-2020", (events["year"] >= 2017) & (events["year"] <= 2020)),
        ("2021-2025", (events["year"] >= 2021) & (events["year"] <= 2025)),
    ]:
        sub = events[mask].copy()
        if len(sub) < 30:
            print(f"    {period}: n={len(sub)} (太少)")
            continue
        sub["alpha_net"] = (sub["fwd_60d"] - sub["baseline_mean"]) - COST_RT
        n = len(sub)
        alpha = sub["alpha_net"].mean()
        bs = sub["baseline_std"].mean()
        t_naive = alpha / (bs / np.sqrt(n)) if bs > 0 else None
        cm, cse = cluster_by_month_se(sub, "alpha_net")
        t_clu = cm / cse if cse > 0 else None
        verdict = "OK" if (t_clu and t_clu > 1.5) else "FAIL"
        t_clu_str = f"{t_clu:+.2f}" if t_clu else "n/a"
        t_n_str = f"{t_naive:+.2f}" if t_naive else "n/a"
        print(f"    {period}: n={n}, alpha_net={alpha:+.2f}%, "
              f"t_naive={t_n_str}, t_cluster={t_clu_str} [{verdict}]")
        rows.append({
            "period": period, "n": n, "alpha_net": round(alpha, 3),
            "t_naive": round(t_naive, 2) if t_naive else None,
            "t_cluster": round(t_clu, 2) if t_clu else None,
            "verdict": verdict,
        })
    return rows


def mcpt(events: pd.DataFrame, label: str) -> dict:
    if events.empty:
        return {"label": label, "p_value": None}
    real_alpha = (events["fwd_60d"] - events["baseline_mean"]).mean() - COST_RT
    rng = np.random.RandomState(42)
    bp = events["baseline_mean"].values
    bs = events["baseline_std"].values
    fakes = []
    for _ in range(N_PERMUTE):
        fk = rng.normal(loc=bp, scale=bs)
        fakes.append(fk.mean() - bp.mean())
    fakes = np.array(fakes)
    p = float((fakes >= real_alpha).sum() / N_PERMUTE)
    print(f"  {label} MCPT: real_alpha_net={real_alpha:+.3f}%, p={p:.4f} "
          f"{'[<0.05 OK]' if p < 0.05 else '[FAIL]'}")
    return {"label": label, "real_alpha_net": round(real_alpha, 3),
            "mcpt_p": round(p, 4), "n_permute": N_PERMUTE}


# ─── Overlap analysis ──────────────────────────────────────────────────────
def overlap_with_deployed(ab_events: pd.DataFrame) -> dict:
    """How much does AB combo overlap with the already-deployed
    'relative YoY excess > +30%' signal? (memory: revenue_yoy deployed)
    Compute fraction of AB events that ALSO satisfy yoy > 30 (proxy for level signal)."""
    if ab_events.empty:
        return {}
    yoy_high = (ab_events["yoy"] > 30) & (ab_events["yoy"] < 200)
    overlap_pct = yoy_high.mean() * 100
    n_overlap = int(yoy_high.sum())
    incremental = ab_events[~yoy_high].copy()
    if len(incremental) >= 20:
        incremental["alpha_net"] = (incremental["fwd_60d"] - incremental["baseline_mean"]) - COST_RT
        incr_alpha = incremental["alpha_net"].mean()
        bs = incremental["baseline_std"].mean()
        n = len(incremental)
        t = incr_alpha / (bs / np.sqrt(n)) if bs > 0 else None
    else:
        incr_alpha = None
        t = None
    print(f"\n  Overlap with deployed YoY level (>30%):")
    print(f"    AB events with yoy>30:  {n_overlap}/{len(ab_events)} ({overlap_pct:.1f}%)")
    if incr_alpha is not None:
        print(f"    Incremental (yoy<=30):  n={len(incremental)}, "
              f"alpha_net={incr_alpha:+.2f}%, t={t:+.2f}")
    return {
        "ab_total": len(ab_events),
        "overlap_n": n_overlap,
        "overlap_pct": round(overlap_pct, 1),
        "incremental_n": int(len(ab_events) - n_overlap),
        "incremental_alpha_net": round(incr_alpha, 3) if incr_alpha is not None else None,
        "incremental_t": round(t, 2) if t else None,
    }


# ─── Main ──────────────────────────────────────────────────────────────────
def main():
    print("=" * 80)
    print("  PEAD Revenue Surprise + 法人 confirm — 新-3 backtest")
    print("=" * 80)

    universe = load_universe()
    events = collect_events(universe)

    print("\n" + "=" * 80)
    print("  Summary (cost 0.585% subtracted)")
    print("=" * 80)
    summaries = []
    oos_all = []
    mcpts = []
    for label in ["A_only", "B_only", "AB"]:
        ev = events[label]
        print(f"\n=== {label} ===")
        s = summarize(ev, label)
        summaries.append(s)
        oos_all.extend([dict(arm=label, **r) for r in oos_2way(ev, label)])
        mcpts.append(mcpt(ev, label))

    overlap = overlap_with_deployed(events["AB"])

    # ─── Final verdict ───
    print("\n" + "=" * 80)
    print("  Verdict")
    print("=" * 80)
    ab_s = summaries[2]
    ab_mc = mcpts[2]
    ab_oos = [r for r in oos_all if r["arm"] == "AB"]
    n_ok = ab_s.get("n", 0) >= 50
    t_ok = (ab_s.get("t_cluster") or 0) > 1.5
    mcpt_ok = (ab_mc.get("mcpt_p") or 1) < 0.05
    oos_ok = len(ab_oos) >= 2 and all((r.get("t_cluster") or 0) > 1.5 for r in ab_oos)
    alpha_pos = (ab_s.get("alpha_net") or 0) > 0
    gates = [
        ("N>=50", n_ok),
        ("alpha_net>0", alpha_pos),
        ("t_cluster>1.5", t_ok),
        ("MCPT p<0.05", mcpt_ok),
        ("OOS both periods t>1.5", oos_ok),
    ]
    print("  Gate checklist (AB combo):")
    for g, ok in gates:
        print(f"    {'PASS' if ok else 'FAIL'}  {g}")
    n_pass = sum(1 for _, ok in gates if ok)
    if n_pass == 5:
        verdict = "DEPLOY"
    elif n_pass >= 3 and alpha_pos:
        verdict = "EDGE"
    else:
        verdict = "FAIL"
    print(f"  → Verdict: {verdict}")

    # ─── Save CSV ───
    out_rows = []
    for s in summaries:
        out_rows.append({"section": "summary", **s})
    for r in oos_all:
        out_rows.append({"section": "oos", **r})
    for m in mcpts:
        out_rows.append({"section": "mcpt", **m})
    out_rows.append({"section": "overlap", **overlap})
    out_rows.append({"section": "verdict", "verdict": verdict,
                     "gates_passed": n_pass, "gates_total": 5})
    out = pd.DataFrame(out_rows)
    out_path = LOGS / "pead_revenue_surprise.csv"
    out.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved → {out_path}")

    return verdict, summaries, oos_all, mcpts, overlap


if __name__ == "__main__":
    main()
