# 韭菜健檢 — Capacitor wrap

把 https://leek-check.streamlit.app 包成 native App,送 App Store / Play Store。

## 目錄結構

```
mobile/
├── capacitor.config.json    # Capacitor 設定(指向 Streamlit URL)
├── package.json              # npm dependencies + scripts
├── www/index.html            # 啟動 placeholder 頁(會 redirect 到 Streamlit URL)
├── README.md                 # 本檔
├── BUILD.md                  # 完整 build 流程(看這個)
└── PRIVACY.md                # 隱私政策模板(送審必備)
```

## 為什麼是「URL 殼」而不是真包?

Streamlit 是 server-side render,不能像 React 一樣 build static dist。
所以策略是 **WebView 殼指向已 deploy 的 leek-check.streamlit.app**。

優點:
- 推送更新只需 push GitHub → Cloud rebuild → user 下次開 app 就是新版
- 不用每次小改動都重送 App Store 審查
- 同步 PWA / Web / Native 所有平台

風險:
- Apple Guideline 4.2「不能是純 WebView shell」可能擋
- 對策:加 **native features**(Push / Face ID / Status bar)凸顯 native value

## 快速 build 順序

1. 看 [BUILD.md](BUILD.md) 完整流程
2. 需要的工具:Node.js 18+ / Android Studio / Xcode(Mac)
3. 預估工時:Android 1-2 天、iOS 2-3 天

## 不在這個 sprint 做(Phase 2)

- Capacitor Push 通知接 alpha 訊號
- Capacitor Biometric(Face ID 解鎖)
- 原生 Google Sign-In(取代被擋的 web OAuth)
- 桌面 widget(iOS / Android)
