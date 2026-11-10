# 大焱资料管理系统 PDA 客户端（android_shell）说明文档

## 1. 概述

本工程是一个极简 Android WebView 壳，用来把内网的 BOM3 系统页面（`/material/inbound`）装进 APK 里，并**补上网页本身做不到的原生能力**：接收优博讯等 PDA 扫描头的 Intent 输出。

背景：扫描器使用「Intent 输出」时，条码是发在 Android Intent（广播 / 启动 Activity）里的，网页 JS 无法监听原生广播；通用打包工具（如 lw.Web2Android）没有原生桥，因此扫码永远不会生效。本壳在原生层接收扫描结果后调用网页里的约定函数，网页侧逻辑原样复用。

- 包名：`com.bom3.shell`
- 最低系统：Android 6.0（API 23），目标版本 API 34
- 当前版本：versionCode 4 / versionName 1.1.2
- 体积：约 280 KB

## 2. 功能特性

| 功能 | 说明 |
|---|---|
| Intent 扫码接收 | 动态注册 4 个常见扫描广播 Action，同时 Manifest 挂 `ACTION_DECODE_DATA`，广播/启动 Activity 两种输出方式都能收 |
| 条码字段兜底 | 优先 `barcode_string`，其次 `barcode`、`SCAN_BARCODE1`、`data` 等，最后遍历全部字符串 extra，兼容不同固件 |
| 扫码注入 | 收到条码后调用网页 `window.__bomPdaScan(条码)`，复用网页的 P/N 截取、去重、查询、核对面板 |
| 原生去重 | 800ms 内同码只处理一次，避免广播与 Activity 双通道重复触发 |
| 断点缓存 | 页面未加载完成时先暂存条码，`onPageFinished` 后自动补发 |
| 启动页 | 原生启动页（logo + 应用名 + 进度），页面加载完成后淡出，最少停留 700ms |
| 键盘模拟兜底 | 扫描器改回键盘模式也能用（网页侧有全局按键捕获，不依赖输入框焦点） |
| 文件下载 | 拦截下载链接走系统 DownloadManager，自动带登录 Cookie |
| 拍照识别 | `<input type="file">` 走系统图片选择/相机，供无摄像头的 http 环境使用 |
| 会话保持 | Cookie 持久化，切后台再回来不用重新登录 |
| 基础交互 | 返回键回退网页，实体菜单键修改服务器地址，作业期间不熄屏 |

## 3. 目录结构

```
android_shell/
├─ assets/
│  ├─ logo.png                 # 应用图标源图（任意尺寸，脚本自动补方形并生成各密度）
│  └─ splash.png               # 可选：启动页主图，没有则用 logo.png
├─ app/src/main/
│  ├─ AndroidManifest.xml      # 包名、权限、版本号、启动扫描 intent-filter
│  ├─ java/com/bom3/shell/
│  │  └─ MainActivity.java     # 全部逻辑：WebView、扫码接收、启动页、下载、地址配置
│  └─ res/
│     ├─ layout/splash.xml     # 启动页布局
│     ├─ values/strings.xml    # 应用名、启动页副标题
│     ├─ values/colors.xml     # 启动页配色
│     ├─ values/styles.xml     # 主题与窗口底色
│     ├─ mipmap-*/ic_launcher.png   # 构建时生成
│     └─ drawable/splash_image.png  # 构建时生成
├─ build.ps1                   # 一键构建脚本
├─ bom3.keystore               # 签名文件（首次构建生成，务必保留）
└─ dist/BOM3-PDA-1.1.2.apk     # 产物
```

## 4. 环境依赖

- Windows 10/11 + PowerShell 5.1
- JDK 8 及以上（本机为 JDK 21，需含 `javac`、`keytool`）
- Android SDK：
  - `platforms;android-34`（提供 `android.jar`）
  - `build-tools;35.0.0`（提供 `aapt2`、`d8`、`zipalign`、`apksigner`）

> 注意：`build-tools;34.0.0` 的 `d8` 在处理本工程匿名内部类时会抛空指针，必须用 35.0.0。

SDK 默认路径为 `%LOCALAPPDATA%\Android\sdk`；如装在别处，设置环境变量 `ANDROID_SDK_ROOT`。JDK 路径在 `build.ps1` 顶部的 `$env:JAVA_HOME` 处指定。

## 5. 构建

```powershell
powershell -ExecutionPolicy Bypass -File f:\TA\bom3\android_shell\build.ps1
```

脚本流程：生成图标/启动图 → `aapt2 compile` → `aapt2 link` → `javac` → `d8` → 写入 `classes.dex` → `zipalign` → `apksigner` 签名。

产物：`android_shell/dist/BOM3-PDA-<版本号>.apk`。

## 6. 安装

方式一：把 APK 拷到 PDA，用文件管理器打开安装（需允许「未知来源」）。

方式二：USB 连接后命令行安装（覆盖安装用 `-r`）：

```powershell
& "$env:LOCALAPPDATA\Android\platform-tools\adb.exe" install -r "f:\TA\bom3\android_shell\dist\BOM3-PDA-1.1.2.apk"
```

## 7. 首次启动与服务器地址

1. 打开「大焱资料管理系统」，首次启动弹出服务器地址输入框，默认 `http://218.59.244.81:55004/`。
2. 填 `http://<服务器IP>:5004/material/inbound` 后点「打开」。
3. 之后要改地址：按 PDA 实体**菜单键**（KEYCODE_MENU）；没有该键的机型可清除应用数据恢复首次引导。
4. 地址会自动补齐 `http://` 并去掉结尾多余斜杠。

## 8. 扫描器配置（优博讯 Intent 输出）

扫描设置 → 输出设置：

| 配置项 | 值 |
|---|---|
| 输出方式 | Intent 输出 |
| Action | `android.intent.ACTION_DECODE_DATA` |
| 键值名 / Extra Key | `barcode_string` |
| 应用包名（若有该栏） | `com.bom3.shell` |
| 扫描到输入框 / 键盘模拟 | 关闭（两种模式互斥，同时开会重复） |

若固件只支持「启动 Activity」方式，无需额外配置：APK 已声明 `ACTION_DECODE_DATA` 的 intent-filter，扫描会直接送到本页。

## 9. 自定义

| 想改什么 | 改哪里 |
|---|---|
| 软件名 | `res/values/strings.xml` 的 `app_name` |
| 启动页副标题 | `res/values/strings.xml` 的 `splash_subtitle` |
| 应用图标 | 替换 `assets/logo.png`（自动生成 48/72/96/144/192 五档） |
| 启动页主图 | 放 `assets/splash.png`（建议 ≥512×512），不提供则用 logo |
| 启动页配色 | `res/values/colors.xml` |
| 启动页排版/页脚 | `res/layout/splash.xml` |
| 默认服务器地址 | `MainActivity.java` 的 `DEFAULT_URL` |
| 版本号 | `AndroidManifest.xml` 的 `versionCode/versionName` + `build.ps1` 顶部同名变量（需同时改，保持一致） |

改完重新执行构建脚本即可。

## 10. 与网页的接口约定

APK 收到条码后执行：

```javascript
window.__bomPdaScan("条码内容")
```

网页侧需提供该函数；当前 `templates/material_inbound.html` 已实现（含占位队列，避免页面未加载完时丢码）。若页面未定义该函数，注入会安全失败，不会报错。

## 11. 常见问题

**扣扫描键完全没反应**

1. 确认扫描器输出方式是 Intent 输出，Action 与键值名和上表一致。
2. 用 adb 模拟一次广播，验证壳与网页链路：

   ```powershell
   adb shell am broadcast -a android.intent.ACTION_DECODE_DATA --es barcode_string "P91175-DE540Q5S26092385"
   ```

   页面能自动查出 `91175-DE540`，说明壳与网页正常，问题在扫描器配置（重点查「应用包名」是否填了 `com.bom3.shell`）。
3. 仍无反应时检查扫描器是否同时开了「键盘模拟」，可能被系统丢键。

**启动页一直转圈然后提示无法连接**

服务器地址或网络不通。确认 PDA 与服务器同一网段、服务已启动（端口 5004）、地址含 `/material/inbound`。

**Excel 下载没有文件**

下载走系统 DownloadManager，文件在 `Android/data/com.bom3.shell/files/Download/`，可通过通知栏点击打开。

**拍照识别只能选图，不能实时取景**

http 内网不属于浏览器安全上下文，WebView 不给摄像头权限，页面自动退化为「选图识别」。需要实时取景时给服务配 https。

**切后台再回来要重新登录**

确认为同一服务器地址；Cookie 在 `onPause` 时落盘，若仍失效可检查服务器会话过期时间。

## 12. 签名与升级

- 签名文件：`bom3.keystore`，别名 `bom3`，口令 `bom3shell`。
- **该文件必须长期保留**：丢失后新包签名不同，无法覆盖安装，只能卸载重装（本地数据会清空）。
- 升级必须保证 `versionCode` 递增，且包名、签名与旧版一致。

## 13. 安全说明

- 应用仅申请 `INTERNET`、`ACCESS_NETWORK_STATE` 两个普通权限。
- 因内网为 http，已开启 `usesCleartextTraffic`；若后续上 https 可关闭。
- 签名口令为构建期固定值，仅用于内部分发，请勿对外公开 keystore。
