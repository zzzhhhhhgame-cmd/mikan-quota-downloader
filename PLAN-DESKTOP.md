# 计划书 V2：桌面端一体化追番应用（mikan-quota-downloader Desktop）

> 前置：V1 计划书见 [PLAN.md](PLAN.md)（订阅监控脚本 + qBittorrent + 每日 30GB 限额，已交付骨架）
> 本篇目标：**抛弃 qBittorrent 的界面，把「网站订阅管理 + 下载引擎 + 限额排队」合并成一个原生桌面应用**（macOS / Windows）
> 撰写日期：2026-09-08 ｜ 状态：规划稿，待确认后开工

---

## 1. 背景与痛点

V1 方案中 qBittorrent 只承担"下载引擎"，但用户仍要频繁面对它的界面（任务列表、
分类过滤、RSS 管理都很陈旧），且订阅（V1 脚本）与下载（qBt）是两个割裂的世界。
V2 要做的是一个**以追番为中心**的桌面应用：

- **看番库**：海报墙展示订阅的番剧、更新角标、集数进度；
- **订阅管理**：在应用内浏览 Mikan、一键订阅/退订，不再手工维护 RSS token；
- **下载透明化**：队列、排队原因（"今日 30GB 已用完，明日 0 点续传"）、速度、进度全部可视；
- **限额内置**：30GB/日 逻辑不变，但从"看不见的后台规则"变成 UI 里的一块仪表盘。

## 2. 需求清单

| 编号 | 需求 | 说明 |
|---|---|---|
| FR1 | 单一应用 | 一个窗口完成：订阅 → 追更 → 下载 → 看结果；无需再开 qBt |
| FR2 | 应用内订阅 | 内嵌浏览器视图打开 mikan.tangbai.cc（过 CF），番剧页注入「订阅」按钮 |
| FR3 | 会话管理 | 应用内完成首次登录与过盾；会话过期在 UI 上提示并引导重新登录 |
| FR4 | 下载引擎 | 使用 qBittorrent 同款内核 **libtorrent**，进程内嵌，不再依赖 qBt 安装 |
| FR5 | 限额排队 | 沿用 V1 的每日 30GB 算法；UI 显示今日用量、排队原因、预计恢复时间 |
| FR6 | 任务控制 | 暂停/恢复/删除/优先级/**边下边看**（顺序下载）/打开目录 |
| FR7 | 双平台 | macOS（Apple Silicon）与 Windows x64 一键打包 |
| FR8 | 数据延续 | 兼容 V1 的 `state.db`（seen/ledger/daily 三表继续使用） |
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
| 桌面窗口 | **pywebview**（macOS 用 WKWebView，Windows 用 WebView2，系统自带组件） | 原生窗口 + 现代 Web UI；支持 `get_cookies()` 收割登录会话（见 6.2） |
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
│    └── 内嵌站点视图（mikan.tangbai.cc + 注入「订阅」按钮）——登录向导也在这完成  │
│                  │ REST / SSE (localhost)                                   │
│  FastAPI 本地服务（后台线程）                                                 │
│    ├── /api/subscriptions  订阅 CRUD、封面、更新状态                          │
│    ├── /api/episodes       集数列表、状态、手动重试/优先                      │
│    ├── /api/tasks          引擎任务：进度/速度/暂停/恢复/删除/顺序下载          │
│    ├── /api/quota          今日用量、近 7 日、排队原因                         │
│    ├── /api/session        会话状态、触发登录向导                              │
│    └── /api/events         SSE 实时推送（进度/告警/限额事件）                   │
│                  │                                                           │
│  业务层（V1 复用）                                                            │
│    ├── MonitorScheduler  订阅轮询（RSS→新集→种子字节）                         │
│    ├── QuotaGuard        每日 30GB 判定 + 等待队列放行（quota.py + store.py）  │
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
| `session.py`（CF 会话、curl-cffi） | **原样复用**；登录来源改为"应用内 webview 收割"（见 6.2） |
| `mikan.py`（RSS 解析、种子抓取） | **原样复用**；新增番剧页/搜索页解析（订阅管理用） |
| `quota.py` + `store.py`（30GB 算法、记账、去重） | **原样复用**；ledger 的 `downloaded` 改从引擎状态取（字段同义） |
| `torrents.py`（bencode 体积） | **原样复用** |
| `qbittorrent.py` | 改造为 `QbtWebuiEngine`（后备实现） |
| `main.py` 轮询逻辑 | 决策部分并入 `QuotaGuard` + `MonitorScheduler`（事件驱动化） |
| `login.py`（Playwright） | 保留为兜底登录方式；主方式见 6.2 |

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

### 6.2 会话与首次登录（体验重点）

1. 设置页点「登录 Mikan」→ 应用窗口切到**内嵌站点视图**打开登录页；
2. 用户在应用内完成账号登录 + Cloudflare 人机验证（真实浏览器环境，成功率同人工）;
3. 完成后点「我登录好了」，应用调 `webview.get_cookies()` 收割会话（含 `cf_clearance`）
   与当前 UA，存 `data/session.json`，curl-cffi 侧复用（UA 必须一致）；
4. 后台请求被 CF 拦截时（V1 已有识别逻辑）→ UI 顶部横幅提示「会话过期」→ 一键重走向导；
5. 兜底：Windows 端若 `get_cookies()` 在目标 WebView2 版本不可用（M2 验证项），
   回退 Playwright 独立窗口登录（V1 `login.py`）或手动粘贴 Cookie。

### 6.3 订阅管理（不再手工维护 RSS）

- `subscriptions` 表：`bangumi_id / 标题 / 封面 / 字幕组过滤 / 启用 / 添加时间`；
- 添加流程：内嵌站点视图浏览 mikan → 番剧详情页由注入脚本显示「＋订阅此番」悬浮按钮
  → 点击调本地 API → 后端解析该页（番剧名、封面、`/Feed/Bangumi?bangumiId=X` RSS）入库；
- 轮询改为**逐订阅**拉各自的番剧 RSS（比 V1 的聚合 RSS 多了字幕组过滤与按番统计的能力），
  聚合 RSS 仍保留为兼容选项。

### 6.4 限额与排队（沿用 V1 算法，事件驱动化）

决策函数不变（`quota.py` 的预算公式与 `store.py` 的按日记账）。变化仅在触发时机：
V1 是每 20 分钟轮询，V2 变成「引擎告警/新集入库/跨天定时」三类事件即时触发，
排队/放行动作与 UI 状态（"排队中 · 今日额度已满 · 00:00 自动恢复"）同一次计算产出。

### 6.5 实时通信

FastAPI SSE（`/api/events`）推送：任务进度（合并至 1s 一帧）、限额事件、订阅更新事件；
前端断线自动重连。不引 WebSocket，SSE 单向够用且实现简单。

## 7. UI 设计（5 个页面）

```
┌────────────────────────────────────────────────────────────┐
│ ⌘ 侧栏：追番库 / 任务 / 数据看板 / 设置        [会话状态●] [今日 12.4/30GB ▓▓▓░] │
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
| 任务 | 引擎任务表：速度、ETA、做种状态、全局限速滑块；失败原因内联展示 |
| 数据看板 | 今日 30GB 仪表盘、近 7 日柱状图、每番消耗排行、限额触达记录 |
| 设置 | 站点会话（登录向导）、限额（GB/日、重置时刻）、保存目录、引擎选择（内嵌/qBt 后备）、通知开关 |

视觉基调：深色为主 + 海报色彩自适应强调色；中文字体优先；所有排队/失败**给出原因**。

## 8. 打包与分发

| | macOS (Apple Silicon) | Windows x64 |
|---|---|---|
| 运行时 | PyInstaller 打包 `.app` | PyInstaller 打包免安装目录 + `.exe` |
| libtorrent | ⚠️ PyPI 无官方 mac 轮子：`brew install libtorrent`（开发）或源码构建后**随包捆绑 dylib** | PyPI 官方轮子，直接内嵌 |
| WebView | 系统 WKWebView，零依赖 | WebView2（Win10/11 一般已内置，缺则引导装 Evergreen 运行时） |
| 签名 | ad-hoc 签名（个人使用免开发者账号；首次打开右键打开） | 未签名 exe 会触发 SmartScreen 提示，文档说明"仍要运行" |
| 自动更新 | 不做（人工替换），M6 再议 | 同左 |

## 9. 里程碑

| 阶段 | 内容 | 交付判据 |
|---|---|---|
| M1 | 引擎层：`LibtorrentEngine` + `QbtWebuiEngine` + 接口单测；限额层接入引擎计数 | 脚本驱动下载/暂停/记账跑通，`pytest` 绿 |
| M2 | 本地服务 + pywebview 空壳 + 登录向导（webview 收割会话） | 应用窗口能登录 Mikan 并通过 CF，会话落盘可复用 |
| M3 | 前端骨架（Vue3）+ 追番库/详情页 + 订阅管理（注入按钮→入库） | 应用内完成「订阅→出现新集→自动下载」闭环 |
| M4 | 任务页 + 看板页 + SSE 实时刷新 + 限额排队可视化 | 限额打满时 UI 正确展示排队与次日恢复 |
| M5 | 打包（双平台）+ 边下边看 + 系统通知 | 免开发环境的双平台包各一份，真机验收 |
| M6 | 打磨：失败重试策略、磁盘水位、可选自动更新 | — |

依赖关系：M1 不依赖站点（纯引擎/限额），可立即开工；M2 起需要真实网络环境验证 CF。

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| libtorrent 在 macOS 无官方 PyPI 轮子 | 打包受阻 | 开发期 `brew install libtorrent`；发布期源码构建并捆绑 dylib；极端情况切 `QbtWebuiEngine`（引擎抽象已隔离风险） |
| Windows WebView2 `get_cookies()` 兼容性 | 登录向导降级 | M2 首项验证；兜底 Playwright 登录（V1 已有）与手动 Cookie |
| libtorrent 2.x mmap 对磁盘占用/兼容的行为差异 | 文件预占空间 | 默认 `enable_memmap=false` 行为配置（1.2 语义），集数完成后再全量校验 |
| PyInstaller 体积与启动速度 | 体验 | 排除无关模块、前端产物压缩；预期 <120MB，可接受 |
| CF 策略变化导致 curl-cffi 会话被拒 | 追更中断 | 拦截识别→UI 引导重登（成本仅 1 分钟）；比 V1 少了"用户不知道挂了"的问题 |
| UI 范围蔓延 | 延期 | M3/M4 只做 5 个页面的最小完整版，动效与美化集中在 M5 |

## 11. 目录结构规划（V2 落地时）

```
mikan-quota-downloader/
├── PLAN.md / PLAN-DESKTOP.md      ← V1 / V2 计划书
├── src/mqd/                        ← V1 业务层（复用，见第 5 节）
├── desktop/
│   ├── server/                     ← FastAPI（api/、scheduler/、engine/）
│   │   ├── engine/libtorrent_engine.py · qbt_engine.py · base.py
│   │   └── main.py
│   ├── web/                        ← Vue3 + Vite 前端（构建产物进 server/static）
│   └── app.py                      ← pywebview 启动器（打包入口）
└── tests/
```

## 12. 待确认事项（开工前需要你拍板）

1. **技术路线确认**：内嵌 libtorrent（推荐主线）+ qBt 后备引擎——是否认可？
2. **前端框架**：Vue 3 + Naive UI（我的推荐）；如你更熟 React 可换，UI 设计不受影响；
3. **与 V1 的关系**：V1 脚本保留可用（qBt 用户），V2 应用独立成 `desktop/` 目录，同仓库演进。
