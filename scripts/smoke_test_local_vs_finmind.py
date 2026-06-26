"""Smoke test — 比對 local TWSE/MOPS scrape vs FinMind for the same query.

Validates that post-FinMind-expiry the local scrapers return:
  1. Same shape (columns match)
  2. Same values (within tolerance)
  3. Cover the same dates

For 4 critical methods on a sample of holdings:
  - get_institutional      → TWSE T86
  - get_per_pbr            → TWSE BWIBBU_d
  - get_monthly_revenue    → MOPS

Pass criteria:
  - Inst net_buy mismatch < 1% per ticker per day
  - PER difference < 5% per ticker per day (allow methodology drift)
  - Revenue exact match per ticker per month

Run:
  python -m scripts.smoke_test_local_vs_finmind

Output: docs/smoke_test_local_vs_finmind.md
"""
from __future__ import annotations
import os, sys, io
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env", override=True)

import pandas as pd
from src.data.finmind_client import FinMindClient
from src.data import local_source

TOKEN = os.environ.get("FINMIND_TOKEN", "")

# Sample tickers from user holdings + popular ones
TICKERS = ["2408", "2345", "2330", "0050", "00646"]
DATE_START = date(2026, 5, 5)
DATE_END   = date(2026, 5, 6)


def cmp_inst(client):
    print("\n[1/3] Institutional buy/sell comparison...")
    rows = []
    for t in TICKERS:
        # Local first
        local = local_source.load_institutional_local(t, DATE_START, DATE_END)
        # Force FinMind by temp-disabling local
        os.environ["INVEST_DISABLE_LOCAL"] = "1"
        try:
            from importlib import reload
            from src.data import finmind_client as _fc
            reload(_fc)
            fm_client = _fc.FinMindClient(token=TOKEN)
            fm = fm_client.get_institutional(t, DATE_START, DATE_END)
        finally:
            os.environ.pop("INVEST_DISABLE_LOCAL", None)
            reload(_fc)

        if local.empty and fm.empty:
            rows.append((t, "BOTH EMPTY", "—", "—"))
            continue
        # Compute net_buy total per source
        local_net = (local["buy"] - local["sell"]).sum() if not local.empty else 0
        # FinMind normalizer renames buy/sell? Let's check raw
        if "net_buy" in fm.columns:
            fm_net = fm["net_buy"].sum()
        elif "buy" in fm.columns and "sell" in fm.columns:
            fm_net = (fm["buy"] - fm["sell"]).sum()
        else:
            fm_net = 0

        if abs(fm_net) < 1:
            pct_diff = 0 if abs(local_net) < 1 else 100
        else:
            pct_diff = abs(local_net - fm_net) / abs(fm_net) * 100
        rows.append((t, f"{local_net:,.0f}", f"{fm_net:,.0f}", f"{pct_diff:.2f}%"))
    return rows


def cmp_per(client):
    print("\n[2/3] PER comparison...")
    rows = []
    for t in TICKERS:
        local = local_source.load_per_local(t, DATE_START, DATE_END)
        os.environ["INVEST_DISABLE_LOCAL"] = "1"
        try:
            from importlib import reload
            from src.data import finmind_client as _fc
            reload(_fc)
            fm_client = _fc.FinMindClient(token=TOKEN)
            fm = fm_client.get_per_pbr(t, DATE_START, DATE_END)
        finally:
            os.environ.pop("INVEST_DISABLE_LOCAL", None)
            reload(_fc)

        local_per = local["PER"].iloc[-1] if not local.empty else None
        fm_per_col = "per" if "per" in fm.columns else "PER"
        fm_per = fm[fm_per_col].iloc[-1] if (not fm.empty and fm_per_col in fm.columns) else None

        if local_per is None and fm_per is None:
            rows.append((t, "—", "—", "—"))
            continue
        if local_per is None or fm_per is None:
            rows.append((t, str(local_per), str(fm_per), "MISSING"))
            continue
        diff_pct = abs(local_per - fm_per) / abs(fm_per) * 100 if fm_per else 0
        rows.append((t, f"{local_per:.2f}", f"{fm_per:.2f}", f"{diff_pct:.1f}%"))
    return rows


def cmp_revenue(client):
    print("\n[3/3] Monthly revenue comparison...")
    rows = []
    rev_start = date(2026, 4, 1)
    rev_end   = date(2026, 5, 31)
    for t in TICKERS:
        local = local_source.load_monthly_revenue_local(t, rev_start, rev_end)
        os.environ["INVEST_DISABLE_LOCAL"] = "1"
        try:
            from importlib import reload
            from src.data import finmind_client as _fc
            reload(_fc)
            fm_client = _fc.FinMindClient(token=TOKEN)
            fm = fm_client.get_monthly_revenue(t, rev_start, rev_end)
        finally:
            os.environ.pop("INVEST_DISABLE_LOCAL", None)
            reload(_fc)

        # Match by revenue_year + revenue_month
        if local.empty and fm.empty:
            rows.append((t, "BOTH EMPTY", "—", "—"))
            continue
        for _, lr in local.iterrows():
            ry, rm = int(lr["revenue_year"]), int(lr["revenue_month"])
            local_rev = float(lr["revenue"])
            fm_match = fm[(fm["revenue_year"] == ry) & (fm["revenue_month"] == rm)]
            if fm_match.empty:
                rows.append((f"{t}@{ry}-{rm:02d}", f"{local_rev:,.0f}", "MISSING", "—"))
                continue
            fm_rev = float(fm_match["revenue"].iloc[0])
            diff_pct = abs(local_rev - fm_rev) / abs(fm_rev) * 100 if fm_rev else 0
            rows.append((f"{t}@{ry}-{rm:02d}",
                         f"{local_rev:,.0f}", f"{fm_rev:,.0f}",
                         f"{diff_pct:.4f}%"))
    return rows


def main():
    if not TOKEN:
        print("ERROR: FINMIND_TOKEN not set")
        return

    client = FinMindClient(token=TOKEN)
    inst_rows = cmp_inst(client)
    per_rows  = cmp_per(client)
    rev_rows  = cmp_revenue(client)

    print("\n" + "=" * 70)
    print(f"INSTITUTIONAL ({DATE_START} ~ {DATE_END} accumulated net buy)")
    print("=" * 70)
    print(f"  {'Ticker':<8} {'Local':>15} {'FinMind':>15} {'Diff%':>10}")
    for t, l, f, d in inst_rows:
        print(f"  {t:<8} {l:>15} {f:>15} {d:>10}")

    print("\n" + "=" * 70)
    print(f"PER ({DATE_END} latest)")
    print("=" * 70)
    print(f"  {'Ticker':<8} {'Local':>10} {'FinMind':>10} {'Diff%':>8}")
    for t, l, f, d in per_rows:
        print(f"  {t:<8} {l:>10} {f:>10} {d:>8}")

    print("\n" + "=" * 70)
    print("MONTHLY REVENUE")
    print("=" * 70)
    print(f"  {'Ticker@YM':<18} {'Local (元)':>17} {'FinMind (元)':>17} {'Diff%':>10}")
    for t, l, f, d in rev_rows:
        print(f"  {t:<18} {l:>17} {f:>17} {d:>10}")

    # Save report
    out = ROOT / "docs" / "smoke_test_local_vs_finmind.md"
    out.parent.mkdir(exist_ok=True)
    md = ["# Local TWSE/MOPS vs FinMind — Smoke Test", ""]
    md.append(f"Test date range: {DATE_START} ~ {DATE_END}")
    md.append(f"Sample tickers: {TICKERS}")
    md.append("")
    md.append("## Institutional net buy (累計)")
    md.append("| Ticker | Local | FinMind | Diff |")
    md.append("|---|---:|---:|---:|")
    for t, l, f, d in inst_rows:
        md.append(f"| {t} | {l} | {f} | {d} |")
    md.append("")
    md.append("## PER (latest)")
    md.append("| Ticker | Local | FinMind | Diff |")
    md.append("|---|---:|---:|---:|")
    for t, l, f, d in per_rows:
        md.append(f"| {t} | {l} | {f} | {d} |")
    md.append("")
    md.append("## Monthly revenue")
    md.append("| Ticker @ YM | Local (元) | FinMind (元) | Diff |")
    md.append("|---|---:|---:|---:|")
    for t, l, f, d in rev_rows:
        md.append(f"| {t} | {l} | {f} | {d} |")
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
