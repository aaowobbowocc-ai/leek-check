"""
晨報組裝器 — 把 PipelineOutput + PortfolioSnapshot + DriftVerdict 渲染成 Markdown。

Jinja2 template: src/report/templates/morning.md.j2
輸出的 Markdown 同時寫入 logs/YYYY-MM-DD.md 並列印到 stdout。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.portfolio.asset_manager import PortfolioSnapshot
from src.risk.concept_drift import DriftVerdict
from src.strategy.composite_scorer import Recommendation
from src.strategy.scoring_pipeline import PipelineOutput

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"

_WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

_REGIME_LABELS = {
    "low":    "低波盤整",
    "normal": "正常交易",
    "high":   "高波趨勢",
    "crazy":  "🔴 狂波",
}


def _format_price(v: float) -> str:
    try:
        return f"{v:,.0f}"
    except Exception:
        return str(v)


def _format_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _format_amount(v) -> str:
    if v == "***":
        return "***"
    try:
        return f"{float(v):,.0f}"
    except Exception:
        return str(v)


def render_morning_report(
    pipeline_out: PipelineOutput,
    portfolio: PortfolioSnapshot,
    drift: DriftVerdict | None,
    company_names: dict[str, str] | None = None,
    min_score: float = 75.0,
    max_positions: int = 3,
    taiex_close: float = 0.0,
    taiex_above_ma: bool = True,
    asset_manager=None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape([]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["format_price"] = _format_price
    env.filters["format_pct"] = _format_pct

    def _fmt_amount(v):
        if asset_manager is not None:
            return asset_manager.format_amount(float(v))
        return _format_amount(v)

    env.filters["format_amount"] = _fmt_amount

    d = pipeline_out.as_of_date
    weekday = _WEEKDAYS[d.weekday()]

    # PortfolioSnapshot 欄位
    long_val = getattr(portfolio, "long_term_value", 0.0)
    short_val = getattr(portfolio, "short_term_value", 0.0)
    net_val = getattr(portfolio, "net_worth", getattr(portfolio, "net_value", 0.0))
    cash_val = getattr(portfolio, "cash", 0.0)
    short_budget = min(cash_val * 0.20, net_val * 0.20)
    reserve = max(0.0, cash_val - short_budget)

    portfolio_ctx = {
        "cash": cash_val,
        "long_term_value": long_val,
        "short_term_value": short_val,
        "net_value": net_val or net_val,   # alias
        "short_term_budget": short_budget,
        "reserve_cash": reserve,
    }

    ctx = {
        "report_date": d.isoformat(),
        "weekday": weekday,
        "defensive": pipeline_out.defensive,
        "defensive_reasons": pipeline_out.defensive_reasons,
        "recommendations": pipeline_out.recommendations,
        "company_names": company_names or {},
        "min_score": min_score,
        "max_positions": max_positions,
        "overnight": pipeline_out.recommendations[0].breakdown if False else pipeline_out.__dict__.get("_overnight_raw", {}),
        "regime_label": _REGIME_LABELS.get(pipeline_out.regime, pipeline_out.regime),
        "vol_ratio": pipeline_out.vol_ratio,
        "taiex_close": taiex_close,
        "taiex_above_ma": taiex_above_ma,
        "portfolio": portfolio_ctx,
        "drift_alert": drift.alert if drift else False,
        "drift_reason": drift.reason if drift else "",
        "force_paper": drift.force_paper if drift else False,
    }

    # overnight 資料直接來自 PipelineOutput（補充）
    # overnight 從 PipelineOutput 傳入的 OvernightReport
    if pipeline_out.overnight is not None:
        on = pipeline_out.overnight
        ctx["overnight"] = {
            "tsmc_adr_change_pct": on["tsmc_adr_change_pct"],
            "sox_change_pct": on["sox_change_pct"],
            "vix": on["vix"],
        }
    else:
        ctx["overnight"] = {"tsmc_adr_change_pct": 0.0, "sox_change_pct": 0.0, "vix": 0.0}

    tmpl = env.get_template("morning.md.j2")
    return tmpl.render(**ctx)


def save_and_print(report_md: str, report_date: date) -> Path:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = _LOGS_DIR / f"{report_date.isoformat()}.md"
    path.write_text(report_md, encoding="utf-8")
    # Windows console 可能是 cp950，強制以 utf-8 輸出避免 emoji 錯誤
    import sys
    sys.stdout.buffer.write((report_md + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()
    return path


