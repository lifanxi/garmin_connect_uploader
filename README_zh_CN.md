# Garmin Connect Uploader

Garmin Connect Uploader（`gcu`）是一个跨平台的 GPS 轨迹上传工具，用于将轨迹同步到 Garmin Connect。它提供桌面 GUI 和 CLI，两者共用同一套同步引擎。

这个工具主要面向历史 GPS 轨迹归档：它会解析本地轨迹文件，转换为 FIT 活动，上传到 Garmin Connect，并通过稳定 Token 避免重复上传。

## 功能

- 基于 PySide6 的桌面 GUI。
- 用于脚本、批量同步、格式转换、清理和诊断的 CLI。
- 支持 Columbus CSV、GPX 和 NMEA RMC 输入格式。
- 生成带有稳定工具设备签名的 FIT 活动。
- 通过稳定的 `[gcu:v1:...]` 活动名称 Token 检测重复。
- 通过时间、时长和坐标匹配历史重复活动。
- 本地预检查重复文件、重叠点、冲突点和解析错误。
- GUI 中按文件展示任务状态：上传、跳过、补填 Token、冲突、排队中、上传中和完成。
- 只清理由本工具上传的活动。
- 根据轨迹坐标自动解析展示时区和城市。
- 可构建包含 Python 运行时和依赖的 Windows 安装包。

## 支持的轨迹格式

### Columbus CSV

示例：

```text
INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
1,T,251201,000001,30.0000010N,120.0000010E,1,3.6,80
```

CSV 时间戳默认按 UTC 解析。

### NMEA RMC

支持包含 `$GPRMC` 或 `$GNRMC` 语句的原始文本文件。常见扩展名包括 `.txt`、`.nmea` 和 `.log`。NMEA 时间戳按 UTC 解析。

### GPX

支持标准 GPX 轨迹文件（`.gpx`、`.GPX`），解析 `trk/trkseg/trkpt` 点中的 `lat`、`lon`、`ele` 和 `time` 字段。使用 `Z` 或显式时区偏移的 GPX 时间戳会按 UTC 解析。

## 去重和安全规则

上传后的活动名称包含稳定 Token：

```text
Hangzhou Track Me [gcu:v1:ec3a118bea0021bf]
```

Token 匹配优先。如果远端活动已经包含相同 Token，本地文件会被跳过。

对于没有 Token 的历史活动，工具可以通过时间戳、时长和坐标进行匹配。如果匹配到的远端活动是由本工具上传的，活动名称会更新为当前标题格式。如果它由其它工具上传，则只会追加或替换 Token。

生成的 FIT 文件使用以下设备签名：

```text
manufacturer = HOLUX
deviceId = 0x12345678
```

清理以及对“识别为本工具上传”的活动进行编辑等破坏性或修改性操作，都会在修改远端活动之前检查这个签名。清理操作不会删除未签名活动。

## 开发环境安装

推荐使用 Python 3.11 或更新版本。

macOS/Linux：

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

## 桌面 GUI

启动 GUI：

```bash
python gcu_gui.py
```

GUI 是一个单窗口流程：

- 登录区域在顶部。
- 文件管理和检查/上传操作在中间。
- 清理功能在底部。
- 窗口底部显示共享状态日志。

启动时，GUI 会检查当前工作目录下的 `.garth_session`。如果存在有效的 Garmin 会话，登录区域会保持锁定，文件和清理操作会启用。如果没有有效会话，文件和清理操作会保持禁用，直到登录成功。

站点选择器支持：

- 国际站：`garmin.com`
- 中国站：`garmin.cn`

在简体中文系统区域设置下，默认选择中国站。其它语言环境默认选择国际站。登录成功后，选中的站点会保存在：

```text
.garth_session/gcu_account.json
```

同一个文件也会保存登录用户名提示，用于更友好地展示账号信息。退出登录会删除 `.garth_session`。

### GUI 文件流程

使用 **添加文件** 或 **添加文件夹** 添加轨迹。选择的文件夹包含子目录时，GUI 会询问是否递归添加文件。

使用 **检查** 执行：

1. 本地预检查。
2. 逐文件解析元数据。
3. 查询远端重复活动并生成任务计划。

如果本地预检查发现问题，受影响的文件会直接在表格中标记。其它有效文件仍会继续进入任务计划。

使用 **运行** 只处理状态可运行的行，例如“上传”或“补填 Token”。运行过程是串行的：一个文件会先完成上传、匹配活动 ID、打标和完成状态，然后才处理下一个文件。运行过程中按钮会变为 **中断运行**。中断会等待当前文件处理完成，然后退出剩余队列。

使用 **清理已完成任务** 可以从表格中移除已完成或已跳过的行，同时保留有问题的行供用户查看。

使用 **清理已上传轨迹** 删除由本工具上传的远端活动。GUI 会先在确认对话框中预览匹配活动，删除前需要输入 `DELETE`。

## CLI 用法

CLI 入口是：

```bash
python gcu_cli.py <command>
```

Windows 打包版 CLI 是：

```powershell
gcu.exe <command>
```

CLI 会自行展开 glob 模式，因此 `tracks/*.CSV` 在 Windows shell 和 macOS/Linux shell 中都可以工作。

### 检查本地文件

```bash
python gcu_cli.py inspect tracks/*.CSV
python gcu_cli.py inspect tracks/*.gpx
python gcu_cli.py inspect 080323-UnixCenter.txt --json
```

### 批量预检查

预检查会报告重复轨迹文件、重叠点、冲突点和解析错误。它不会上传或修改任何内容。

```bash
python gcu_cli.py pre-check tracks/*.CSV
python gcu_cli.py pre-check tracks/*.CSV --json
```

### 转换为 FIT

```bash
python gcu_cli.py convert input.CSV --output output.fit
python gcu_cli.py convert tracks/*.CSV --output-dir out-fit
```

### 认证

```bash
python gcu_cli.py auth login --domain garmin.cn
python gcu_cli.py auth status --domain garmin.cn --json
```

CLI 会话默认使用 garth 的默认会话目录，除非传入 `--session-dir`。GUI 永远使用当前目录下的 `.garth_session`。

凭证可以通过命令行选项或环境变量传入：

```bash
export GARMIN_USERNAME="name@example.com"
export GARMIN_PASSWORD="..."
python gcu_cli.py auth login --domain garmin.com
```

### 同步

只预览决策，不上传：

```bash
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn --dry-run
```

上传并标记活动：

```bash
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn
```

不查询 Garmin，执行离线任务计划：

```bash
python gcu_cli.py sync tracks/*.CSV --dry-run --offline
```

常用同步选项：

```bash
--keep-fit
--output-dir fit-output
--post-upload-max-wait-s 180
--post-upload-wait-base-s 30
--post-upload-wait-per-1000-points-s 5
```

上传后的等待时间会根据点数估算，并受 `--post-upload-max-wait-s` 限制。

### 补填 Token

```bash
python gcu_cli.py backfill tracks/*.CSV --domain garmin.cn --dry-run
python gcu_cli.py backfill tracks/*.CSV --domain garmin.cn
```

补填会查找匹配的远端活动，并在活动名称中添加或替换稳定 Token。

### 清理由本工具上传的活动

预览：

```bash
python gcu_cli.py purge --domain garmin.cn --dry-run
```

删除匹配的已签名活动：

```bash
python gcu_cli.py purge --domain garmin.cn --yes
```

限制日期范围或使用更小扫描窗口：

```bash
python gcu_cli.py purge --dry-run --start-date 2025-01-01 --end-date 2026-07-01
python gcu_cli.py purge --yes --chunk-days 31
```

## 命名、时区和城市

默认活动名称使用：

```text
<City> Track Me [gcu:v1:<token>]
```

如果无法解析城市，基础名称为：

```text
Track Me [gcu:v1:<token>]
```

使用 `--name-template` 可以覆盖基础命名规则。

源文件时间戳默认按 UTC 解析。使用 `--display-timezone auto` 时，会根据坐标解析面向人类展示的时区。如果自动解析无法决定，默认回退到 `Asia/Shanghai`，除非显式覆盖。

城市解析是离线完成的。对于一个轨迹，解析器会先比较起点和终点城市。如果两者不同，会逐步采样中点分段，直到某个城市获得多数。如果所有采样点都无法分出胜负，则使用起点城市。

常用格式选项：

```bash
--format auto
--timezone UTC
--display-timezone auto
--display-timezone-fallback Asia/Shanghai
--display-city auto
--display-city-min-population 300000
--name-template "{city} Track Me"
```

## Garmin HTTP 详细日志

设置 `GCU_GARMIN_VERBOSE_HTTP=1` 可以记录 Garmin HTTP 请求和响应。

Windows 默认日志路径：

```text
%LOCALAPPDATA%\GarminConnectUploader\garmin-connect-http.log
```

macOS/Linux 默认日志路径：

```text
garmin-connect-http.log
```

覆盖日志路径：

```bash
GCU_GARMIN_VERBOSE_HTTP=1 GCU_GARMIN_VERBOSE_HTTP_LOG=/tmp/gcu-http.log python gcu_gui.py
```

详细日志包含 URL、Method、Headers 和 Body。它可能包含 Garmin 凭证、Cookie、Bearer Token 和上传的 FIT 数据。只应在本地调试时启用，不要分享这个文件。

## Windows 安装包

Windows 安装包会打包 Python 和所有依赖。最终用户不需要提前安装 Python。

在 Windows 上构建：

```powershell
winget install JRSoftware.InnoSetup
.\scripts\build_windows_installer.ps1 -Version 0.1.0
```

构建脚本也会检测安装在当前用户目录下的 Inno Setup，例如：

```text
%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
```

输出文件：

```text
dist\installer\GarminConnectUploader-0.1.0-Setup.exe
```

安装包包含：

- `GarminConnectUploader.exe`，GUI。
- `gcu.exe`，CLI。
- Python 运行时和收集到的 Python 依赖。

### 代码签名

使用 Authenticode 证书给打包后的可执行文件和安装包签名：

```powershell
.\scripts\build_windows_installer.ps1 -Version 0.1.0 -Sign -CertThumbprint "<SHA1_THUMBPRINT>"
```

或使用 PFX：

```powershell
.\scripts\build_windows_installer.ps1 -Version 0.1.0 -PfxPath ".\codesign.pfx" -PfxPassword "<password>"
```

## 测试

运行测试套件：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## 说明

- Garmin Connect 是外部服务，行为可能随时变化。
- 项目使用 `garth` 进行 Garmin 认证和 API 访问。
- 应用层会覆盖 garth 默认的 User-Agent，包括 Garmin API 请求和 SSO 页面请求。
- 请保护好 `.garth_session`。它包含可复用的 Garmin 会话 Token。
