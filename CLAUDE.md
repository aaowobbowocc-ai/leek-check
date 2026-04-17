# INVEST 專案 — Claude 行為準則

## 模型選用規則

**派 subagent 時自動選對 model：**
- `model: "sonnet"` — 資料搜尋、文件閱讀、爬蟲、格式轉換、簡單腳本、Explore 型任務
- `model: "opus"` — 架構設計、策略邏輯、風控公式、回測引擎、Plan 型任務、code review

**主動提醒切換（開工前先看 model 對不對）：**
- 每次收到任務時，先判斷所需 model，**若與目前使用的 model 不符**，開口第一件事就說：「目前是 `opus/sonnet`，這個任務建議切 `/model sonnet/opus`，要我繼續嗎？」等確認後再動工。
- 遇到複雜架構任務（新模組設計、因子邏輯、Walk-Forward 演算法）時，開工前提示：「建議切 `/model opus` 再繼續，這段需要深度推理。」
- 任務完成後提示：「已完成，可切回 `/model sonnet` 節省成本。」

## 每次完成程式碼後執行 Code Review（CODEX 檢查）

任何新增或修改程式碼完成後，自動進行以下檢查（不需使用者提醒）：

1. **Look-ahead bias 檢查**：凡是在回測路徑（`src/backtest/`）的程式碼，確認任何資料讀取都有 `date < simulated_today` 過濾，否則標 `# BUG: look-ahead bias`
2. **交易成本檢查**：模擬進出場是否扣除手續費 + 0.3% 證交稅 + 滑價
3. **型別安全**：`pydantic` 資料模型是否正確驗證輸入
4. **隱私保護**：`asset_manager.py` 任何印出金額的路徑是否有 `USER_UUID` 檢核
5. **測試覆蓋**：新模組是否在 `tests/` 有對應測試，若無則提示補上

## 專案架構速查

- **計畫書**：`~/.claude/plans/2026-iridescent-adleman.md`（Phase 0–10 建置順序）
- **設定**：`config/strategy.yaml`（因子權重、風控參數）
- **觀察清單**：`config/watchlist.yaml`
- **資產**：`data/assets.json`（.gitignore，不進版控）
- **晨報主入口**：`scripts/morning_briefing.py`
- **回測**：`src/backtest/engine.py`、`walk_forward.py`、`survival_check.py`

## 建置優先順序

嚴守 Phase 0 → 1 → 2 → … → 10 順序，不跳相依關係。
實盤前必須完成 Phase 10（paper trading 2–4 週，累積 ≥ 20 筆模擬）。
