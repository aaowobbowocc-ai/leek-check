# INVEST — 台股短線顧問機器人

每個交易日 08:30 產出 Markdown 晨報，涵蓋：
- ⚡ 三秒鐘決策區（推薦標的 / 買入區間 / 止損 / 目標）
- 大盤背景、TSMC ADR 夜盤、VIX
- 個股詳細評分（籌碼 / 族群動能 / 供應鏈 / 新聞情緒 / 技術 / 大盤）
- 資產總覽與資金配置建議

純顧問模式 — 不自動下單。

## 安裝

```bash
uv sync                         # 或 pip install -e .
cp config/.env.example config/.env
cp data/assets.json.example data/assets.json
# 編輯 .env 填入 API Key；編輯 assets.json 填入實際資產
```

## 每日手動執行

```bash
python scripts/morning_briefing.py
```

## 設定每日排程（週一至週五 08:30）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_windows_task.ps1
```

## 回測

```bash
python -m src.backtest.engine --start 2024-01-01 --end 2025-12-31 --tickers 3413
python -m src.backtest.walk_forward --start 2017-03-01
python -m src.backtest.survival_check
```

## 實作計畫

完整架構與 10 階段建置順序見 `~/.claude/plans/2026-iridescent-adleman.md`。

## ⚠️ 安全聲明

本專案為個人研究用途，**不構成任何投資建議**。實盤前務必完成 Phase 10（paper trading 2–4 週）。
