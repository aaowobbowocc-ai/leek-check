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

## 資料源授權

- **FinMind 方案：Sponsor（NT$999/月）** — **將於 2026-06 停訂**
- **訂閱結束前已 backfill 完整 5 年歷史資料**（2026-05-03）：
  - 法人買賣超 1988 檔 ✓
  - 大戶持股分級（散戶比例）2437 檔 ✓
  - 月營收 2019 檔 ✓
  - 財報 2396 檔 ✓
  - PER/PBR 2042 檔 ✓
  - 融資融券 1988 檔（補完中）
  - 外資 daily 持股 1988 檔（補完中）
  - 期貨法人未平倉 23K rows ✓（TX/MTX/TE/TF × 三大法人）
  - 期貨日 OHLC 136K rows ✓
  - **政府基金買賣超 13.3M rows / 209MB**（2021-06 起，八大行庫）
- 停訂後仍可：跑歷史 backtest，所有 cache 永久保存於 `data/cache/finmind/`
- 停訂後不可：取得最新（每日）資料更新 — 若需 daily update，需臨時訂閱 1 個月
- 仍無：內盤比（任何方案都沒有，改用 OHLC `(close-low)/(high-low)` 替代）

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

## 成本基數計算規則（Phase 10 起效）

**原則：稅金跟手續費要算在股票成本裡，影響損益計算**

- **買進成本**：`cost_incl_fee = cost × (1 + 0.001425)`
  - 成本基數 = 執行價 + 0.1425% 手續費（華南永昌主券商，永豐金用於 Shioaji API；兩家手續費結構相同：當下扣全額，月底退 70%）
  - 儲存在 `assets.json` 每檔持股的 `cost_incl_fee` 欄位
  - 向下相容：如果 `cost_incl_fee` 缺失，fallback 到 `cost`

- **損益計算**（gross 慣例，跟券商顯示一致）：
  ```python
  cost_total = shares * cost_incl_fee    # 成本基數（含買進 0.1425% 手續費）
  mv = shares * price                    # 市值（不扣賣出費用）
  pnl = mv - cost_total                  # 未實現損益
  pct = (price / cost_incl_fee - 1) * 100 if cost_incl_fee > 0 else 0.0
  ```

- **實作位置**（2 處需保持一致）：
  - `scripts/web_dashboard.py`（手機版 Streamlit）
  - `scripts/dashboard_gui.py`（電腦版 GUI）

- **2026-05-07 變更歷程**：
  - 早上嘗試改 net (扣賣出 0.4-0.6%)，user 比對券商發現對不上 → revert
  - **結論：用 gross 慣例，跟券商一致**。記住實際 exit net 會少 ~0.5%。
  - 來回真實成本：ETF ~0.34%, 個股 ~0.585%（決策時心算扣掉）

## 紙交易標籤規則（Phase 10 起效）

**原則：[Paper] 前綴區分實盤 vs 紙交易推薦**

所有模型信號輸出加 `[Paper]` 標籤，避免使用者誤認為是實盤交易：

- **晨報部分**：
  - `## 🎯 [Paper] ORB Paper Trade 訊號`（策略_signals_section.py:48）
  - `## 📡 [Paper] 法人訊號（真 alpha 驗證後）`（strategy_signals_section.py:86）
  - `## 🔔 [Paper] 異常量能預警（吃貨期候選）`（volume_anomaly_scanner.py:339）

- **Discord 通知**：
  - `[Paper] 新訊號: ...` 詳見 `unified_paper_ledger.py:push_triggers_to_discord()`
  - `[Paper] 平倉結果: ...` 詳見 `unified_paper_ledger.py:close_of_day_summary()`

- **目標**：確保醫學生看到任何建議時，瞬間知道是紙交易驗證，不是實盤下單

## 四項高ROI項目完成（2026-05-02）

**執行結果：3 項完成 + 1 項放棄**

### ✅ 已完成項目

1. **DXJ 日股 DCA Timing**（1.5h）
   - 檔案：`src/report/strategy_signals_section.py:304-357`
   - 晨報新增「DXJ DCA Timing」section，每日檢查 SPY 跌幅 + USD/JPY 變動
   - 觸發條件：SPY 30d 跌>5% / SPY 90d 跌>10% / JPY 30d 變>5%
   - 預期 alpha：+3.27~5.22%（90日hold）

2. **配對交易 Spread 監控**（1.5h）
   - 檔案：`src/report/strategy_signals_section.py:268-275`
   - 補完 6 對 Tier A（DRAM + 重電 + 半導體 2 對 + 航運 + 塑化）
   - 晨報每日計算 spread z-score，|z| > 2.5 觸發進場建議
   - 預期 alpha：+1.12~3.16%/筆

3. **妖股多因子篩選 S1+S3**（已完全實作，0 工程量）
   - 檔案：`scripts/daily_signal_scanner.py:123-158`
   - 邏輯已實作：散戶<20% 分位 AND 量爆 z>=2.5（排除大型權值）
   - 自動排程：`scripts/run_paper_ledger.bat`
   - 預期 alpha：+8.13pp（中小股）

### ❌ 放棄項目

- **散戶% exit filter**：實測失敗（-5.87~-19.59pp）
  - 原因：砍掉一些正在賺的交易，portfolio 反而退化
  - 結論：「trade-level lift +11.3pp」≠「portfolio alpha」（V2 framework dilution）

### 依賴項（需人工操作）

- **配對交易實交易**：需開啟永豐 Shioaji + 信用帳戶（待後續）
- **Phase 10 paper trading**：繼續累積，目標 n=20/20

## 集中度 + DCA Gate + Crash Hedge 整合（2026-05-03）

**新模組：** `src/report/concentration_advisor.py`，已整合到 morning_briefing.py

### 核心原則：regime-aware（不機械式調整）

決策邏輯**根據市場 regime 動態調整**，避免在錯誤時點做錯誤動作。

### 1. DCA Gate 規則（優先序：halted > paused > accelerated > normal）

| 條件 | 模式 | DCA 倍率 | 動作 |
|---|---|---|---|
| VIX > 30 | `HALTED` 🔴 | 0.0x | 暫停所有 DCA |
| TAIEX 距 MA200 > +30% | `PAUSED` 🟡 | 0.0x | 暫停大盤 DCA，僅 EWY 可繼續 |
| TAIEX 距 MA60 < -5% | `ACCELERATED` 🟢⬆️ | 1.5x | 加速 DCA 1.5x |
| 其他 | `NORMAL` 🟢 | 1.0x | 正常 DCA |

**Why:** 在 +35% MA200 高位加速 DCA = 高位接刀，違反風控。

### 2. Crash Hedge 規則

進入：**VIX > 30 AND TAIEX 月跌 > 15%**（雙條件 AND）
- 動作：停止所有新倉、現金 ≥ 50%、等 VIX 從高點回落 30% 再進場

**Why:** 「任何縮倉都 net negative」（記憶教訓），所以 hedge **只用於極端情境**，不是預測式縮倉。

### 3. 集中度建議（依 regime cycle 動態調整）

| Cycle | 集中度 > 30% | 建議 |
|---|---|---|
| `late_bull` | 是 | **減持為主**（嚴格降 30% / 折衷降 35%）|
| `mid_bull` / `early_bull` | 是 | **加買稀釋**（順勢，保留持股）|
| `bear` | 是 | **維持現金**（避險，不調整）|

### 當前狀態（2026-05-03）

- DCA Gate: `PAUSED`（TAIEX 距 MA200 +35.3%）
- Crash Hedge: 未觸發（VIX 17 / 月跌 < 15%）
- 2345 集中度警報：49% → 嚴格版減 12 股至 18 股 / 折衷版減 9 股至 21 股
