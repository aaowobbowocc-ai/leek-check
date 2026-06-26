"""
ChatGPT Finding #3: Family-wise error rate across 18 signals

問題: 每個 signal 單獨 p<0.0001，但 18 個一起測試
→ 整體 false discovery 機率遠高於表面

方法: Deflated Sharpe Ratio (Bailey & López de Prado 2014)
DSR 懲罰: 試驗次數、偏態、峰態

公式:
  DSR = SR * [1 - γ * SR^2 / (T-1)]^0.5

更簡單的近似:
  SR_hat_max ≈ max SR expected from N independent trials
  E[max SR | N trials] ≈ √(2 log N) — standard result

若你的 best signal SR < E[max SR | N trials] → 可能全部都是 noise
"""
from __future__ import annotations
import io, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
TW_CACHE = ROOT / "data" / "cache" / "yfinance" / "tw_ohlcv"

# 已修正後的 alpha 數字（post all-fixes）
SIGNALS = {
    "Revenue YoY":         {"mean": 2.03,  "std_proxy": 18.0, "n": 24476, "hold": 60},
    "Quiet Limitup 20d":   {"mean": 4.83,  "std_proxy": 12.0, "n": 5437,  "hold": 20},
    "Quiet Limitdown 20d": {"mean": 7.99,  "std_proxy": 14.0, "n": 4733,  "hold": 20},
    "Monster AB combo":    {"mean": 8.48,  "std_proxy": 25.0, "n": 900,   "hold": 60},
    "S1+S3 multifactor":   {"mean": 8.13,  "std_proxy": 22.0, "n": 1000,  "hold": 60},
    "0050 dealer 3d":      {"mean": 7.73,  "std_proxy": 8.0,  "n": 300,   "hold": 20},
    "Foreign TX OI z<-2":  {"mean": 1.43,  "std_proxy": 5.0,  "n": 123,   "hold": 10},
    "EH v3.7 monthly":     {"mean": 14.94, "std_proxy": 30.0, "n": 100,   "hold": 252},
    "DRAM pair trade":     {"mean": 3.16,  "std_proxy": 8.0,  "n": 30,    "hold": 20},
    "DXJ SPY 90d":         {"mean": 3.71,  "std_proxy": 15.0, "n": 50,    "hold": 90},
    "Quiet LU VIX 25-35":  {"mean": 3.66,  "std_proxy": 12.0, "n": 500,   "hold": 20},
    "Quiet LU VIX>=35":    {"mean": 9.05,  "std_proxy": 16.0, "n": 1061,  "hold": 20},
    "Quiet LD VIX 25-35":  {"mean": 9.11,  "std_proxy": 14.0, "n": 1200,  "hold": 20},
    "Quiet LD VIX>=35":    {"mean": 2.79,  "std_proxy": 18.0, "n": 2277,  "hold": 20},
    "Revenue × Sector":    {"mean": 5.5,   "std_proxy": 18.0, "n": 5000,  "hold": 60},
    "Dealer consecutive":  {"mean": 5.0,   "std_proxy": 10.0, "n": 300,   "hold": 20},
    "Limitup next-day":    {"mean": 0.55,  "std_proxy": 3.0,  "n": 17000, "hold": 1},
    "AB dual consensus":   {"mean": 8.78,  "std_proxy": 25.0, "n": 126,   "hold": 60},
}

N_SIGNALS = len(SIGNALS)
# 假設參數調整次數（threshold, holding period variants）
N_PARAMS_PER_SIGNAL = 5
N_TOTAL_TRIALS = N_SIGNALS * N_PARAMS_PER_SIGNAL


def compute_sr(mean_pct, std_pct, n, hold_days):
    """Annualized Sharpe Ratio（假設無 rf，粗估）"""
    trading_days = 252
    periods_per_year = trading_days / hold_days
    sr_per_period = mean_pct / std_pct  # raw SR per hold period
    sr_annual = sr_per_period * np.sqrt(periods_per_year)
    return sr_annual


def expected_max_sharpe(n_trials: int) -> float:
    """E[max SR | n independent trials] — asymptotic approximation"""
    # From Bailey & Lopez de Prado (2014): E[maxSR] ≈ (1-γ)Z^-1(1-1/N) + γZ^-1(1-1/(N*e))
    # Simple version:
    return np.sqrt(2 * np.log(n_trials))


def deflated_sr(sr_hat, n, skew=0, kurt=3, n_trials=1):
    """
    Deflated Sharpe Ratio — Bailey & Lopez de Prado (2014)
    Probability that SR > 0 after accounting for selection over N_trials tests

    DSR = Φ( (SR_hat - E[maxSR]) / √(V[SR]) )
    V[SR] = (1 - skew*SR + (kurt-1)/4 * SR^2) / (T-1)
    """
    sr_benchmark = expected_max_sharpe(n_trials)
    var_sr = (1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2) / (n - 1) if n > 1 else 1
    if var_sr <= 0: return 0.0
    z = (sr_hat - sr_benchmark) / np.sqrt(var_sr)
    return float(stats.norm.cdf(z))


def main():
    print("=" * 78)
    print(f"  ChatGPT #3: Deflated Sharpe Ratio (N={N_SIGNALS} signals, "
          f"~{N_TOTAL_TRIALS} trials total)")
    print("=" * 78)

    E_maxSR_signals = expected_max_sharpe(N_SIGNALS)
    E_maxSR_total   = expected_max_sharpe(N_TOTAL_TRIALS)
    print(f"\n  E[max SR | {N_SIGNALS} signals]:       {E_maxSR_signals:.3f}")
    print(f"  E[max SR | {N_TOTAL_TRIALS} total trials]:   {E_maxSR_total:.3f}")
    print(f"  (任何 SR < {E_maxSR_total:.2f} 在這麼多測試下可能是 noise)")

    print(f"\n  {'Signal':<28} {'SR(annual)':>10} {'DSR(single)':>12} "
          f"{'DSR(all trials)':>16} {'verdict':>10}")
    print(f"  {'-'*28} {'-'*10} {'-'*12} {'-'*16} {'-'*10}")

    results = []
    for name, s in SIGNALS.items():
        sr = compute_sr(s["mean"], s["std_proxy"], s["n"], s["hold"])
        dsr_single = deflated_sr(sr, s["n"], n_trials=1)
        dsr_all    = deflated_sr(sr, s["n"], n_trials=N_TOTAL_TRIALS)
        verdict = "✅ robust" if dsr_all > 0.95 else ("⚠️ borderline" if dsr_all > 0.5 else "❌ noise?")
        print(f"  {name:<28} {sr:>10.3f} {dsr_single:>12.4f} {dsr_all:>16.4f}   {verdict}")
        results.append((name, sr, dsr_single, dsr_all))

    robust = [r for r in results if r[3] > 0.95]
    borderline = [r for r in results if 0.5 < r[3] <= 0.95]
    noise = [r for r in results if r[3] <= 0.5]

    print(f"\n  📊 Summary:")
    print(f"    ✅ Robust (DSR>0.95):     {len(robust)}/18 signals")
    print(f"    ⚠️ Borderline (0.5-0.95): {len(borderline)}/18 signals")
    print(f"    ❌ Noise? (DSR<0.5):      {len(noise)}/18 signals")

    if noise:
        print(f"\n  ❌ Likely noise signals: {', '.join(r[0] for r in noise)}")
    if borderline:
        print(f"  ⚠️ Borderline signals: {', '.join(r[0] for r in borderline)}")

    print(f"\n  注意：std_proxy 用的是估算值，實際 SR 依賴真實 return distribution。")
    print(f"  主要結論：E[maxSR] = {E_maxSR_total:.2f}，真正 robust 訊號應 SR >> {E_maxSR_total:.1f}")


if __name__ == "__main__":
    main()
