# Garmin Connect Uploader 重构设计

## 背景

当前工具用于把 GPS 轨迹文件上传到 Garmin Connect。仓库中存在 GUI、CLI、CSV 转 FIT、上传测试脚本等多套实现，但核心逻辑耦合较重：

- GUI 直接调用转换和上传函数，不能作为 CLI 的纯封装。
- CLI 中仍有占位上传实现，和真实上传路径不一致。
- CSV 解析、轨迹规范化、FIT 生成、Garmin 登录、上传、上传后活动定位、活动重命名混在同一模块中。
- 上传去重主要依赖 Garmin HTTP 409 或上传后再查询活动，重复运行批量任务时无法稳定预判。
- 目前强绑定 Columbus P-1 M2 CSV 格式，不利于后续支持 GPX、TCX、FIT、NMEA、更多设备 CSV 等格式。

本次重构应视为全新实现：不复用现有代码结构和框架，只吸收现有行为经验、Garmin API 探索结果和去重设计。

## 目标

1. 前后端分离。
   - CLI 是完整、可自动化、可脚本化的一等入口。
   - GUI 只是 CLI 或后端应用服务的封装，不包含业务规则。
2. 全部重写。
   - 重新设计包结构、数据模型、流程编排、错误处理和测试。
   - 不沿用当前 tkinter 直接调用脚本函数的架构。
3. 上传自动去重。
   - 优先在上传前判断远端是否已经存在同一条轨迹。
   - 去重状态不依赖本地数据库或上传历史文件。
   - 可为历史已上传活动回填去重标记。
4. 预留更多轨迹文件格式支持。
   - 解析层使用格式适配器。
   - 后续可添加 GPX、TCX、FIT、NMEA、不同设备 CSV，而不改上传流程。
5. 可测试、可观测、可恢复。
   - 核心逻辑可脱离 GUI 和真实 Garmin 网络调用测试。
   - 每个文件输出明确决策：上传、跳过、冲突、失败、已回填。

## 非目标

- 不实现本地长期数据库、缓存或上传台账作为去重依据。
- 不把源文件名作为唯一身份。
- 不只依赖 Garmin Connect 的 HTTP 409 冲突响应。
- 不要求用户在 Garmin Connect 页面中手工维护活动备注。
- 不在第一阶段实现所有轨迹格式，只设计可扩展边界并实现 Columbus CSV 和
  NMEA RMC。

## 总体架构

采用分层架构：

```text
GUI / TUI / automation
        |
        v
CLI command layer
        |
        v
Application service layer
        |
        +-- Track import layer
        +-- Normalization and fingerprint layer
        +-- Export/render layer
        +-- Garmin Connect gateway
        +-- Sync planner and executor
```

建议项目结构：

```text
gcu/
  cli/
    main.py
    commands.py
    output.py
  app/
    sync_service.py
    backfill_service.py
    convert_service.py
    models.py
    events.py
  formats/
    base.py
    columbus_csv.py
    gpx.py
    tcx.py
    fit_import.py
  export/
    fit_writer.py
  garmin/
    client.py
    auth.py
    activities.py
    upload.py
  duplicate/
    fingerprint.py
    remote_index.py
    matcher.py
  gui/
    main.py
    cli_bridge.py
  tests/
```

核心约束：

- `gcu.app` 不依赖 GUI。
- `gcu.gui` 不实现上传规则，只调用 CLI 或应用服务。
- `gcu.formats` 只负责把不同源格式读成统一轨迹模型。
- `gcu.export` 只负责把统一轨迹模型写成 Garmin 可接受的上传文件。
- `gcu.garmin` 隔离所有 Garmin Connect 和 `garth` 细节。
- `gcu.duplicate` 隔离指纹、远端索引、旧活动匹配和冲突判定。

## 数据模型

统一轨迹模型是整个重构的核心。所有输入格式都先转换为同一组领域对象：

```text
TrackFile
  source_path
  source_format
  track: Track

Track
  points: list[TrackPoint]
  metadata: TrackMetadata

TrackPoint
  timestamp_utc
  latitude
  longitude
  altitude_m?
  speed_mps?
  heading_deg?
  accuracy_m?
  raw_extensions?

TrackMetadata
  start_time_utc
  end_time_utc
  duration_s
  point_count
  start_latitude
  start_longitude
  end_latitude
  end_longitude
  display_name
  source_device?
```

解析器必须保证：

- 时间统一为 timezone-aware UTC。
- 经纬度统一为十进制度。
- 速度统一为 m/s。
- 高度统一为米。
- 轨迹点按时间排序。
- 无效点以结构化警告返回，而不是散落 `print`。

## 格式适配器

定义统一接口：

```text
TrackReader
  can_read(path) -> bool
  read(path, options) -> TrackFile
  format_id -> str
```

第一阶段实现：

- `columbus_csv`
  - 支持当前 Columbus P-1 M2 CSV。
  - 字段：`INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING`。
  - 默认源时区为 `UTC`，可通过 CLI 参数覆盖。
  - 默认展示时区为 `auto`，用于活动名、报告和 GUI 展示。
  - `auto` 展示时区按轨迹开始后 5 分钟内的坐标抽样，用多数坐标所在
    IANA 时区决定；无法判断时回退到 `Asia/Shanghai`。
  - 默认展示城市为 `auto`，用于活动标题；从轨迹中间 5 分钟内的坐标抽样，
    通过离线城市库查最近主要城市并多数票决定。城市名使用拼音或英文。
    默认只考虑人口不少于 300,000 的城市，避免把区镇或小地名写进标题。
  - 当前 Columbus CSV 的 `DATE`/`TIME` 字段按 UTC+0000 解释。

- `nmea-rmc`
  - 支持 `.txt`、`.nmea`、`.log` 中逐行 `$GPRMC` 或 `$GNRMC` 语句。
  - RMC 中的日期和时间按 UTC+0000 解释。
  - 速度从 knots 转换为 m/s，course over ground 写入 heading。
  - 若句子包含 NMEA checksum，必须校验通过；不含 checksum 的旧数据可按宽松模式解析。

后续适配器：

- `gpx`
- `tcx`
- `fit_import`
- `generic_csv`，通过字段映射配置支持常见导出文件。

上传流程不应该知道输入来自哪种格式，只处理统一 `Track`。

## CLI 设计

CLI 是主入口，支持本地预检查、同步、预览、转换、回填、清理和诊断。
当前仓库可通过 `python gcu_cli.py ...` 直接运行；安装为 console script 后可使用
等价的 `gcu ...`：

```bash
python gcu_cli.py pre-check tracks/*.CSV
python gcu_cli.py sync tracks/*.CSV
python gcu_cli.py sync tracks/*.CSV --dry-run
python gcu_cli.py sync tracks/*.CSV --domain garmin.cn
python gcu_cli.py sync tracks/*.CSV --timezone UTC
python gcu_cli.py sync tracks/*.CSV --display-timezone auto
python gcu_cli.py sync tracks/*.CSV --display-timezone Asia/Shanghai
python gcu_cli.py sync tracks/*.CSV --display-city auto
python gcu_cli.py sync tracks/*.CSV --display-city-min-population 300000
python gcu_cli.py sync tracks/*.CSV --format columbus-csv

python gcu_cli.py convert input.CSV --output output.fit
python gcu_cli.py inspect input.CSV
python gcu_cli.py backfill tracks/*.CSV
python gcu_cli.py purge --dry-run
python gcu_cli.py purge --yes
python gcu_cli.py auth login --domain garmin.cn
python gcu_cli.py auth status
```

`pre-check` 只检查本地文件并输出报告，不上传、不删除、不修改远端。检查内容包括：

- 完全相同轨迹文件，即规范化轨迹指纹 token 相同。
- 跨文件同一 timestamp 且坐标相同的重叠点。
- 跨文件同一 timestamp 但坐标不同的冲突点。

CLI 需要在应用层展开 `*`、`?`、`[]` 通配符，保证 Windows PowerShell/cmd 和
macOS/Linux shell 下的 `tracks/*.CSV` 参数行为一致。

`purge` 会扫描 Garmin Connect 活动列表，仅删除签名同时满足
`manufacturer=HOLUX` 和 `deviceId=0x12345678` 的活动。默认全时间范围扫描；
可用 `--start-date YYYY-MM-DD` 和 `--end-date YYYY-MM-DD` 缩小范围；内部按
`--chunk-days` 分段查询，默认约一年一段。该命令是破坏性操作，必须显式传入
`--yes`；预览使用 `--dry-run`。

CLI 输出模式：

- 默认人类可读表格。
- `--json` 输出机器可读事件流或结果数组。
- `--verbose` 输出 Garmin API 查询、匹配分数和警告。
- `--quiet` 只输出错误和最终摘要。

建议每个文件的最终状态使用固定枚举：

- `upload`
- `skip-token`
- `skip-legacy-match`
- `backfilled-token`
- `ambiguous`
- `upload-conflict`
- `failed`

## GUI 设计

GUI 是 CLI/应用服务的封装，不直接实现业务规则。

当前实现直接调用 `gcu.app` 应用服务，和 CLI 共享同一套模型、解析器、
同步服务和 Garmin 网关。

GUI 当前是单窗口布局：

- 顶部为登录区。启动时自动检查当前目录 `.garth_session`；有效则禁用登录
  输入、显示已登录用户名、把按钮切换为“退出登录”，并启用文件和清理区域；
  无效则只允许登录。退出登录会删除 `.garth_session`。
- 中部为文件区。添加文件或文件夹后，可执行 Inspect；Inspect 依次做本地
  pre-check、逐文件解析和 dry-run 规划，结果逐行更新。Run 执行真实同步。
  文件表格支持点击表头排序，Inspect 完成后默认按开始 UTC 排序。
- 底部为清理区。清理已上传轨迹会先 dry-run 预览所有满足 GCU 签名的远端
  活动，在带滚动表格的确认对话框中展示活动 ID、名称、开始 UTC 和自适应
  时长；用户必须输入 `DELETE` 才能确认删除。
- 最底部为共享状态日志，登录、检查、上传和清理都会输出带时间戳的进度。

GUI 功能边界：

- 选择文件或文件夹。
- 设置 Garmin 域名；session 固定保存在当前目录 `.garth_session`。
- 展示文件列表、解析结果、去重决策、上传进度、错误详情。
- 登录、退出登录和启动时会话状态检查。
- 清理本工具上传的远端活动，删除前必须先预览并二次确认。

GUI 不应该：

- 自己解析 CSV。
- 自己生成 FIT。
- 自己调用 Garmin API。
- 自己决定重复、冲突或重试策略。

## Garmin Connect 网关

使用 `garth` 作为 Garmin Connect 认证和 API 网关的底层库，但封装在 `gcu.garmin` 内部。

接口建议：

```text
GarminClient
  login(username, password)
  resume_session()
  save_session()
  upload_activity(file_path) -> UploadResult
  list_activities(start_date, end_date, page) -> list[RemoteActivity]
  update_activity_name(activity_id, name)
```

认证策略：

- 默认使用 `~/.garth` 或应用配置目录保存 OAuth token。
- GUI 固定使用当前工作目录下的 `.garth_session` 保存 OAuth token，并通过
  `.gitignore` 排除。
- 支持 `GARMIN_USERNAME` 和 `GARMIN_PASSWORD` 环境变量。
- 不再把明文账号密码保存到仓库目录。
- 支持 `garmin.cn` 和全球 Garmin Connect 域名参数。
- `auth status` 必须执行一次轻量 Garmin API 查询，不能只验证本地 token 文件存在。
- `auth login` 和 `auth status` 应返回当前 Garmin 用户资料；当前实现通过
  Garmin socialProfile API 读取 `user_name`，CLI 可用 `--json` 输出。

## GCU 上传签名

本工具生成的 FIT 文件必须写入稳定设备签名：

```text
manufacturer = HOLUX
deviceId = 0x12345678
```

Garmin 活动列表接口会返回：

```text
manufacturer = HOLUX
deviceId = 305419896
```

该签名是判断“这条远端活动是否由本工具生成”的精确标准。任何会修改远端
活动的操作，包括 backfill、追加或替换 `[gcu:v1:...]` token、迁移 token、
删除或未来的批量清理，都必须先校验远端活动满足该签名。签名不符合时，
只能报告失败或冲突，不能修改活动。

活动名中的 `[gcu:v1:...]` 是去重 token；它不是修改权限依据。即使某条非
GCU 活动名称中含有类似 token，也不得在签名不匹配时修改它。

## 去重标记

每条本地轨迹生成稳定指纹，并写入 Garmin Connect 活动元数据。

第一阶段推荐继续使用 `activityName` 内嵌 token：

```text
Nanjing Track Me [gcu:v1:9f2a6c4e81d3b7a0]
```

原因：

- 活动列表接口可低成本返回 `activityName`。
- 当前探索已验证可通过活动服务更新名称。
- 备注、描述等字段是否能由列表接口低成本返回仍需验证。

若后续确认 Garmin 活动列表可返回可写的备注字段，可把 token 迁移到备注字段，但必须保留读取旧 `activityName` token 的能力。

## 指纹算法

指纹必须来自规范化轨迹内容，而不是文件名或 FIT 文件字节。

标记格式：

```text
[gcu:v1:<digest>]
```

建议 digest 初始长度为 16 个十六进制字符。若后续需要降低碰撞风险，可扩展到 24 个字符并通过版本区分。

规范化输入：

```text
gcu-fingerprint-v1
record_count=<count>
first_timestamp_utc=<epoch_ms>
last_timestamp_utc=<epoch_ms>
first_lat=<decimal_degrees_7dp>
first_lon=<decimal_degrees_7dp>
last_lat=<decimal_degrees_7dp>
last_lon=<decimal_degrees_7dp>
rows_sha256=<sha256 of normalized points>
```

单点规范化：

```text
<epoch_ms>,<lat_7dp>,<lon_7dp>,<altitude_m>,<speed_mps_3dp>,<heading_deg>
```

规则：

- 只使用被解析器接受的有效点。
- 按 `timestamp_utc, latitude, longitude` 排序。
- 经纬度固定 7 位小数。
- 速度固定 3 位小数。
- 缺失高度、速度、航向使用空字段，不能用任意默认值伪造。
- 指纹版本号随规范化规则变化而变化。

## 远端索引

同步前，先为本批次本地文件计算：

- token。
- 起止时间。
- 起止坐标。
- 点数。
- 时长。
- 计划活动名。

然后按整个批次时间范围查询 Garmin 活动列表：

```text
/activitylist-service/activities/search/activities
```

查询窗口：

- 从本地最早轨迹开始日期到最晚轨迹开始日期。
- 两端各扩展一天，容忍时区边界。
- 分页读取直到没有更多结果。

建立远端索引：

```text
token -> RemoteActivity
date -> list[RemoteActivity]
```

`RemoteActivity` 至少包含：

- `activity_id`
- `activity_name`
- `begin_timestamp`
- `start_latitude`
- `start_longitude`
- `duration`
- `activity_type`
- `manufacturer`
- `deviceId`

## 同步决策流程

对每个本地文件执行：

1. 解析源文件为统一 `Track`。
2. 生成指纹 token 和计划活动名。
3. 按轨迹点数从大到小排序处理整批数据，让大文件更早开始上传和等待
   Garmin 后台处理。
4. 如果 token 已存在于远端索引：
   - 决策为 `skip-token`。
   - 不转换、不上传。
5. 如果 token 不存在，执行旧活动匹配：
   - 同一天或相邻一天。
   - 开始时间差小于 60 秒。
   - 起点坐标差小于 0.001 度。
   - 若远端有时长，则时长差在可配置阈值内。
6. 如果旧活动匹配到唯一活动：
   - 校验远端活动签名必须为 `manufacturer=HOLUX` 且
     `deviceId=0x12345678`。
   - 将 token 写入活动名。
   - 决策为 `skip-legacy-match` 或 `backfilled-token`。
   - 不上传。
7. 如果匹配到多个候选：
   - 决策为 `ambiguous`。
   - 不自动上传。
8. 如果没有匹配：
   - 生成临时 FIT。
   - 上传 FIT。
   - 定位上传后活动。
   - 校验远端活动签名必须为 `manufacturer=HOLUX` 且
     `deviceId=0x12345678`。
   - 签名符合时写入 token；签名不可见或不符合时不修改远端活动。
   - 决策为 `upload`。
9. 如果上传返回 HTTP 409：
   - 重新查询附近活动。
   - 若找到唯一匹配，先校验 GCU 上传签名；签名匹配才写入 token，
     决策为 `upload-conflict`。
   - 否则报告冲突，用户后续用 backfill 或人工检查处理。

`--dry-run` 必须执行到决策阶段，但不上传、不更新 Garmin 元数据、不写临时 FIT，除非用户显式要求 `--dry-run --render-fit`。

## 上传后定位

上传后定位活动的优先级：

1. 使用上传响应中明确返回的 `activityId`。
2. 查询轨迹日期附近的活动列表，通过起点时间、坐标、时长匹配。
3. 使用指数退避重试，因为 Garmin Connect 活动列表可能延迟刷新。

建议重试策略：

```text
1s, 2s, 3s, 5s, 5s, 5s, 5s
```

等待时间按轨迹点数估算：

```text
wait_s = min(max_wait_s, base_wait_s + ceil(point_count / 1000) * per_1000_points_s)
```

默认值：

```text
base_wait_s = 30
per_1000_points_s = 5
max_wait_s = 180
```

上传主循环不应在每个文件上同步等待完整定位时间。如果上传响应没有可靠
`activityId`，应把“等待活动出现在列表接口并写入 token”的工作提交给后台
worker，然后继续上传后续文件。整批上传结束前等待这些后台打标任务收尾。
这样长文件的 Garmin 后台处理时间可以与后续文件上传重叠。

定位失败时：

- 上传结果记为部分成功。
- 报告“已上传但未能写入 token”。
- 后续可通过 `python gcu_cli.py backfill` 补齐。

## 活动名策略

活动名由应用层统一生成，而不是由 FIT writer 或 GUI 生成。

默认格式：

```text
Nanjing Track Me [gcu:v1:...]
```

规则：

- 用户可通过 `--name-template` 覆盖。
- token 追加在末尾。
- 更新活动名时避免重复 token。
- 默认不覆盖用户已有的非 token 名称，除非处于本工具刚上传活动或用户显式指定 `--rename-existing`。

## Backfill 模式

Backfill 用于给历史已上传活动补 token，不上传新活动。

流程：

1. 解析本地文件并生成 token。
2. 查询对应日期附近的远端活动。
3. 如果 token 已存在，跳过。
4. 如果旧规则匹配唯一活动，追加 token。
5. 如果无匹配或多匹配，输出 unresolved 报告。

Backfill 输出应包含：

- 本地文件。
- token。
- 匹配到的 activity id。
- 原活动名。
- 新活动名。
- 决策状态。

## 错误处理

错误分为：

- `ParseError`：源文件无法解析或无有效点。
- `FormatUnsupportedError`：无法识别格式。
- `AuthError`：未登录、会话过期、凭据错误。
- `GarminApiError`：Garmin API 非预期失败。
- `DuplicateAmbiguousError`：远端存在多个候选活动。
- `UploadConflictError`：Garmin 返回冲突且无法唯一定位。
- `PostUploadTaggingError`：上传成功但 token 写入失败。

批量同步默认继续处理后续文件，最后汇总失败。`--fail-fast` 可在首个错误时退出。

## 临时文件和输出

默认临时 FIT 写入系统临时目录或应用缓存目录，上传成功后删除。

用户可指定：

```bash
python gcu_cli.py sync tracks/*.CSV --keep-fit --output-dir ./fit-out
python gcu_cli.py convert tracks/*.CSV --output-dir ./fit-out
```

转换产物和上传决策要解耦：

- `convert` 只转换。
- `sync` 只在需要上传时渲染 FIT。
- `dry-run` 默认不渲染 FIT。

## 配置

配置优先级：

1. CLI 参数。
2. 环境变量。
3. 用户配置文件。
4. 内置默认值。

建议配置项：

```text
domain = garmin.cn
timezone = UTC
display_timezone = auto
display_timezone_fallback = Asia/Shanghai
display_city = auto
display_city_min_population = 300000
default_format = auto
activity_name_template = {city} Track Me
coord_tolerance_deg = 0.001
time_tolerance_s = 60
duration_tolerance_s = 120
post_upload_wait_base_s = 30
post_upload_wait_per_1000_points_s = 5
post_upload_max_wait_s = 180
post_upload_tag_workers = 4
```

跨平台依赖要求：

- Python 3.11+。
- Windows 环境需安装 `tzdata`，以保证 `zoneinfo.ZoneInfo("Asia/Shanghai")` 等
  IANA 时区名称可用；项目依赖清单应显式包含该包。

不要在项目目录保存明文密码。CLI 默认使用用户级配置目录；GUI 使用当前目录
`.garth_session` 保存会话 token，并确保该目录不进入版本库。

## 测试策略

核心逻辑需要先覆盖单元测试：

- Columbus CSV 解析。
- 坐标、速度、时间规范化。
- 指纹稳定性。
- 不同文件名、相同轨迹得到相同 token。
- 行顺序不同、CSV 格式轻微差异不影响 token。
- 缺失/非法行被跳过并产生警告。
- 远端 token 索引。
- 旧活动唯一匹配、多匹配、无匹配。
- activityName token 追加、替换、去重。

集成测试：

- 使用 fake Garmin client 测试完整 `sync` 决策。
- 使用 fixture 文件验证 FIT writer 生成非空文件。
- CLI `--json` 输出稳定 schema。

真实 Garmin Connect 测试应独立标记，需要人工凭据和显式开关，不能在默认测试中运行。

## 迁移计划

建议分阶段实施：

1. 建立新包结构和统一模型。
2. 实现 Columbus CSV reader、fingerprint、activity name token 工具。
3. 实现 fake Garmin client 和 sync planner，先完成 dry-run。
4. 实现 Garmin client、远端活动分页查询和 token 索引。
5. 实现 FIT writer 和真实上传。
6. 实现上传后定位、token 写入和 HTTP 409 恢复。
7. 实现 backfill。
8. 实现 CLI JSON 事件流。
9. 用新 GUI 包装 CLI。
10. 移除旧脚本、测试脚本和明文凭据读取路径。

## 待验证事项

实施前或实施中需要用真实 Garmin Connect 验证：

- `activityName` 中 bracket token 是否会被 Garmin 原样保留。
- 活动列表 API 在 `garmin.cn` 和全球站点返回字段是否一致。
- 上传响应是否在部分情况下包含可靠 activity id。
- 活动备注、描述等字段是否能被列表接口返回并通过 API 更新。
- `PUT /activity-service/activity/{id}` 最小 payload 是否只需要 `activityId` 和 `activityName`。
- Garmin 对短时间内批量上传和批量改名的限流行为。

## 设计原则

- CLI 先行，GUI 跟随。
- 轨迹模型先行，格式适配器跟随。
- 上传前决策优先，HTTP 409 只作兜底。
- 远端 Garmin 元数据是去重事实来源，本地文件只提供可重算指纹。
- 每个自动动作都必须能在 dry-run 中解释清楚。
