# 计划书 V2：桌面端一体化追番应用（mikan-quota-downloader Desktop）

> 前置：V1 计划书见 [PLAN.md](PLAN.md)（订阅监控脚本 + qBittorrent + 每日 30GB 限额，已交付骨架）
> 本篇目标：**抛弃 qBittorrent 的界面，把「网站订阅管理 + 下载引擎 + 限额排队」合并成一个原生桌面应用**（macOS / Windows）
> 撰写日期：2026-09-08 ｜ 状态：**三项决策已按推荐确认，进入执行**（M1 引擎层已交付，见第 9/12 节）

---

## 1. 背景与痛点

V1 方案中 qBittorrent 只承担"下载引擎"，但用户仍要频繁面对它的界面（任务列表、
分类过滤、RSS 管理都很陈旧），且订阅（V1 脚本）与下载（qBt）是两个割裂的世界。
V2 要做的是一个**以追番为中心**的桌面应用：

- **看番库**：海报墙展示订阅的番剧、更新角标、集数进度；
- **订阅管理**：在应用内浏览 Mikan、一键订阅/退订，不再手工维护 RSS token；
- **下载透明化**：队列、排队原因（"今日 30GB 已用完，明日 0 点续传"）、速度、进度全部可视；
- **限额内置**：**下载量 + 做种上传量双日限额，上限均可在设置页自定义**（测试期均定 1GB/日），从"看不见的后台规则"变成 UI 里的一块仪表盘；
- **BT 流量直连**：下载与做种不经过 VPN（引擎绑定物理网卡），代理流量只留给网页/API。

## 2. 需求清单

| 编号 | 需求 | 说明 |
|---|---|---|
| FR1 | 单一应用 | 一个窗口完成：订阅 → 追更 → 下载 → 看结果；无需再开 qBt |
| FR2 | 应用内订阅 | ✅ **RSS 链接直接订阅已交付**：粘贴任意 Mikan RSS（如 `/RSS/Bangumi?bangumiId=…&subgroupid=…`）即下载全量集数并定时检查更新，**无需登录账号**；剩余：番剧页注入「订阅」按钮、账号聚合 RSS 路线 |
| FR3 | 会话管理 | ~~应用内登录向导~~ **已移除（2026-09-09）**：CF 人机验证无法通过且订阅/下载实测无需会话；被拦时手动导入浏览器 Cookie（无需登录） |
| FR4 | 下载引擎 | 使用 qBittorrent 同款内核 **libtorrent**，进程内嵌，不再依赖 qBt 安装 |
| FR5 | 限额排队 | **下载量、做种/上传量双日限额，上限均可自定义**（测试期均 1GB/日）；UI 显示今日用量、排队原因、预计恢复时间 |
| FR6 | 任务控制 | 暂停/恢复/删除/优先级/**边下边看**（顺序下载）/打开目录 |
| FR7 | 双平台 | macOS（Apple Silicon）与 Windows x64 一键打包 |
| FR8 | 数据延续 | 兼容 V1 的 `state.db`（seen/ledger/daily 三表继续使用，自动迁移加列） |
| FR9 | BT 直连 | 下载与做种的 peer/tracker 流量**不经过 VPN**：引擎绑定物理网卡出站，代理只留给网页/API 会话 |
| FR10 | 下载目录 | **指定下载地址**：全局默认目录（`desktop.save_path`）设置页可改、自动建目录、回写 config.yaml；手动添加任务可按次覆盖；未设置时添加任务明确报错 |
| NFR1 | 实时反馈 | 下载进度、速度通过推送刷新（≤1s 延迟），无需手动刷新 |
| NFR2 | 凭据安全 | 会话/配置存本地 `data/`，不入 Git、不外发 |
| NFR3 | 低打扰 | 后台常驻托盘，新集落地可发系统通知（可关） |

## 3. 核心决策：内核与桌面技术路线

### 3.1 "qBittorrent 的内核"是什么

qBittorrent 本身只是 Qt 壳，真正的下载内核是 **libtorrent**（C++ 库）。
"基于 qBittorrent 的内核重做 UI"因此有两条实现路径，必须先定：

| 方案 | 说明 | 结论 |
|---|---|---|
| 0. 换肤 qBt | qBt 支持替换 WebUI 前端（如 VueTorrent） | 成本最低，但受限于 qBt Web API 的信息结构，订阅仍需外挂脚本，**做不到一体化**，排除 |
| **A. 内嵌 libtorrent（推荐）** | 用 libtorrent 的 **Python 官方绑定**（`pip install libtorrent`）把内核嵌进我们自己的应用，qBt 不再需要安装 | 单一应用、全状态可控（每个 piece、每个告警）、业务与引擎同进程；**采用** |
| B. qBt 无头后台 + 新 UI | qBt 只当服务跑，新 UI 连它的 Web API | 零引擎开发量，但用户仍需安装/维护 qBt，Windows 下无官方无头模式，体验不彻底；**保留为后备引擎** |
| C. fork qBittorrent 改 C++/Qt | 在源码上改 UI | C++/Qt 重度开发，维护成本极高，排除 |
| D. Electron + webtorrent | Node 生态 | webtorrent 是 JS 另一实现，**并非 qBt 内核**，性能与成熟度弱，排除 |
| E. Tauri/Rust + libtorrent | Rust 绑定 libtorrent | 绑定不成熟，业务要用 Rust 重写一遍，排除 |

**结论：方案 A 为主线，B 作为工程兜底。** 引擎做成抽象层 `Engine`，两个实现：
`LibtorrentEngine`（默认，进程内嵌）与 `QbtWebuiEngine`（后备，连外部 qBt——V1 的
`qbittorrent.py` 直接改造复用）。若某平台 libtorrent 打包受阻，切 B 不影响 UI 与业务层。

### 3.2 桌面技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | **全栈 Python** | V1 业务模块（会话/订阅解析/限额/记账）约 70% 直接复用，单语言 lowest risk |
| 下载内核 | **libtorrent-python**（qBt 同款内核，Rakshasa/arvid 编写，qBt 即基于它） | 满足"基于 qBittorrent 内核"的本意 |
| 桌面窗口 | **pywebview**（macOS 用 WKWebView，Windows 用 WebView2，系统自带组件） | 原生窗口 + 现代 Web UI（get_cookies() 收割已随登录向导移除） |
| 本地服务 | **FastAPI + uvicorn**（127.0.0.1 随机端口） | REST + SSE；前端静态资源由它托管 |
| 前端 | **Vue 3 + Vite + Naive UI + Tailwind** | 海报墙、仪表盘类 UI 用 Web 技术开发效率与观感最好 |
| 打包 | **PyInstaller**（前端产物随包内置） | 双平台出 `.app` / `.exe` |

对比过的其他 UI 方案：PySide6/Qt（原生但海报墙类界面开发慢、观感一般）、
Electron（体积 ~150MB、且需解决 libtorrent 的 Node 绑定问题）——均不采用。

## 4. 总体架构

```
┌────────────────────────── 桌面应用（单窗口 + 托盘）──────────────────────────┐
│                                                                             │
│  pywebview 原生窗口（WKWebView / WebView2）                                  │
│    ├── 应用 UI（Vue3：番剧库 / 详情 / 任务 / 看板 / 设置）                     │
│    └── 内嵌站点视图（mikan.tangbai.cc + 注入「订阅」按钮，M3+）——登录向导已移除  │
│                  │ REST / SSE (localhost)                                   │
│  FastAPI 本地服务（后台线程）                                                 │
│    ├── /api/subscriptions  订阅 CRUD、封面、更新状态                          │
│    ├── /api/episodes       集数列表、状态、手动重试/优先                      │
│    ├── /api/tasks          引擎任务：进度/速度/暂停/恢复/删除/顺序下载          │
│    ├── /api/quota          今日用量、近 7 日、排队原因                         │
│    ├── /api/session        Cookie 导入/状态/清除（Cloudflare 兜底，无需登录）       │
│    └── /api/events         SSE 实时推送（进度/告警/限额事件）                   │
│                  │                                                           │
│  业务层（V1 复用）                                                            │
│    ├── MonitorScheduler  订阅轮询（RSS→新集→种子字节）                         │
│    ├── QuotaGuard        下载/做种双限额 + 队列放行 + 上传闸门（store.py）      │
│    ├── MikanClient       会话/种子抓取（session.py + mikan.py）               │
│    └── Store             SQLite（seen/ledger/daily + 新增 subscriptions）     │
│                  │                                                           │
│  引擎抽象层 Engine                                                            │
│    ├── LibtorrentEngine  ★ 默认：进程内嵌 libtorrent（qBt 同款内核）           │
│    │     alert 循环线程：add/pause/resume/remove、状态与流量统计             │
│    └── QbtWebuiEngine    后备：连外部 qBittorrent（V1 qbittorrent.py 改造）   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 5. 代码复用与迁移（V1 → V2）

| V1 模块 | V2 去向 |
|---|---|
| `session.py`（CF 会话、curl-cffi） | **原样复用**；会话来源改为手动 Cookie 导入（见 6.2） |
| `mikan.py`（RSS 解析、种子抓取） | **原样复用**；新增番剧页/搜索页解析（订阅管理用） |
| `quota.py` + `store.py`（30GB 算法、记账、去重） | **原样复用**；ledger 的 `downloaded` 改从引擎状态取（字段同义） |
| `torrents.py`（bencode 体积） | **原样复用** |
| `qbittorrent.py` | 改造为 `QbtWebuiEngine`（后备实现） |
| `main.py` 轮询逻辑 | 决策部分并入 `QuotaGuard` + `MonitorScheduler`（事件驱动化） |
| `login.py`（Playwright） | ❌ 已移除（2026-09-09）：CF 无法通过，会话改走手动 Cookie 导入（`/api/session/import`） |

## 6. 关键设计

### 6.1 引擎抽象层

```python
class Engine(Protocol):
    def add(self, data: bytes, *, paused: bool, save_path: str, sequential: bool = False) -> str: ...
    def pause(self, sha: str) / resume(sha) / remove(sha, with_files: bool = False)
    def list(self) -> list[TorrentState]   # sha/name/state/size/done/rate/eta/sequential
    def counters(self) -> EngineCounters   # 今日增量记账所需的 downloaded 等
    def set_global_limit(self, down_bps: int | None)   # 全局限速
```

- `LibtorrentEngine`：独立线程跑 alert 循环（`session.pop_alerts()`，500ms），
  `torrent_handle.status()` 提供进度/速度/`total_done`/`all_time_download`；
  追番场景默认开 **sequential download**（边下边看）作为集数级开关。
- `QbtWebuiEngine`：同一接口映射到 Web API，仅在 libtorrent 打包失败的平台或
  用户显式选择时启用。

### 6.2 站点会话（已降级为 Cookie 导入通道）

> **2026-09-09：登录向导已整体移除。** 实测 Cloudflare 人机验证无法通过，且订阅/下载
> **不需要任何会话**（curl-cffi 的 Chrome 指纹可直连镜像 RSS，已实测）。

现行策略：

1. 日常：不带会话直接访问；
2. 被拦时（HTTP 403/503 或 challenge 页面特征）：界面提示到「站点 Cookie」卡片手动
   粘贴浏览器 Cookie（**无需登录账号**，浏览器能打开站点即可复制），存
   `data/session.json`，curl-cffi 侧复用；
3. webview 收割（`get_cookies()`）与 Playwright 登录工具（V1 `login.py`）均已随向导移除。

### 6.3 订阅管理（RSS 链接路线已交付；注入按钮与账号路线随后）

**已交付（2026-09-08，回应用户"无法通过 CF 登录"）：RSS 链接订阅**

- `subscriptions` 表：`rss_url（唯一，重复添加=更新）/ title / save_path（目录覆盖）/
  enabled / last_checked / last_error`；
- **添加即验证并下载**：`POST /api/subscriptions` 立即拉取 RSS（顺带校验可达性），
  全量条目经限额守门入队（旧→新），GUID 写入 `seen` 去重；
- **定时轮询**：调度器每 `monitor.interval_minutes` 分钟（界面可改，立即生效并回写
  config.yaml）对启用中的订阅逐个检查；新集自动下载，限额装不下自动排队；
- **滚动窗口语义**：番剧 RSS 只保留最近若干条，但每次轮询拉全量 + GUID 去重，
  新集出现即被下载，源滚动不影响订阅；应用离线超过窗口期漏掉的集数需手动补种；
- **无需登录**：实测 curl-cffi 的 Chrome 指纹可直接拉取镜像站 RSS；被 CF 拦截时
  订阅仍保存并在界面提示"导入 Cookie（无需登录账号，能浏览站点即可）"，
  Cookie 导入后下一轮自动恢复；
- 被拦截的订阅本轮即停（避免连环触发 CF），其余订阅顺延到下轮。

**M3 剩余**：内嵌站点视图浏览 + 番剧页「＋订阅」注入按钮；账号聚合 RSS
（`/Feed/MyBangumi?token=`）作为已登录用户的可选路线；Vue 正式前端。

### 6.4 限额与排队（下载 + 做种双限额，上限均可自定义）

两个相互独立的自然日限额，**上限在设置页随时可改，测试期均定为 1GB/日**：

| 限额 | 计量（均按自然日本地日、按种子增量归集） | 触达后行为 |
|---|---|---|
| **下载量** | 各任务 `downloaded` 增量 → `daily.used` | 预算装不下的新集排队（添加为暂停），次日自动续传——沿用 V1 预算公式 `预算 = 上限 − 今日已用 − 在途剩余` |
| **做种/上传量** | 各任务 `uploaded` 增量 → `daily.up_used` | 当日关闭「上传闸门」：全局上传限速压到闸门值 1 B/s（引擎里 0=不限速，不能用 0），次日自动放开；任务不删除、下载不受影响 |

设计要点：

- **速率限制与容量限额是两层**：设置页的全局限速滑块管"跑多快"，本节限额管"每天跑多少"；做种闸门由限额层托管——触达时覆盖用户上传限速，放开时还原用户设定值；
- 记账表扩展：`ledger` 加 `uploaded` 列、`daily` 加 `up_used` 列（旧库自动 `ALTER TABLE` 迁移，兼容 V1 数据）；
- 下载限额的预算判定沿用 V1 算法（`quota.py`/`store.py`），触发时机从轮询改为事件驱动：
  「引擎告警 / 新集入库 / 跨天定时」即时触发 `QuotaGuard.sync()`（放行下载队列 + 应用做种闸门），
  排队/放行动作与 UI 状态（"排队中 · 今日额度已满 · 00:00 自动恢复"）同一次计算产出。

### 6.5 实时通信

FastAPI SSE（`/api/events`）推送：任务进度（合并至 1s 一帧）、限额事件、订阅更新事件；
前端断线自动重连。不引 WebSocket，SSE 单向够用且实现简单。

### 6.6 下载目录管理（指定下载地址）

- **全局默认**：`desktop.save_path`，设置页可随时修改——校验绝对路径、目录不存在自动创建、
  通过 `ConfigStore` 回写实际加载的 config.yaml（重启后仍生效）；
- **任务级覆盖**：手动添加种子（`POST /api/tasks/add`，请求体为 .torrent 字节）可用
  `save_path` 参数指定本次目录；
- **生效范围**：目录变更只影响之后新增的任务，已在途任务保持各自原目录
  （libtorrent 按逐任务 `save_path` 落盘，qBt 按 `savepath` 字段）；
- **兜底**：默认目录与请求目录都为空时，添加任务返回明确报错，不放任任务落到随机目录；
- 订阅级目录（每番各自目录）随 M3 的 `subscriptions` 表一起落地。

### 6.7 BT 流量直连（不经过 VPN）

目标：下载与做种的 peer/tracker 流量走物理网络，**不消耗 VPN 代理流量**；
Mikan 网页/API 会话不受影响（仍走系统默认路由，需要代理时可正常代理——过盾会话与代理无冲突）。

- **原理：网卡绑定。** libtorrent 侧 `listen_interfaces` 绑定物理网卡 IP +
  `outgoing_interfaces` 限定出站网卡——即使 VPN 以 TUN/全隧模式接管默认路由，
  绑定物理网卡即可绕开虚拟网卡（与 qBittorrent「网络接口」绑定同一原理）；
  qBt 后备引擎用 `app/setPreferences` 的 `current_interface_address` 实现同样效果；
- 引擎接口增加 `bind_ip` 构造参数；设置页提供网卡下拉
  （`engine/net.py` 枚举 IPv4 网卡候选；未装 psutil 时退化为探测默认路由出口 IP 并提示人工核对）；
- **边界与回退**：若直连后部分 tracker/peer 不可达（所在网络必须经代理出网），设置页保留
  「跟随系统路由」开关一键回退（清空绑定）。

## 7. UI 设计（5 个页面）

```
┌────────────────────────────────────────────────────────────┐
│ ⌘ 侧栏：追番库 / 任务 / 数据看板 / 设置   [会话状态●] [↓12.4/1GB ▓▓▓░] [↑0.3/1GB ░░░] │
├────────────────────────────────────────────────────────────┤
│  追番库（海报墙）                                             │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐                │
│  │ 封面    │ │ 封面    │ │ 封面    │ │ 封面    │                │
│  │ ●更新2集│ │ 下载中  │ │ 已完结  │ │ 排队中  │   ← 角标=状态   │
│  │ ★★★★   │ │ ▓▓▓░ 64%│ │ 12/12  │ │ 明日续传│                │
│  └────────┘ └────────┘ └────────┘ └────────┘                │
├────────────────────────────────────────────────────────────┤
│  番剧详情（点海报进入）：集数列表 [第08集 ✓已完成] [第09集 ↓64%] [第10集 ⏸排队·额度已满] │
└────────────────────────────────────────────────────────────┘
```

| 页面 | 内容 |
|---|---|
| 追番库 | 海报墙（封面/更新角标/进度条/限额状态）；「＋添加订阅」进内嵌站点视图 |
| 番剧详情 | 集数列表（状态标签：已完成/下载中/排队/失败）、集数级操作（优先、重试、边下边看） |
| 任务 | 引擎任务表：速度、ETA、做种状态、全局限速滑块、**手动添加种子（可指定本次下载目录、边下边看）**；失败原因内联展示 |
| 数据看板 | 今日下载/上传双仪表盘、近 7 日柱状图、每番消耗排行、限额触达记录 |
| 设置 | **站点 Cookie 导入（无需登录）**、**下载/做种双限额上限、重置时刻**、保存目录、**BT 网卡绑定（直连/跟随系统路由）**、引擎选择（内嵌/qBt 后备）、通知开关 |

视觉基调：深色为主 + 海报色彩自适应强调色；中文字体优先；所有排队/失败**给出原因**。

## 8. 打包与分发

| | macOS (Apple Silicon) | Windows x64 |
|---|---|---|
| 运行时 | PyInstaller 打包 `.app` | PyInstaller 打包免安装目录 + `.exe` |
| libtorrent | ✅ 已实测：PyPI 无 mac 轮子，但 `brew install libtorrent-rasterbar`（2.1.1 自带 **python3.14** 绑定）可用；venv 用 `.pth` 指向其 site-packages 即可，源码构建捆绑留给打包期 | PyPI 官方轮子，直接内嵌 |
| WebView | 系统 WKWebView，零依赖 | WebView2（Win10/11 一般已内置，缺则引导装 Evergreen 运行时） |
| 签名 | ad-hoc 签名（个人使用免开发者账号；首次打开右键打开） | 未签名 exe 会触发 SmartScreen 提示，文档说明"仍要运行" |
| 自动更新 | 不做（人工替换），M6 再议 | 同左 |

## 9. 里程碑

| 阶段 | 内容 | 交付判据 |
|---|---|---|
| M1 | 引擎层：`LibtorrentEngine` + `QbtWebuiEngine` + 接口单测；限额层接入引擎计数 | ✅ 已交付（2026-09-08）：`desktop/server/engine/`、`services/quota_guard.py`，全绿；同日按新需求扩展**双限额（下载/做种，1GB 测试值）与网卡绑定直连** |
| M2 | 本地服务 + pywebview 空壳 + 登录向导（webview 收割会话） | ✅ 代码交付（2026-09-09）：FastAPI 服务 + 桌面壳（pywebview/浏览器双模式，含引擎不可达优雅降级）+ 最小可用 UI；**登录向导已按 09-09 决策移除**（Cookie 导入保留为兜底通道） |
| M3 | 前端骨架（Vue3）+ 追番库/详情页 + 订阅管理（注入按钮→入库） | ▶️ **RSS 链接订阅已提前交付**（2026-09-08）：订阅表/服务/定时轮询/去重 + 过渡版 UI，84 项单测全绿；剩余：Vue 正式前端、番剧页注入订阅、账号聚合路线 |
| M4 | 任务页 + 看板页 + SSE 实时刷新 + **下载/做种双限额可视化** | 限额打满时 UI 正确展示排队与次日恢复、上传闸门状态 |
| M5 | 打包（双平台）+ 边下边看 + 系统通知 + 设置页落地（**双限额、网卡绑定**） | 免开发环境的双平台包各一份，真机验收 |
| M6 | 打磨：失败重试策略、磁盘水位、可选自动更新 | — |

依赖关系：M1 不依赖站点（纯引擎/限额），可立即开工；M2 起需要真实网络环境验证 CF。

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| libtorrent 在 macOS 无官方 PyPI 轮子 | 打包受阻 | ✅ 已验证 brew 路线：`brew install libtorrent-rasterbar`（绑定 python3.14，2026-09-08 实机通过）；打包期改为捆绑 dylib/绑定；极端情况切 `QbtWebuiEngine`（引擎抽象已隔离风险） |
| Windows WebView2 `get_cookies()` 兼容性 | 登录向导降级 | ✅ 已随登录向导移除（2026-09-09），Cookie 手动导入不受影响 |
| libtorrent 2.x mmap 对磁盘占用/兼容的行为差异 | 文件预占空间 | 默认 `enable_memmap=false` 行为配置（1.2 语义），集数完成后再全量校验 |
| BT 直连后部分 tracker/peer 不可达（所在网络必须经代理出网） | 下载/做种变慢或失败 | 设置页「跟随系统路由」一键回退（清空网卡绑定）；Mikan 会话流量不受影响 |
| PyInstaller 体积与启动速度 | 体验 | 排除无关模块、前端产物压缩；预期 <120MB，可接受 |
| CF 策略变化导致 curl-cffi 会话被拒 | 追更中断 | 拦截识别→UI 引导重登（成本仅 1 分钟）；比 V1 少了"用户不知道挂了"的问题 |
| UI 范围蔓延 | 延期 | M3/M4 只做 5 个页面的最小完整版，动效与美化集中在 M5 |

## 11. 目录结构规划（V2 落地时）

```
mikan-quota-downloader/
├── PLAN.md / PLAN-DESKTOP.md      ← V1 / V2 计划书
├── src/mqd/                        ← V1 业务层（复用，见第 5 节）
├── desktop/
│   ├── server/
│   │   ├── engine/        ← ✅ base.py（Engine 接口）/ libtorrent_engine.py / qbt_engine.py / net.py（网卡枚举，BT 直连）
│   │   ├── services/      ← ✅ quota_guard.py（下载/做种双限额守门 + 上传闸门）
│   │   └── api/ · scheduler/ · main.py   ← M2+ 落地（FastAPI、SSE、订阅轮询）
│   ├── web/               ← M3 落地（Vue3 + Naive UI）
│   └── app.py             ← M2 落地（pywebview 启动器，打包入口）
└── tests/                 ← V1 + V2 单测（限额/记账/bencode/双引擎/QuotaGuard）
```

## 12. 决策记录（已确认，2026-09-08）

按推荐方案拍板，进入执行：

| 决策点 | 结论 |
|---|---|
| 技术路线 | **内嵌 libtorrent**（`LibtorrentEngine`）为主线；`QbtWebuiEngine`（连外部 qBittorrent）作为后备实现，二者实现同一 `Engine` 接口 |
| 前端框架 | **Vue 3 + Vite + Naive UI + Tailwind** |
| 仓库布局 | **与 V1 同仓库演进**：业务层复用 `src/mqd/`，V2 代码位于 `desktop/`，V1 脚本继续可用；已加 `pyproject.toml` 支持各端安装 |
| 限额体系（2026-09-08 补充） | 下载量 + 做种/上传量**双日限额**，上限均可在设置页自定义；**测试期均定 1GB/日**；做种触达后当日关闭上传闸门（1 B/s），次日自动恢复 |
| 网络策略（2026-09-08 补充） | **BT 下载与做种流量不经过 VPN**：引擎绑定物理网卡（libtorrent `listen/outgoing_interfaces`；qBt `setPreferences` 接口绑定）；Mikan 网页/API 会话不受影响 |
| 下载目录（2026-09-09 补充） | **指定下载地址**：全局默认目录设置页可改、自动建目录、回写 config.yaml（`ConfigStore` 只回写实际加载的配置文件）；手动添加任务可按次覆盖；未设置时添加任务明确报错 |
| 订阅路线（2026-09-08 补充） | 用户无法通过 CF 登录 → 新增 **RSS 链接订阅**：粘贴 `/RSS/Bangumi` 链接即可下载并定时检查更新，**无需账号登录**；实测 Chrome 指纹可直连镜像 RSS，被拦时以「导入 Cookie（无需登录）」兜底 |
| 移除登录（2026-09-09 决策） | **登录向导整体移除**（应用桥接、`/api/session/harvest` 端点、V1 `login.py`、playwright 依赖注释）：CF 人机验证无法通过且订阅实测无需会话；「站点 Cookie」手动导入保留为唯一兜底通道 |
| 四项增强（2026-09-09 补充，用户实测反馈后） | ① 订阅可重命名；② **磁力链接直接添加**（无需 .torrent 文件；元数据后置限额判定，超预算自动排队）；③ UI 改版：白底浅色主题、大按钮、分区编号布局；④ 做种可视化（「做种中」状态 + 上传速度列 + 做种排查指引） |
| 侧栏 UI + 订阅生命周期（2026-09-09 补充） | UI 改为**左侧固定导航**（主页/订阅/下载/下载任务/全局限速，设置在侧栏底部；今日限额并入主页）；订阅删除改为**软删除**（「已删除」列表可恢复/彻底删除）；新增**集数追踪**（sub_episodes 表）+ 本地种子存档（data/torrents/）+ 每轮自动补拉——未下完的集数任务丢失后会自动重新入队续传，修复"删了订阅/丢了任务就永远不补下"的问题 |

当前进度：M3 的 RSS 订阅闭环已交付并经用户实测跑通；侧栏 UI 与订阅生命周期增强已交付
（95 项单测全绿，磁链真机冒烟通过）。剩余：番剧页注入订阅；UI 已相当完整，Vue 重构优先级降低。
