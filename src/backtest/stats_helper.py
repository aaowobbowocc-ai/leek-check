"""
共用 backtest 統計 helper — 修正錯誤的 t-stat 公式

舊公式（錯）：
  t = alpha / (baseline_std / sqrt(n))
  問題：用 baseline_std 而非 signal_std；n 是 signal n 但 std 是 baseline
  → 統計上 incoherent

正確公式（two-sample Welch's t-test）：
  t = (signal_mean - baseline_mean) / sqrt(signal_var/n_signal + baseline_var/n_baseline)
"""
from __future__ import annotations
import numpy as np
from scipy import stats


def proper_t_stat(signal_returns, baseline_returns):
    """Welch's t-test（不假設等變異數）

    Returns: t_stat, p_value, alpha
    """
    sig = np.asarray(signal_returns)
    base = np.asarray(baseline_returns)
    if len(sig) < 2 or len(base) < 2:
        return None, None, None
    alpha = sig.mean() - base.mean()
    t, p = stats.ttest_ind(sig, base, equal_var=False, alternative="greater")
    return t, p, alpha


def block_bootstrap_alpha(signal_returns, baseline_returns,
                           signal_dates=None, n_bootstrap=1000,
                           block_size_days=5):
    """Block bootstrap 處理事件 clustering

    用法：當 signals cluster in time（e.g. COVID March 2020 多檔同時觸發），
    standard t-test inflate significance。block bootstrap 保留 temporal cluster。

    Args:
      signal_dates: 每個 signal 對應的 date（用來估算 cluster size）
      block_size_days: bootstrap block 長度

    Returns: dict with mean_alpha, ci_low, ci_high, p_value
    """
    sig = np.asarray(signal_returns)
    base = np.asarray(baseline_returns)
    if len(sig) < 30: return None
    real_alpha = sig.mean() - base.mean()

    rng = np.random.default_rng(42)
    boot_alphas = []
    for _ in range(n_bootstrap):
        # 簡化：對 signal 做 block bootstrap
        n = len(sig)
        if signal_dates is not None and len(signal_dates) == n:
            # group by date proximity
            sorted_idx = np.argsort(signal_dates)
            sig_sorted = sig[sorted_idx]
            n_blocks = max(1, n // block_size_days)
            block_starts = rng.integers(0, n - block_size_days + 1, n_blocks)
            boot_idx = np.concatenate([np.arange(s, s + block_size_days) for s in block_starts])
            boot_idx = boot_idx[boot_idx < n]
            sample = sig_sorted[boot_idx]
        else:
            sample = rng.choice(sig, size=n, replace=True)
        boot_alphas.append(sample.mean() - base.mean())

    boot_alphas = np.array(boot_alphas)
    return {
        "real_alpha": real_alpha,
        "mean_boot_alpha": boot_alphas.mean(),
        "ci_low": np.percentile(boot_alphas, 2.5),
        "ci_high": np.percentile(boot_alphas, 97.5),
        "p_value": (boot_alphas <= 0).mean(),  # 多少 % bootstrap 給負 alpha
    }


def proper_mcpt(signal_returns, baseline_returns_pool,
                n_permute=1000, exclude_signal_from_pool=True, seed=42):
    """正確 MCPT — null pool 不包含 signal events 自己

    Args:
      signal_returns: 真實 signal returns
      baseline_returns_pool: 用來抽 random null 的 pool
        (例如其他非 signal events，或 same ticker random)
      exclude_signal_from_pool: 確保 pool 不含 signal events
    """
    sig = np.asarray(signal_returns)
    pool = np.asarray(baseline_returns_pool)
    if len(sig) < 30 or len(pool) < len(sig): return None
    real = sig.mean()

    rng = np.random.default_rng(seed)
    fakes = []
    for _ in range(n_permute):
        idx = rng.choice(len(pool), size=len(sig), replace=False)
        fakes.append(pool[idx].mean())
    fakes = np.array(fakes)
    p = (fakes >= real).mean()
    return {
        "real": real,
        "fake_mean": fakes.mean(),
        "fake_std": fakes.std(),
        "p_value": p,
    }


def fdr_correction(p_values, alpha=0.05):
    """Benjamini-Hochberg FDR correction

    給 N 個 p-values，回傳哪些通過 FDR α=0.05
    """
    p_values = np.asarray(p_values)
    n = len(p_values)
    order = np.argsort(p_values)
    sorted_p = p_values[order]
    threshold = alpha * np.arange(1, n + 1) / n
    passes = sorted_p <= threshold
    if not passes.any():
        return np.zeros(n, dtype=bool)
    # find largest index passing
    max_pass = np.where(passes)[0].max()
    result = np.zeros(n, dtype=bool)
    # all p-values up to max_pass position pass
    result[order[:max_pass + 1]] = True
    return result
