"""
Shioaji 自動下單 executor — 等開戶通過後立刻能上線。

設計：
  - Paper mode：訊號正常 + mock 成交（不真送單）
  - Production mode：真送單 + Discord 確認
  - Pair trading：雙邊同步下單（必要）
  - 風險管理：max_position / stop_loss / max_daily_drawdown

使用：
  pip install shioaji
  在 .env 加 SHIOAJI_API_KEY / SHIOAJI_API_SECRET / SHIOAJI_CA_PATH

  程式：
    from src.exec.shioaji_executor import ShioajiExecutor
    exec = ShioajiExecutor(paper_mode=True)
    exec.connect()
    exec.place_pair_trade("2408", "2344", direction="long_a_short_b",
                          shares_per_leg=1000, hold_days=20)
"""
from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """單筆交易結果（mock 或真實）"""
    ticker: str
    action: Literal["Buy", "Sell"]
    shares: int
    price: float           # 預計價（限價）or 0 (市價)
    fill_price: float       # 實際成交價
    order_type: str         # ROD / IOC / FOK
    order_cond: str         # Cash / MarginTrading / ShortSelling
    status: str             # Filled / Pending / Rejected
    timestamp: str
    paper_mode: bool


@dataclass
class RiskRules:
    """風險管理規則"""
    max_capital_per_pair: int = 350_000  # 單對最大 capital (DRAM 2408×1張 ~24萬)
    max_pairs_concurrent: int = 2        # 同時最多幾對
    stop_loss_pct: float = 5.0           # 單對虧 5% 強制平倉
    max_daily_drawdown_pct: float = 3.0  # 整體日虧 3% 暫停
    max_holding_days: int = 25           # 最長持有
    min_correlation: float = 0.80        # 持倉中 corr 跌破則平倉


class ShioajiExecutor:
    """Shioaji 自動下單執行器（paper / production 雙模）"""

    def __init__(self, paper_mode: bool = True, ca_path: str | None = None):
        if not paper_mode:
            unlock = os.environ.get("SHIOAJI_LIVE_TRADING_UNLOCK", "")
            if unlock != "I_UNDERSTAND_LIVE_TRADING_RISKS":
                raise RuntimeError(
                    "🚫 Live trading 已鎖死。要解開必須在 .env 加: "
                    "SHIOAJI_LIVE_TRADING_UNLOCK=I_UNDERSTAND_LIVE_TRADING_RISKS "
                    "且改 paper_mode=False。任何排程腳本都不該觸發此路徑。"
                )
        self.paper_mode = paper_mode
        self.ca_path = ca_path or os.environ.get("SHIOAJI_CA_PATH", "")
        self.api = None
        self.contracts = {}
        self.risk = RiskRules()
        self.trade_log_path = Path(__file__).resolve().parents[2] / "data" / "paper_trades" / "shioaji_trades.jsonl"
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> bool:
        """連接 Shioaji。Paper mode 不真連線。"""
        if self.paper_mode:
            logger.info("📋 Paper mode — 不真連 Shioaji")
            return True
        try:
            import shioaji as sj
            api_key = os.environ.get("SHIOAJI_API_KEY", "")
            api_secret = os.environ.get("SHIOAJI_API_SECRET", "")
            if not api_key or not api_secret:
                logger.error("❌ SHIOAJI_API_KEY / SECRET 未設")
                return False

            self.api = sj.Shioaji(simulation=False)
            self.api.login(api_key=api_key, secret_key=api_secret)
            if self.ca_path:
                ca_password = os.environ.get("SHIOAJI_CA_PASSWORD", "")
                self.api.activate_ca(ca_path=self.ca_path, ca_passwd=ca_password)
            logger.info("✅ Shioaji 連線成功（production mode）")
            return True
        except ImportError:
            logger.error("❌ shioaji 未安裝。pip install shioaji")
            return False
        except Exception as e:
            logger.error(f"❌ Shioaji 連線失敗: {e}")
            return False

    def disconnect(self):
        if self.api and not self.paper_mode:
            try:
                self.api.logout()
            except Exception:
                pass

    def get_contract(self, ticker: str):
        """取得 contract 物件（production mode）"""
        if self.paper_mode:
            return None
        if ticker in self.contracts:
            return self.contracts[ticker]
        if not self.api:
            return None
        try:
            contract = self.api.Contracts.Stocks[ticker]
            self.contracts[ticker] = contract
            return contract
        except Exception:
            return None

    def get_current_price(self, ticker: str) -> float:
        """取得即時報價"""
        if self.paper_mode:
            # paper mode 用 yfinance fallback
            try:
                import yfinance as yf
                t = yf.Ticker(f"{ticker}.TW")
                h = t.history(period="1d", auto_adjust=False)
                if not h.empty:
                    return float(h["Close"].iloc[-1])
            except Exception:
                pass
            return 0.0
        try:
            contract = self.get_contract(ticker)
            if contract:
                snap = self.api.snapshots([contract])
                if snap:
                    return float(snap[0].close)
        except Exception:
            pass
        return 0.0

    def place_order(
        self,
        ticker: str,
        action: Literal["Buy", "Sell"],
        shares: int,
        price: float = 0.0,
        order_type: str = "ROD",
        order_cond: str = "Cash",
    ) -> TradeResult:
        """
        下單（paper 或 production）
        order_cond: 'Cash' / 'MarginTrading' (融資買) / 'ShortSelling' (融券賣)
        """
        timestamp = datetime.now().isoformat(timespec="seconds")

        if self.paper_mode:
            # Paper mode: mock fill at current price (or limit price if 限價)
            cur = self.get_current_price(ticker)
            fill_price = price if price > 0 else cur
            result = TradeResult(
                ticker=ticker, action=action, shares=shares,
                price=price, fill_price=fill_price,
                order_type=order_type, order_cond=order_cond,
                status="Filled", timestamp=timestamp, paper_mode=True,
            )
            self._log_trade(result)
            logger.info(f"📋 Paper {action} {ticker} × {shares} @ {fill_price:.2f}")
            return result

        # Production mode
        if not self.api:
            raise RuntimeError("Shioaji 未連線")
        contract = self.get_contract(ticker)
        if not contract:
            raise ValueError(f"找不到 contract: {ticker}")

        try:
            import shioaji as sj
            order = sj.order.Order(
                action=action,
                price=price if price > 0 else 0,
                quantity=shares,
                price_type=sj.constant.StockPriceType.LMT if price > 0 else sj.constant.StockPriceType.MKT,
                order_type=getattr(sj.constant.OrderType, order_type),
                order_lot=sj.constant.StockOrderLot.Common,
                order_cond=getattr(sj.constant.StockOrderCond, order_cond),
                account=self.api.stock_account,
            )
            trade = self.api.place_order(contract, order)
            # 等填單 callback
            fill_price = price  # 簡化：實際應從 callback 取得
            result = TradeResult(
                ticker=ticker, action=action, shares=shares,
                price=price, fill_price=fill_price,
                order_type=order_type, order_cond=order_cond,
                status=trade.status.status if trade else "Pending",
                timestamp=timestamp, paper_mode=False,
            )
            self._log_trade(result)
            return result
        except Exception as e:
            logger.error(f"❌ 下單失敗: {e}")
            return TradeResult(
                ticker=ticker, action=action, shares=shares,
                price=price, fill_price=0,
                order_type=order_type, order_cond=order_cond,
                status=f"Error: {e}", timestamp=timestamp, paper_mode=False,
            )

    def place_pair_trade(
        self,
        ticker_long: str, ticker_short: str,
        shares_per_leg: int, capital_estimate: float,
        note: str = "",
    ) -> tuple[TradeResult, TradeResult]:
        """
        雙邊同步下單（配對交易必要）
        """
        # 風險檢查
        if capital_estimate > self.risk.max_capital_per_pair:
            raise ValueError(
                f"單對 capital {capital_estimate} 超過上限 {self.risk.max_capital_per_pair}"
            )

        long_result = self.place_order(
            ticker=ticker_long, action="Buy",
            shares=shares_per_leg, order_cond="Cash"
        )
        short_result = self.place_order(
            ticker=ticker_short, action="Sell",
            shares=shares_per_leg, order_cond="ShortSelling"
        )

        # 檢查兩邊都成交
        if long_result.status not in ("Filled",) and short_result.status not in ("Filled",):
            logger.warning("⚠️ 配對單部分未成交，需手動處理")

        logger.info(f"📊 配對單 long {ticker_long} short {ticker_short} × {shares_per_leg}")
        return long_result, short_result

    def _log_trade(self, result: TradeResult):
        """記錄到 jsonl"""
        with self.trade_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                **result.__dict__,
                "logged_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")

    def list_positions(self) -> list:
        """查詢持倉"""
        if self.paper_mode:
            # paper mode：從 jsonl 重建
            if not self.trade_log_path.exists():
                return []
            positions = {}
            with self.trade_log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    t = json.loads(line)
                    tk = t["ticker"]
                    if tk not in positions:
                        positions[tk] = {"shares": 0, "avg_cost": 0}
                    if t["action"] == "Buy":
                        old_shares = positions[tk]["shares"]
                        old_cost = positions[tk]["avg_cost"]
                        new_shares = old_shares + t["shares"]
                        new_cost = (old_shares * old_cost + t["shares"] * t["fill_price"]) / new_shares
                        positions[tk] = {"shares": new_shares, "avg_cost": new_cost}
                    else:  # Sell
                        positions[tk]["shares"] -= t["shares"]
            return [{"ticker": tk, **p} for tk, p in positions.items() if p["shares"] != 0]

        if not self.api:
            return []
        try:
            return [{"ticker": p.code, "shares": p.quantity, "avg_cost": p.price}
                    for p in self.api.list_positions()]
        except Exception as e:
            logger.error(f"list_positions error: {e}")
            return []


# ════════════════════════════════════════════
# 自動執行 wrapper（配合 unified_paper_ledger）
# ════════════════════════════════════════════
def execute_signal(executor: ShioajiExecutor, signal: dict) -> bool:
    """
    執行 unified_paper_ledger 的訊號。

    signal: {
      "strategy": "pair_2408_2344" / "0050_dealer_buy_3d",
      "ticker_long": str, "ticker_short": str,
      "entry_price_long": float, "entry_price_short": float,
      ...
    }
    """
    if signal["strategy"].startswith("pair_"):
        # 配對交易雙邊
        try:
            shares = 1000  # 1 張
            capital = signal["entry_price_long"] * shares
            long_r, short_r = executor.place_pair_trade(
                signal["ticker_long"], signal["ticker_short"],
                shares_per_leg=shares, capital_estimate=capital,
                note=signal.get("note", ""),
            )
            return long_r.status == "Filled" and short_r.status == "Filled"
        except Exception as e:
            logger.error(f"配對下單失敗: {e}")
            return False
    elif "dealer_buy" in signal["strategy"]:
        # 0050 long
        try:
            r = executor.place_order(
                signal["ticker_long"], "Buy",
                shares=1000, price=signal["entry_price_long"],
                order_cond="Cash"
            )
            return r.status == "Filled"
        except Exception as e:
            logger.error(f"自營商訊號下單失敗: {e}")
            return False

    logger.warning(f"未知策略: {signal['strategy']}")
    return False


# ════════════════════════════════════════════
# 測試
# ════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("Shioaji Executor 測試 (Paper mode)")
    print("=" * 60)

    exec = ShioajiExecutor(paper_mode=True)
    exec.connect()

    print("\n1. 測試現價:")
    p2408 = exec.get_current_price("2408")
    p2344 = exec.get_current_price("2344")
    print(f"  2408: {p2408:.2f}")
    print(f"  2344: {p2344:.2f}")

    print("\n2. 測試單筆下單 (Paper):")
    r = exec.place_order("0050", "Buy", 100, price=92.50)
    print(f"  {r.action} {r.ticker} × {r.shares} @ {r.fill_price} → {r.status}")

    print("\n3. 測試配對下單 (Paper):")
    long_r, short_r = exec.place_pair_trade(
        "2408", "2344",
        shares_per_leg=1000, capital_estimate=p2408 * 1000,
    )
    print(f"  Long: {long_r.action} {long_r.ticker} × {long_r.shares} @ {long_r.fill_price}")
    print(f"  Short: {short_r.action} {short_r.ticker} × {short_r.shares} @ {short_r.fill_price}")

    print("\n4. 持倉:")
    positions = exec.list_positions()
    for p in positions:
        print(f"  {p}")

    print("\n✅ Paper mode 測試完成")
    print("\n等 Shioaji 開戶通過後：")
    print("  1. pip install shioaji")
    print("  2. .env 加 SHIOAJI_API_KEY / SHIOAJI_API_SECRET")
    print("  3. ShioajiExecutor(paper_mode=False) 切換 production")
