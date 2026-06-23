# Android Signing Keystore

⚠️ **這個資料夾的 .jks 跟 .properties 絕對不能 commit 到 git**(已 gitignored)。
如果 lost,**Play Store 上的 App 就再也不能更新**(只能下架重建)。

## 立刻做的事

### 1. 備份 keystore 到 3 個安全位置
- ✅ Google Drive / iCloud(加密 zip)
- ✅ USB 隨身碟(離線備份)
- ✅ Email 給自己(加密附件)

要備份的兩個檔:
- `leek-check-upload.jks`
- `keystore.properties`(內含密碼)

### 2. 密碼存到密碼管理器
| 欄位 | 值 |
|------|------|
| Keystore password | `leek2026!check` |
| Key alias | `leek-check` |
| Key password | `leek2026!check` |
| Validity | 100 年(到 2126-06-23) |

存到 1Password / Bitwarden / LastPass / KeePass。

### 3. 記下 fingerprint(Google Play 上會用到)
| Type | Fingerprint |
|------|------|
| SHA1 | `D6:CE:7C:6D:00:FD:17:D0:30:FB:08:37:1E:C1:26:BC:56:8B:85:0F` |
| SHA256 | `6F:21:7B:AE:B2:97:35:A6:D2:95:75:74:82:8A:E0:19:B3:23:AF:F0:08:FA:87:06:E5:81:49:EC:25:B3:00:4C` |

## 自己 build Release AAB

```bash
cd C:\Users\USER\Desktop\INVEST\mobile\android
$env:ANDROID_HOME = "C:\Users\USER\AppData\Local\Android\Sdk"
$env:JAVA_HOME = "C:\Program Files\Zulu\zulu-17"
.\gradlew bundleRelease
```

AAB 輸出在:
```
app/build/outputs/bundle/release/app-release.aab
```

## 上傳到 Play Store

1. 登入 [Google Play Console](https://play.google.com/console)
2. 新增 App → 韭菜健檢
3. **Release** → **Production**(或先 **Internal testing**)
4. **Create new release** → 上傳 `app-release.aab`
5. **Play App Signing**(推薦開啟)— Google 接管 signing key,你只管 upload key
6. 填 Release notes(中文):
   ```
   v0.1 首發
   - 觀察清單 + 4 面健檢
   - 7 個真 alpha 策略掃描
   - 多用戶 + 隱私加密
   ```
7. 送審

## 如果換電腦

把 backup 的兩個檔放回:
```
C:\Users\USER\Desktop\INVEST\mobile\android\keystore\leek-check-upload.jks
C:\Users\USER\Desktop\INVEST\mobile\android\keystore\keystore.properties
```

build.gradle 會自動讀。
