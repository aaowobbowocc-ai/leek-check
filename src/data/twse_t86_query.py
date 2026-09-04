"""
TWSE T86 法人 cache 查詢 helper

Cache: data/cache/twse_t86/{YYYYMMDD}.parquet（每日一檔）
Schema: date, stock_id, stock_name, market, foreign_net, trust_net, dealer_net, total_3_inst_net

優勢 vs FinMind cache:
  - 收盤後 1-2h 即有資料（vs FinMind T+1 早上）
  - 完全免費（FinMind 訂閱結束後仍可用）
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
T86_CACHE = ROOT / "data" / "cache" / "twse_t86"


def get_inst_recent(ticker: str, days_back: int = 14) -> pd.DataFrame:
    """取個股最近 N 個交易日的法人買賣超 (張 = 千股)"""
    today_d = date.today()
    rows = []
    for i in range(days_back + 5):  # 多看 5 天涵蓋週末
        d = today_d - timedelta(days=i)
        f = T86_CACHE / f"{d.strftime('%Y%m%d')}.parquet"
        if f.exists():
            df = pd.read_parquet(f)
            sub = df[df["stock_id"] == ticker]
            if not sub.empty:
                rows.append(sub)
        if len(rows) >= days_back: break
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True).sort_values("date")
    # 轉成「張」（÷ 1000）
    for col in ["foreign_net", "trust_net", "dealer_net", "total_3_inst_net"]:
        if col in df.columns:
            df[col + "_lots"] = df[col] / 1000
    return df


def get_market_total(d: date | str = None) -> dict:
    """取某天全市場法人 net buy 總和"""
    if d is None:
        d = date.today()
    if isinstance(d, str):
        d = date.fromisoformat(d)
    f = T86_CACHE / f"{d.strftime('%Y%m%d')}.parquet"
    if not f.exists(): return {}
    df = pd.read_parquet(f)
    return {
        "date": d.isoformat(),
        "n_stocks": len(df),
        "foreign_net_total": float(df["foreign_net"].sum()),
        "trust_net_total": float(df["trust_net"].sum()),
        "dealer_net_total": float(df["dealer_net"].sum()),
        "total_3_inst": float(df["total_3_inst_net"].sum()),
    }


def get_latest_available_date() -> date | None:
    """從 cache 找最新可用日期"""
    if not T86_CACHE.exists(): return None
    files = sorted(T86_CACHE.glob("*.parquet"), reverse=True)
    if not files: return None
    name = files[0].stem  # YYYYMMDD
    try:
        return date(int(name[:4]), int(name[4:6]), int(name[6:8]))
    except: return None


def get_top_buys(d: date | str = None, n: int = 20) -> pd.DataFrame:
    """某日外資買超 top N"""
    if d is None: d = date.today()
    if isinstance(d, str): d = date.fromisoformat(d)
    f = T86_CACHE / f"{d.strftime('%Y%m%d')}.parquet"
    if not f.exists(): return pd.DataFrame()
    df = pd.read_parquet(f)
    return df.nlargest(n, "foreign_net")[["stock_id", "stock_name",
                                          "foreign_net", "trust_net", "dealer_net"]]


def get_top_sells(d: date | str = None, n: int = 20) -> pd.DataFrame:
    """某日外資賣超 top N"""
    if d is None: d = date.today()
    if isinstance(d, str): d = date.fromisoformat(d)
    f = T86_CACHE / f"{d.strftime('%Y%m%d')}.parquet"
    if not f.exists(): return pd.DataFrame()
    df = pd.read_parquet(f)
    return df.nsmallest(n, "foreign_net")[["stock_id", "stock_name",
                                           "foreign_net", "trust_net", "dealer_net"]]
