"""
交易紀錄模組 — 寫入買賣事件 + 自動更新 assets.json + 計算手續費/稅金。

費用結構（永豐金，可從 config 改）：
  手續費標準 0.1425%（買賣各收）
  3折優惠 → 實際 0.04275%（月退 rebate）
  證交稅 0.3%（僅賣方）
  當沖減半 → 0.15%

交易紀錄：data/transactions.jsonl（append-only）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Literal


# Default fee structure (永豐金 3折)
FEE_RATE_STANDARD = 0.001425         # 0.1425%
FEE_DISCOUNT = 0.30                   # 3 折
TAX_RATE = 0.003                      # 0.3%
TAX_DAYTRADE_DISCOUNT = 0.5           # 當沖減半


@dataclass
class TransactionResult:
    """交易試算結果"""
    action: str                 # "buy" / "sell"
    ticker: str
    shares: int
    price: float
    gross: float                # 價金 = shares × price
    fee_immediate: float        # 當下扣的手續費（全額 0.1425%）
    fee_rebate: float           # 月底退手續費 (gross × 0.1425% × 0.7)
    fee_net: float              # 淨手續費 = immediate - rebate
    tax: float                  # 證交稅（僅賣）
    net_cash_immediate: float   # 帳戶當下變動（含全手續費，未退）
    net_cash_final: float       # 月退後最終變動
    is_day_trade: bool


def compute_transaction(
    action: str,
    ticker: str,
    shares: int,
    price: float,
    is_day_trade: bool = False,
    fee_discount: float = FEE_DISCOUNT,
) -> TransactionResult:
    """純試算，不寫檔。"""
    gross = shares * price
    fee_immediate = gross * FEE_RATE_STANDARD
    fee_rebate = fee_immediate * (1 - fee_discount)
    fee_net = fee_immediate - fee_rebate

    if action == "sell":
        tax_rate = TAX_RATE * (TAX_DAYTRADE_DISCOUNT if is_day_trade else 1.0)
        tax = gross * tax_rate
        # 賣出：收到價金，扣手續費 + 稅
        net_cash_immediate = gross - fee_immediate - tax
        net_cash_final = gross - fee_net - tax
    else:  # buy
        tax = 0.0
        # 買入：付出價金，扣手續費（買方無證交稅）
        net_cash_immediate = -(gross + fee_immediate)
        net_cash_final = -(gross + fee_net)

    return TransactionResult(
        action=action, ticker=ticker, shares=shares, price=price,
        gross=gross, fee_immediate=fee_immediate, fee_rebate=fee_rebate,
        fee_net=fee_net, tax=tax,
        net_cash_immediate=net_cash_immediate,
        net_cash_final=net_cash_final,
        is_day_trade=is_day_trade,
    )


def record_transaction(
    project_root: Path,
    action: str,
    ticker: str,
    shares: int,
    price: float,
    trade_date: date | None = None,
    is_day_trade: bool = False,
    note: str = "",
) -> dict:
    """
    記錄交易 + 更新 assets.json。

    回傳：
      {
        "result": TransactionResult,
        "old_state": {...},
        "new_state": {...},
        "realized_pnl": float (僅 sell 有效)
      }
    """
    project_root = Path(project_root)
    assets_path = project_root / "data" / "assets.json"
    log_path = project_root / "data" / "transactions.jsonl"

    if trade_date is None:
        trade_date = date.today()

    # 試算
    result = compute_transaction(action, ticker, shares, price, is_day_trade)

    # 讀現有 assets
    with assets_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    holdings = data.get("holdings", {})
    long_term = holdings.get("long_term", []) or []
    short_term = holdings.get("short_term", []) or []

    # 找該 ticker 在哪
    target_list = long_term  # 預設長期
    target_h = None
    for h in long_term + short_term:
        if str(h.get("ticker")) == ticker:
            target_h = h
            target_list = long_term if h in long_term else short_term
            break

    realized_pnl = 0.0
    old_state = {
        "ticker": ticker,
        "shares": int(target_h["shares"]) if target_h else 0,
        "cost": float(target_h["cost"]) if target_h else 0.0,
        "cash": float(data.get("cash", 0)),
    }

    if action == "buy":
        # 加權平均成本
        if target_h:
            old_shares = int(target_h["shares"])
            old_cost = float(target_h["cost"])
            new_shares = old_shares + shares
            # 含手續費的實際進場成本
            new_cost = (old_shares * old_cost + shares * price + result.fee_immediate) / new_shares
            target_h["shares"] = new_shares
            target_h["cost"] = round(new_cost, 4)
        else:
            # 新加 holding
            cost_with_fee = (shares * price + result.fee_immediate) / shares
            new_h = {"ticker": ticker, "shares": shares, "cost": round(cost_with_fee, 4)}
            long_term.append(new_h)
            target_h = new_h
            target_list = long_term
        # 扣現金（用當下實扣，月底再加回 rebate）
        data["cash"] = round(data.get("cash", 0) + result.net_cash_immediate)

    elif action == "sell":
        if not target_h:
            raise ValueError(f"找不到持股 {ticker}，無法賣出")
        old_shares = int(target_h["shares"])
        old_cost = float(target_h["cost"])
        if shares > old_shares:
            raise ValueError(f"賣出 {shares} > 持有 {old_shares}")
        new_shares = old_shares - shares
        # 已實現損益（含成本含費用）
        cost_basis = shares * old_cost
        realized_pnl = result.net_cash_immediate - cost_basis  # 含當下實扣費用

        if new_shares == 0:
            # 清倉
            target_list.remove(target_h)
        else:
            target_h["shares"] = new_shares
            # cost 不變（FIFO 假設保留同成本）

        data["cash"] = round(data.get("cash", 0) + result.net_cash_immediate)

    else:
        raise ValueError(f"action 必須是 buy/sell，got {action}")

    new_state = {
        "ticker": ticker,
        "shares": int(target_h["shares"]) if target_h and (action == "buy" or new_shares > 0) else 0,
        "cost": float(target_h["cost"]) if target_h and (action == "buy" or new_shares > 0) else 0.0,
        "cash": float(data["cash"]),
    }

    # 寫回 assets.json
    holdings["long_term"] = long_term
    holdings["short_term"] = short_term
    data["holdings"] = holdings
    with assets_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Append 交易紀錄
    log_entry = {
        "date": trade_date.isoformat(),
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "ticker": ticker,
        "shares": shares,
        "price": price,
        "gross": round(result.gross, 2),
        "fee_immediate": round(result.fee_immediate, 2),
        "fee_rebate": round(result.fee_rebate, 2),
        "tax": round(result.tax, 2),
        "net_cash": round(result.net_cash_immediate, 2),
        "is_day_trade": is_day_trade,
        "realized_pnl": round(realized_pnl, 2) if action == "sell" else 0,
        "note": note,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    return {
        "result": result,
        "old_state": old_state,
        "new_state": new_state,
        "realized_pnl": realized_pnl,
        "log_entry": log_entry,
    }


def load_transactions(project_root: Path) -> list[dict]:
    """讀全部交易紀錄。"""
    log_path = Path(project_root) / "data" / "transactions.jsonl"
    if not log_path.exists():
        return []
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows
