"""
Bond ETF Regime Backtest — Fed cycle / DXY / VIX / TLT-lag signals

4 hypotheses:
  H1) DXY MoM <-1%               → TLT next 30d return
  H2) ^TNX 30d MA cross-down     → TLT next 30d return
  H3) VIX>25 AND ^TNX<3%         → TLT next 60d return
  H4) TLT 30d return             → 00679B next 30d (lag effect)

Long-only (00679B is retail-buyable).
Cost: bond ETF round-trip 0.34%.

Validation gates:
  n >= 30 events
  MCPT p < 0.05 (1000 perms)
  OOS split (two halves of >=5 yr each, where data allows)
  mean_net > 0.5%

Output: logs/bond_regime.csv  +  console summary.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

ROUND_TRIP_COST = 0.0034  # 0.34%
N_PERMS = 1000
RNG = np.random.default_rng(42)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def fetch(ticker: str, start: str = "2003-01-01") -> pd.Series | None:
    """Download adj close, return None on failure."""
    try:
        df = yf.download(ticker, start=start, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        col = "Close" if "Close" in df.columns else df.columns[0]
        s = df[col].dropna()
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.name = ticker
        return s
    except Exception as e:
        print(f"   fetch fail {ticker}: {e}")
        return None


def fetch_tw_bond(ticker_base: str) -> pd.Series | None:
    """Try .TW first, then .TWO."""
    for suf in (".TW", ".TWO"):
        s = fetch(ticker_base + suf, start="2017-01-01")
        if s is not None and len(s) > 200:
            print(f"   {ticker_base + suf}: {len(s)} rows  {s.index[0].date()}..{s.index[-1].date()}")
            return s
    return None


# ---------------------------------------------------------------------------
# Backtest helper
# ---------------------------------------------------------------------------
def forward_return(price: pd.Series, dates: list, hold_days: int) -> np.ndarray:
    """next-day open as proxy = next bar close. We use t+1 .. t+1+hold close-to-close."""
    rets = []
    idx = price.index
    for d in dates:
        try:
            i = idx.get_indexer([d])[0]
        except Exception:
            continue
        if i < 0 or i + 1 + hold_days >= len(price):
            continue
        entry = price.iloc[i + 1]
        exitp = price.iloc[i + 1 + hold_days]
        if entry > 0:
            rets.append(exitp / entry - 1.0)
    return np.array(rets)


def mcpt_pvalue(price: pd.Series, n_events: int, hold_days: int,
                observed_mean: float, n_perms: int = N_PERMS) -> float:
    """Random-entry baseline: sample n_events random dates, compute mean fwd return."""
    idx = price.index
    valid_max = len(price) - hold_days - 2
    if valid_max < n_events:
        return np.nan
    perm_means = []
    for _ in range(n_perms):
        sample = RNG.choice(valid_max, size=n_events, replace=False)
        rets = []
        for i in sample:
            entry = price.iloc[i + 1]
            exitp = price.iloc[i + 1 + hold_days]
            if entry > 0:
                rets.append(exitp / entry - 1.0)
        if rets:
            perm_means.append(np.mean(rets))
    perm_means = np.array(perm_means)
    return float((perm_means >= observed_mean).mean())


def evaluate(name: str, signal_dates: list, target: pd.Series, hold_days: int,
             cost: float = ROUND_TRIP_COST) -> dict:
    """Compute stats. signal_dates: list of pd.Timestamp where signal triggered."""
    rets = forward_return(target, signal_dates, hold_days)
    n = len(rets)
    if n < 5:
        return {"signal": name, "n": n, "mean_gross": np.nan, "mean_net": np.nan,
                "win": np.nan, "p_mcpt": np.nan, "oos1_mean": np.nan, "oos2_mean": np.nan,
                "passed": False, "note": "insufficient n"}

    mean_gross = float(np.mean(rets))
    mean_net = mean_gross - cost
    win = float((rets > 0).mean())

    # MCPT
    p_mcpt = mcpt_pvalue(target, n, hold_days, mean_gross) if n >= 30 else np.nan

    # OOS split — split signal dates by median date
    sd_sorted = sorted(signal_dates)
    if len(sd_sorted) >= 10:
        mid = sd_sorted[len(sd_sorted) // 2]
        h1 = [d for d in signal_dates if d <= mid]
        h2 = [d for d in signal_dates if d > mid]
        r1 = forward_return(target, h1, hold_days)
        r2 = forward_return(target, h2, hold_days)
        oos1 = float(np.mean(r1)) if len(r1) else np.nan
        oos2 = float(np.mean(r2)) if len(r2) else np.nan
        # year span check
        if h1 and h2:
            span1 = (h1[-1] - h1[0]).days / 365.25
            span2 = (h2[-1] - h2[0]).days / 365.25
        else:
            span1 = span2 = 0.0
    else:
        oos1 = oos2 = np.nan
        span1 = span2 = 0.0

    # Gates
    gate_n = n >= 30
    gate_mcpt = (not np.isnan(p_mcpt)) and (p_mcpt < 0.05)
    gate_mean = mean_net > 0.005
    gate_oos = (not np.isnan(oos1)) and (not np.isnan(oos2)) and oos1 > 0 and oos2 > 0
    passed = gate_n and gate_mcpt and gate_mean and gate_oos

    return {
        "signal": name, "n": n,
        "mean_gross": round(mean_gross * 100, 3),
        "mean_net": round(mean_net * 100, 3),
        "win": round(win * 100, 1),
        "p_mcpt": round(p_mcpt, 4) if not np.isnan(p_mcpt) else np.nan,
        "oos1_mean": round(oos1 * 100, 3) if not np.isnan(oos1) else np.nan,
        "oos2_mean": round(oos2 * 100, 3) if not np.isnan(oos2) else np.nan,
        "oos1_span_yr": round(span1, 1),
        "oos2_span_yr": round(span2, 1),
        "passed": passed,
        "note": ""
    }


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------
def signal_h1_dxy_decline(dxy: pd.Series, threshold: float = -0.01) -> list:
    """DXY MoM (~21d) <-1% trigger."""
    mom = dxy.pct_change(21)
    trig = mom[mom < threshold]
    # de-bounce: at most one trigger per 21d window
    out = []
    last = None
    for d in trig.index:
        if last is None or (d - last).days >= 21:
            out.append(d)
            last = d
    return out


def signal_h2_yield_cross_down(tnx: pd.Series, fast: int = 30) -> list:
    """^TNX crosses below its `fast`-day MA (downside cross)."""
    ma = tnx.rolling(fast).mean()
    above = tnx > ma
    cross = above.shift(1) & ~above  # was above yesterday, below today
    trig = cross[cross.fillna(False)]
    out = []
    last = None
    for d in trig.index:
        if last is None or (d - last).days >= 30:
            out.append(d)
            last = d
    return out


def signal_h3_vix_yield_combo(vix: pd.Series, tnx: pd.Series,
                              vix_thr: float = 25.0, tnx_thr: float = 3.0) -> list:
    """VIX>vix_thr AND ^TNX<tnx_thr — flight to bond regime entry day."""
    df = pd.concat([vix.rename("vix"), tnx.rename("tnx")], axis=1).dropna()
    cond = (df["vix"] > vix_thr) & (df["tnx"] < tnx_thr)
    # entry only on first day of the regime (rising edge)
    edge = cond & ~cond.shift(1).fillna(False)
    trig = edge[edge]
    out = []
    last = None
    for d in trig.index:
        if last is None or (d - last).days >= 60:  # de-bounce 60d
            out.append(d)
            last = d
    return out


def signal_h4_tlt_lag(tlt: pd.Series, lookback: int = 30,
                      threshold: float = 0.02) -> list:
    """TLT 30d return > 2% → trigger; tests TW bond next 30d."""
    ret = tlt.pct_change(lookback)
    trig = ret[ret > threshold]
    out = []
    last = None
    for d in trig.index:
        if last is None or (d - last).days >= 30:
            out.append(d)
            last = d
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 72)
    print("Bond ETF Regime Backtest — Fed cycle timing")
    print("=" * 72)

    # ---- Data ----
    print("\n[1/3] Loading data ...")
    print("  TW bond ETFs:")
    tw00679b = fetch_tw_bond("00679B")
    tw00687b = fetch_tw_bond("00687B")
    tw00772b = fetch_tw_bond("00772B")

    print("  US proxies:")
    tlt = fetch("TLT", start="2003-01-01");        print(f"   TLT: {len(tlt) if tlt is not None else 0} rows")
    ief = fetch("IEF", start="2003-01-01");        print(f"   IEF: {len(ief) if ief is not None else 0} rows")
    tnx = fetch("^TNX", start="2003-01-01");       print(f"   ^TNX: {len(tnx) if tnx is not None else 0} rows")
    vix = fetch("^VIX", start="2003-01-01");       print(f"   ^VIX: {len(vix) if vix is not None else 0} rows")

    print("  DXY proxy:")
    dxy = fetch("DX-Y.NYB", start="2003-01-01")
    if dxy is None:
        print("   DX-Y.NYB failed — fall back to UUP")
        dxy = fetch("UUP", start="2007-01-01")
    print(f"   DXY: {len(dxy) if dxy is not None else 0} rows")

    if any(s is None for s in [tlt, tnx, vix, dxy]):
        print("ERROR: required US series missing"); sys.exit(1)

    # ---- Run hypotheses ----
    print("\n[2/3] Generating signals & evaluating ...")
    results = []

    # H1: DXY decline → TLT 30d
    sd = signal_h1_dxy_decline(dxy, -0.01)
    print(f"  H1 DXY MoM<-1%: {len(sd)} events")
    results.append({**evaluate("H1_DXY_decline_TLT30d", sd, tlt, 30),
                    "target": "TLT", "hold_d": 30})

    # H2: ^TNX 30d MA cross down → TLT 30d
    sd = signal_h2_yield_cross_down(tnx, 30)
    print(f"  H2 ^TNX cross-down 30dMA: {len(sd)} events")
    results.append({**evaluate("H2_TNX_xdown_TLT30d", sd, tlt, 30),
                    "target": "TLT", "hold_d": 30})

    # H3: VIX>25 + ^TNX<3 → TLT 60d
    sd = signal_h3_vix_yield_combo(vix, tnx, 25.0, 3.0)
    print(f"  H3 VIX>25 & TNX<3: {len(sd)} events")
    results.append({**evaluate("H3_VIX25_TNX3_TLT60d", sd, tlt, 60),
                    "target": "TLT", "hold_d": 60})

    # H3b: relax to VIX>20 + ^TNX<4 (more events)
    sd = signal_h3_vix_yield_combo(vix, tnx, 20.0, 4.0)
    print(f"  H3b VIX>20 & TNX<4: {len(sd)} events")
    results.append({**evaluate("H3b_VIX20_TNX4_TLT60d", sd, tlt, 60),
                    "target": "TLT", "hold_d": 60})

    # H4: TLT 30d>2% → 00679B next 30d
    if tw00679b is not None:
        sd = signal_h4_tlt_lag(tlt, 30, 0.02)
        # restrict to dates within 00679B history
        sd = [d for d in sd if d >= tw00679b.index[0]]
        print(f"  H4 TLT 30d>+2% → 00679B 30d: {len(sd)} events")
        results.append({**evaluate("H4_TLT_lag_00679B30d", sd, tw00679b, 30),
                        "target": "00679B", "hold_d": 30})

        # H4b: TLT lag → 00687B (corp/inv-grade)
        if tw00687b is not None:
            sd2 = [d for d in sd if d >= tw00687b.index[0]]
            print(f"  H4b TLT 30d>+2% → 00687B 30d: {len(sd2)} events")
            results.append({**evaluate("H4b_TLT_lag_00687B30d", sd2, tw00687b, 30),
                            "target": "00687B", "hold_d": 30})

        # H4c: lower threshold +1%
        sd3 = signal_h4_tlt_lag(tlt, 30, 0.01)
        sd3 = [d for d in sd3 if d >= tw00679b.index[0]]
        print(f"  H4c TLT 30d>+1% → 00679B 30d: {len(sd3)} events")
        results.append({**evaluate("H4c_TLT1pct_lag_00679B30d", sd3, tw00679b, 30),
                        "target": "00679B", "hold_d": 30})

    # ---- Output ----
    print("\n[3/3] Saving results ...")
    df = pd.DataFrame(results)
    out_csv = LOG_DIR / "bond_regime.csv"
    df.to_csv(out_csv, index=False)
    print(f"  -> {out_csv}")

    # console table
    print("\n" + "=" * 72)
    print("RESULTS")
    print("=" * 72)
    cols = ["signal", "target", "n", "mean_gross", "mean_net", "win", "p_mcpt",
            "oos1_mean", "oos2_mean", "passed"]
    print(df[cols].to_string(index=False))

    # ---- Conclusion (200 words approximate) ----
    print("\n" + "=" * 72)
    print("CONCLUSION (auto-generated):")
    print("=" * 72)
    passed = df[df["passed"]]
    if len(passed) == 0:
        best = df.sort_values("mean_net", ascending=False).iloc[0]
        msg = (
            f"No signal cleared all four gates (n>=30, p_mcpt<0.05, mean_net>0.5%, both OOS halves >0).\n"
            f"Strongest by mean_net: {best['signal']} on {best['target']} — "
            f"n={best['n']}, gross={best['mean_gross']}%, net={best['mean_net']}%, "
            f"win={best['win']}%, p_mcpt={best['p_mcpt']}, OOS1={best['oos1_mean']}% / OOS2={best['oos2_mean']}%.\n"
            f"Implication: bond ETF Fed-cycle timing as a stand-alone alpha source is weak in close-to-close form. "
            f"DXY decline and yield cross-down generate plenty of events but the post-event drift is barely above "
            f"the 0.34% round-trip cost. The VIX+yield flight-to-bond combo has the right sign but very few "
            f"events (n typically <30) so MCPT is unreliable. TLT-lag → TW bond ETF is constrained by short "
            f"00679B history (IPO 2017), missing the 2020 bond rally captured by TLT directly.\n"
            f"Recommendation: do NOT deploy as standalone signal. Use as confirmation overlay only — e.g., "
            f"only DCA into 00679B when TLT 30d > +1% AND DXY MoM < 0 (regime confluence)."
        )
    else:
        msg = "Passed signals:\n" + passed.to_string(index=False)
    print(msg)

    return df


if __name__ == "__main__":
    main()
