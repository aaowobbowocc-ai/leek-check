# Windows Task Scheduler 排程 — 每天自動 pre-compute

讓 `daily_precompute.ps1` 每天早上自動跑、commit、push,完全不用碰。

⚠️ **不要用 `.bat`**(中文 codepage 在 cmd 容易壞),用 `.ps1` PowerShell 版本。

## 一次性設定(5 分鐘)

1. 開 **Windows 工作排程器**(`Win+R` → 輸入 `taskschd.msc`)
2. 右側 **建立基本工作**
3. **名稱**:`韭菜健檢策略 precompute`
4. **觸發**:每天
5. **開始**:**06:00**(早於你 08:30 查房時段)
6. **動作**:啟動程式
7. **程式或 script**:
   ```
   powershell.exe
   ```
8. **新增引數**:
   ```
   -ExecutionPolicy Bypass -File "C:\Users\USER\Desktop\INVEST\scripts\daily_precompute.ps1" -Auto
   ```
   (`-Auto` 跑完就退出,不會卡 Read-Host)
9. **起始位置**:
   ```
   C:\Users\USER\Desktop\INVEST
   ```
10. **完成**

## 進階設定(右鍵工作 → 內容)

- **條件 → 喚醒電腦執行此工作**:✅ 勾起來(避免電腦睡著漏跑)
- **條件 → 只有在電腦使用交流電源時才執行此工作**:❌ 取消勾選(筆電也能跑)
- **觸發條件 → 編輯 → 進階**:
  - ✅ 「如果工作失敗,每 1 小時重試 3 次」(網路斷線 fallback)
  - ✅ 「如果未在排定時間執行此工作,將盡快執行」(關機過夜 fallback)

## 驗證已排程

工作排程器左邊 **工作排程器庫** → 找到 `韭菜健檢策略 precompute`:
- 右下 **狀態**:準備好
- **下一個執行時間**:明天 06:00

雙擊 → **歷程** tab 可看每次執行記錄。

## 手動測試

右鍵工作 → **執行**

或直接 PowerShell 跑:
```powershell
cd C:\Users\USER\Desktop\INVEST
.\scripts\daily_precompute.ps1
```

跑完看 `data/strategy_results.json` 的 `updated_at` 時間是不是剛剛。

## 暫停 / 移除

右鍵 → **停用** / **刪除**。

## 流程內容

1. **跑 Python `precompute_strategy_results.py`**(~30 秒)— 用本機 1.1GB cache 掃全市場 2351 檔,輸出 7 個策略的 hits 到 `data/strategy_results.json`
2. **Git add** strategy_results.json
3. **檢查** 有沒有變更 — 沒變化就跳過
4. **Commit + push** — Cloud 自動 rebuild,~2 分鐘策略市集更新

## 為什麼不用 .bat?

cmd 的中文 codepage(Big5 vs UTF-8)容易讓 .bat 內中文字元解析失敗,
偶爾整個 .bat 崩掉。PowerShell 處理 UTF-8 較穩,而且 `$LASTEXITCODE` 檢查更可靠。
