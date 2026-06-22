# 韭菜健檢 隱私政策

**最後更新**:2026-06-22

## 我們收集什麼資料

### 帳號資料(用戶主動提供)
- Email(用於登入)
- 密碼(經 Supabase Auth 加密儲存,我們看不到原文)

### 用戶生成資料(儲存在 Supabase RLS 加密 DB)
- 觀察清單 ticker
- 持股股數 / 成本 / 進場日期 / 筆記
- 個人化設定(手續費折抵率、晨報精選等)

**RLS(Row Level Security)保證:用戶 A 看不到用戶 B 的資料。**

### App 使用資料(本機,不上傳)
- session_state(瀏覽器內,關閉就消失)
- cookie(refresh_token 30 天保持登入)
- 個股健檢 cache(每用戶共用,加速體驗)

## 我們不收集什麼

- ❌ 不追蹤 GPS 位置
- ❌ 不收集聯絡人 / 通訊錄
- ❌ 不存取相片
- ❌ 不錄音 / 不存麥克風資料
- ❌ 不收集財務帳戶連結
- ❌ 不在第三方廣告平台追蹤

## 第三方服務(資料處理者)

| 服務 | 用途 | 資料種類 |
|------|------|---------|
| Supabase | 用戶帳號 + RLS 加密 DB | Email / 觀察清單 / 持股 |
| Streamlit Cloud | App 主機 | 連線記錄(無個資) |
| FinMind API | 股票歷史資料 | 公開市場資料(無個資) |
| yfinance API | 即時股價 | 公開市場資料(無個資) |
| Google Gemini API | AI 解讀 | 你的觀察清單 ticker(匿名,無 email) |
| Anthropic Claude API(可選) | AI 解讀 | 同上 |
| OpenAI API(可選) | AI 解讀 | 同上 |

所有 AI prompt 都不包含 email / 帳號識別碼。

## Cookie

- `leek_check_session`:保持登入(30 天有效,不含其他資訊)
- `streamlit-cookies-controller`:streamlit cookies 機制必備

## 資料保留期間

- 帳號:除非用戶要求刪除,否則保留
- 觀察清單 / 持股:同上
- Cookie:30 天

## 用戶權利

### 刪除帳號
- 點 App 內 **🚪 登出** → email 我們 → 24h 內全清

### 取得資料副本
- email 我們:`aaowobbowocc@gmail.com`(或你的聯絡 email)

### 撤回 AI 解讀
- App 內每個 AI 區塊都可選不展開,不送 prompt

## 兒童使用

韭菜健檢不針對 13 歲以下兒童設計。發現有未成年用戶會立即刪除帳號。

## 政策變更

重大變更會在 App 內推播通知 + email。

## 聯絡我們

aaowobbowocc@gmail.com

---

⚠️ **本 App 純為盤後分析工具,不構成任何投資建議。**
所有數據僅供參考,投資決策請自行判斷或諮詢專業顧問,盈虧自負。
