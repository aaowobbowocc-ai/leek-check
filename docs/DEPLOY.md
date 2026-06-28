# 部署指南 — Render (Backend) + Vercel (Frontend)

## 🚀 一次性部署 (~30 分鐘)

### Step 1: Backend → Render

1. 去 [render.com](https://render.com) 註冊(GitHub 登入)
2. Dashboard → **New** → **Blueprint**
3. Connect repo: `aaowobbowocc-ai/leek-check`
4. Render 自動讀 `render.yaml` 建好服務
5. **填環境變數**(`.streamlit/secrets.toml` 同樣這幾個):
   - `GEMINI_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_KEY`
   - `DISCORD_ALERT_WEBHOOK`(選用)
6. Click **Apply** → 等 ~5 分鐘 build
7. 服務跑起 → 拿到 URL,例:`https://leek-check-api.onrender.com`

**Free tier 注意**:15 分沒人 ping 會 spin-down,首次 request 需 30s 喚醒。升級 Starter $7/mo 就常駐 + 1GB disk。

### Step 2: Frontend → Vercel

1. 去 [vercel.com](https://vercel.com) 註冊(GitHub 登入)
2. Dashboard → **New Project** → import `aaowobbowocc-ai/leek-check`
3. **Root Directory**: 選 `web`(重要)
4. Framework Preset 自動偵測 Next.js
5. **填環境變數**:
   ```
   NEXT_PUBLIC_API_URL = https://leek-check-api.onrender.com
   NEXT_PUBLIC_SUPABASE_URL = https://wzeoxwzxrayhxefwsxdd.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY = <你的 anon key>
   ```
6. **Deploy** → 1-2 分鐘 → 拿到 URL 例:`https://leek-check.vercel.app`

### Step 3: 驗證

```bash
curl https://leek-check-api.onrender.com/healthz
# → {"status":"ok"}
```

打開 https://leek-check.vercel.app → 韭菜健檢首頁。

---

## 📱 之後 Capacitor v2 APK 重包

`mobile/capacitor.config.json` 改:
```json
{
  "server": {
    "url": "https://leek-check.vercel.app",
    "allowNavigation": ["*.vercel.app", "*.supabase.co"]
  }
}
```
然後 `npm run build && npx cap sync android && cd android && ./gradlew bundleRelease`。

---

## 💸 月成本估算

| 服務 | 費用 |
|------|------|
| Render Backend | $0 (Free, spin-down) 或 $7 (Starter) |
| Vercel Frontend | $0 (Hobby tier 充足) |
| Supabase | $0 (Free 500MB DB) |
| Gemini API | $0 (Free 1500/day,夠用) |
| **總計** | **$0 / 月** 或 $7 (推薦) |

---

## 🔄 之後 push 怎麼觸發部署

- 你 `git push origin master`
- Vercel + Render 都自動偵測,各自重 build
- 約 5-7 分鐘後新版上線
- 不用手動點按鈕

---

## 🐛 常見問題

**Q: Render 一直 build fail?**
A: 看 build log,通常是 requirements.txt 某 package 版本衝突。

**Q: Frontend 連不到 backend?**
A: 檢查 backend 是否 spin-down(免費版 15min 無流量會睡)。
   也檢查 Vercel `NEXT_PUBLIC_API_URL` 環境變數是否設對。

**Q: Supabase RLS 拒絕請求?**
A: 確認 frontend 用 `NEXT_PUBLIC_SUPABASE_ANON_KEY`(不是 service_role)。
   service_role 只給 backend 用,千萬別讓前端拿到。
