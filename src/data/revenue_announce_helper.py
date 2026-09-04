"""
Revenue announcement date helper

問題：FinMind TaiwanStockMonthRevenue 的 'date' 欄位是「月份的 1 號」
（e.g., 4/1 對應 3 月營收），但實際公告日通常 11-21 天後（次月 11-21 號）

這是 look-ahead bias 來源 — backtest 用 date 進場 = 比實際公告早 11-21 天

修正：
  - 若有 create_time → 用 create_time 作為 announce_date
  - 否則用 date + 14 days (conservative middle estimate)
  - 法定最晚 10 日（次月 10 日）;實際 10-20 日多
"""
from __future__ import annotations
import pandas as pd
from datetime import timedelta


def estimate_announce_date(date_col, create_time_col=None,
                           default_lag_days=14):
    """
    給定 'date' 欄位（月份 1 號）+ optional 'create_time'，回傳估計公告日

    Args:
        date_col: pd.Series of period start dates (e.g., 2026-04-01 for 3 月)
        create_time_col: pd.Series of actual announce dates (may be empty)
        default_lag_days: 沒 create_time 時的 fallback (推薦 14)
                          法定最晚 10 日，多數實際 10-21 日
    Returns:
        pd.Series of estimated announce dates (datetime)
    """
    date_dt = pd.to_datetime(date_col)
    if create_time_col is not None:
        create_dt = pd.to_datetime(create_time_col, errors="coerce")
        # 用 create_time if available, else date + lag
        announce = date_dt + pd.Timedelta(days=default_lag_days)
        mask = create_dt.notna() & (create_dt > date_dt)
        announce = announce.where(~mask, create_dt)
        return announce
    else:
        return date_dt + pd.Timedelta(days=default_lag_days)


def is_post_announcement(today_date, period_date, create_time=None,
                         default_lag_days=14):
    """檢查 today 是否已過 estimated announce date

    用於 production scanner 避免 look-ahead bias
    """
    today_ts = pd.Timestamp(today_date)
    period_ts = pd.Timestamp(period_date)
    if create_time and pd.notna(create_time):
        try:
            create_ts = pd.Timestamp(create_time)
            return today_ts >= create_ts
        except Exception:
            pass
    estimated = period_ts + pd.Timedelta(days=default_lag_days)
    return today_ts >= estimated


def days_since_announcement(today_date, period_date, create_time=None,
                            default_lag_days=14):
    """回傳 days since estimated announce (negative if not yet announced)"""
    today_ts = pd.Timestamp(today_date)
    period_ts = pd.Timestamp(period_date)
    if create_time and pd.notna(create_time):
        try:
            create_ts = pd.Timestamp(create_time)
            return (today_ts - create_ts).days
        except Exception:
            pass
    estimated = period_ts + pd.Timedelta(days=default_lag_days)
    return (today_ts - estimated).days
