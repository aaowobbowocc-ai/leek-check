# 🩺 韭菜健檢 — Web Frontend (Next.js 15)

正在進行的 v2 前端重寫,取代 Streamlit 版本以獲得 native-app 體感。

## Stack

- Next.js 15 App Router + TypeScript + Turbopack
- Tailwind CSS 3 + shadcn-style 元件
- Supabase auth(沿用既有後端)
- TanStack Query + Zustand
- Framer Motion(頁面切換 + tab animation)
- 之後用 Capacitor 6 包成 App

## 開發

```bash
cd web
npm install
cp .env.example .env.local  # 填 Supabase URL + anon key
npm run dev
# 開 http://localhost:3000
```

## Capacitor 打包(之後)

```bash
NEXT_BUILD_MODE=capacitor npm run build
# 產出 out/ 給 Capacitor copy 到 mobile/www/
```

## 路徑

- `/login` — 訪客 / Email / Google 三入口
- `/auth/callback` — Google OAuth 回流
- `/` — 主畫面(bottom-tab navigation)

## TODO(Week 1)

- [x] Day 1-2: Scaffold + 登入頁
- [ ] Day 3: FastAPI 後端骨架
- [ ] Day 4: 觀察清單 UI
- [ ] Day 5: 4 面健檢頁
- [ ] Day 6: Vercel + Render 部署
- [ ] Day 7: Capacitor 重包 APK
