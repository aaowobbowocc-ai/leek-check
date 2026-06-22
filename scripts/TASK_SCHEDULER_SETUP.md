# Windows Task Scheduler 排程 — 每天自動 pre-compute

讓 `daily_precompute.bat` 每天早上自動跑、commit、push,完全不用碰。

## 一次性設定(5 分鐘)

1. 開 **Windows 工作排程器**(`Win+R` → 輸入 `taskschd.msc`)
2. 右側 **建立基本工作**
3. **名稱**:`韭菜健檢策略 precompute`
4. **觸發**:每天
5. **開始**:**07:30**(週一-週五早上,在你 08:30 查房前)
   - 或設更早,例如 **06:00**,讓 8 點看晨報時已完全更新
6. **動作**:啟動程式
7. **程式或 script**:
   ```
   C:\Users\USER\Desktop\INVEST\scripts\daily_precompute.bat
   ```
8. **新增引數**:
   ```
   auto
   ```
   (有 `auto` 跑完就退出,不會卡 `pause`)
9. **起始位置**:
   ```
   C:\Users\USER\Desktop\INVEST
   ```
10. **完成**

## 進階設定(右鍵工作 → 內容)

- **條件 → 唤醒電腦執行此工作**:勾起來(避免電腦睡著漏跑)
- **條件 → 只有在電腦使用交流電源時才執行此工作**:取消勾選(筆電也能跑)
- **觸發條件 → 編輯 → 進階**:勾「**如果工作失敗,每 1 小時重試 3 次**」(網路斷線 fallback)

## 驗證已排程

工作排程器左邊 **工作排程器庫** → 找到 `韭菜健檢策略 precompute`:
- 右下 **狀態**:準備好
- **下一個執行時間**:明天 06:00

雙擊 → **歷程** tab 可看每次執行記錄。

## 手動測試一次

右鍵工作 → **執行**

跑完看 `C:\Users\USER\Desktop\INVEST\data\strategy_results.json` 的 `updated_at` 時間是不是剛剛。

## 暫停 / 移除

右鍵 → **停用** / **刪除**。

## Alternative: 你不想用 Task Scheduler

直接雙擊 `scripts\daily_precompute.bat` 也行。手動操作,30 秒。

或開啟 PowerShell 後跑:
```powershell
cd C:\Users\USER\Desktop\INVEST
.\scripts\daily_precompute.bat
```
