# 韭菜健檢 部署 + 多 user 計畫

## 你需要做的(一次性,30 分鐘)

### 1. GitHub CLI 登入
```bash
gh auth login
```
選 GitHub.com → HTTPS → 用瀏覽器授權

### 2. Supabase 帳號(免費 tier)
1. 到 [supabase.com](https://supabase.com),用 GitHub 登入
2. 建新 project:
   - Name: `leek-check`
   - Database password: 隨機產生並存好
   - Region: `Northeast Asia (Tokyo)` (最近台灣)
3. 等 ~1 分鐘 provisioning
4. 進 project → Settings → API,記下:
   - **Project URL**(`https://xxx.supabase.co`)
   - **anon public key**(很長的 JWT)

### 3. 把 secrets 給我(或自己貼進 .streamlit/secrets.toml)
```toml
SUPABASE_URL = "https://xxx.supabase.co"
SUPABASE_ANON_KEY = "eyJxxx..."
GEMINI_API_KEY = "AQ.xxx..."
```

---

## 我會做的(2-3 天)

### Day 1:Supabase 基礎建設
- [ ] 建 tables(SQL 在 `docs/supabase_schema.sql`)
- [ ] 設 RLS(Row Level Security)— 確保 user A 看不到 user B 的資料
- [ ] 寫 `src/db.py` 取代現有的 `load_json` / `save_json`

### Day 2:Auth 整合
- [ ] 加 `src/auth.py`(login / signup / magic link)
- [ ] 改 `app.py` 頂部:未登入 → 強制 login page
- [ ] `session_state.user_id` 流入所有 watchlist / holding / alert 操作

### Day 3:Deploy
- [ ] Push 到 GitHub private repo
- [ ] Streamlit Cloud connect repo + 設 secrets
- [ ] 真實 device 測 PWA mode
- [ ] 拉 5 個朋友測試

---

## Phase 2 後續(Capacitor wrap)

完成多 user 之後:
1. Capacitor wrap WebView 指向 Streamlit Cloud URL
2. 加 native features(push / Face ID / status bar)
3. App Store / Play Store 送審

---

## 成本估算

| 項目 | 月費 |
|------|------|
| Streamlit Cloud(免費 tier) | NT$ 0 |
| Supabase(免費 tier — 50K MAU, 500MB DB) | NT$ 0 |
| Apple Developer(已有) | NT$ 0 |
| Google Play(已有) | NT$ 0 |
| **MVP 月成本** | **NT$ 0** |

當用戶 > 1000 / DB > 500MB → Supabase Pro $25/月(NT$ 800)
