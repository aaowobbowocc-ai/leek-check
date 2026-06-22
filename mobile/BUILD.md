# 完整 Capacitor build 流程

## 0. 前置需求

| 平台 | 工具 | 安裝 |
|------|------|------|
| 通用 | Node.js 18+ | https://nodejs.org |
| 通用 | npm | 跟 Node.js 一起裝 |
| Android | Android Studio | https://developer.android.com/studio |
| Android | JDK 17+ | Android Studio 自動裝 |
| iOS | macOS 13+ | 需要 Mac |
| iOS | Xcode 15+ | Mac App Store |
| iOS | CocoaPods | `sudo gem install cocoapods` |

## 1. 第一次設定(20 分鐘)

```bash
cd C:\Users\USER\Desktop\INVEST\mobile

# 裝 npm dependencies
npm install

# 初始化 Capacitor(已經 init 過,不用再跑)
# npx cap init  ← 已經有 capacitor.config.json
```

## 2. Android 平台(1-2 小時 首次)

```bash
cd mobile

# 加 Android 平台 → 生成 android/ 目錄
npm run add:android

# 同步:複製 capacitor.config.json + www/ 到 android/
npm run sync

# 開 Android Studio
npm run open:android
```

Android Studio 開了之後:

1. 等 Gradle sync 完成(~5 分鐘第一次)
2. **Run → Run 'app'** → 選實機或模擬器
3. 看到韭菜健檢主畫面 = 成功

### 加 native features(凸顯 native value 避免 Apple 退件)

#### Push 通知(Capacitor Push)
```bash
npm install @capacitor/push-notifications
npx cap sync android
```

接 [Firebase Cloud Messaging](https://firebase.google.com/docs/cloud-messaging) 或 OneSignal。

#### Biometric 解鎖(Face ID / 指紋)
```bash
npm install @aparajita/capacitor-biometric-auth
npx cap sync android
```

## 3. iOS 平台(2-3 小時 首次,需要 Mac)

```bash
cd mobile

# 加 iOS 平台
npm run add:ios

# 同步
npm run sync

# 開 Xcode
npm run open:ios
```

Xcode 開了之後:

1. 上方裝置選 **「Any iOS Device」**
2. **Product → Archive**(會 build .ipa)
3. **Distribute App → App Store Connect → Upload**
4. 等 Apple 處理 ~10 分鐘
5. 進 App Store Connect 綁定 build 到 App 版本
6. **Submit for Review**

### iOS 必填 Info.plist

```xml
<key>NSUserTrackingUsageDescription</key>
<string>用於提供個人化分析,純本機處理</string>

<key>NSCameraUsageDescription</key>
<string>用於掃描股票代碼條碼</string>

<key>NSFaceIDUsageDescription</key>
<string>用於快速安全地解鎖 App</string>
```

## 4. App Store 上架資料

| 欄位 | 填法 |
|------|------|
| App 名稱 | 韭菜健檢 |
| Subtitle | 買進前先做一次韭菜健檢 |
| Category | Finance |
| Content Rights | I do not have rights to display third-party content |
| Price | Free |
| Availability | Taiwan + 其他繁體中文地區 |

### Description(中文)

```
韭菜不是命,是健檢不夠勤。

買進前先做一次韭菜健檢。4 面分析:
🩺 技術面 — KD / RSI / 均線排列
🩺 籌碼面 — 外資 / 投信 / 散戶比例
🩺 基本面 — 月營收 YoY / EPS / 殖利率
🩺 新聞面 — Google News 整合

⭐ 觀察清單追蹤 + 記帳功能(成本 / 損益 / 集中度警示)
📡 真 alpha 訊號偵測(memory 驗證過的策略)
🤖 智能個股健檢報告(白話 4 面解讀)
🌅 每日早晨個人化重點

⚠️ 純客觀數據工具,不報明牌、不喊飆股、不指示動作。
盤後分析工具,不適合盤中即時下單。
```

### Screenshots(必拍)

iPhone 6.7" (1290×2796):
1. 觀察清單卡片(含 4 檔範例)
2. 個股健檢頁(健檢分數 + 4 面細項)
3. 大盤 / 智能個人化早晨重點
4. 排行榜 / 策略市集
5. 記帳 portfolio 總覽

iPhone 6.5" (1242×2688)、5.5" (1242×2208) 也要拍。

## 5. Apple Guideline 4.2 對策(避免被退件)

App Store 對「純 WebView 殼」很嚴格。我們的 app 必須有以下 **native value**:

| Native feature | 解法 |
|---------------|------|
| Push 通知 | Capacitor Push + Firebase 接 alpha 訊號 |
| Biometric 解鎖 | Capacitor Biometric → 保護 portfolio 隱私 |
| 桌面 widget | Capacitor Widget(複雜,可後做) |
| 離線分頁 | PWA cache 已做,Capacitor 也支援 |
| Native share | Capacitor Share API |
| Status bar 樣式 | 已在 config 設好 |

實作 1-2 個就可過審。

## 6. 時程估算

| 階段 | 工時 |
|------|------|
| Android 第一次 build | 4-6 小時 |
| iOS 第一次 build | 6-8 小時(含 Mac 環境設定) |
| Native features 整合 | 1-2 天 |
| Screenshots / 上架資料 | 半天 |
| 送審 → 通過 | 1-7 天(Apple)/ 1-3 天(Google) |

## 7. 後續更新

每次推 GitHub → Streamlit Cloud 自動 rebuild → user 下次開 app 看到新版。
**App 殼本身只在功能架構變(加新 plugin / 改 native code)時才需重送審**。

簡單調整文字 / 修 bug / 加新策略 → user 開 app 就更新,無需重送。
