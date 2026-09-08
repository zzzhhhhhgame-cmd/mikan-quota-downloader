# 计划书：Mikan 订阅自动追番下载器（mikan-quota-downloader）

> 目标站点：<https://mikan.tangbai.cc/>（Mikan Project 镜像，带 Cloudflare 人机验证）
> 撰写日期：2026-09-07 ｜ 状态：规划完成，脚手架代码已就绪

---

## 1. 背景与目标

你希望通过番剧订阅站点（mikan.tangbai.cc）追番，但该站点有 Cloudflare 人机验证，且希望
下载行为受「每日 30GB」的总量约束。目标是搭一套**跨平台（macOS / Windows）**的自动化系统：

1. 复用你的站点账号会话（首次登录后自动保持）；
2. 定期检查订阅是否有新集数；
3. 有更新时自动下载 `.torrent` 文件并交给下载器；
4. 所有下载遵守**每日 30GB 限额**，超出的自动排队、次日续传；
5. 全流程无人值守。

## 2. 需求清单

| 编号 | 需求 | 说明 |
|---|---|---|
| FR1 | 账号会话保持 | 首次人工登录（含过盾）后，会话长期复用，过期可低成本的重新授权 |
| FR2 | 定期检查订阅 | 轮询订阅 RSS/页面（默认 20 分钟，可配置） |
| FR3 | 自动抓取种子 | 从更新条目中取到 `.torrent` 文件本体（需登录态） |
| FR4 | 入队下载 | 种子交给下载器排队执行，自动去重（同一集不重复下） |
| FR5 | 每日 30GB 限额 | 按自然日统计实际下载流量，超出部分排队、次日自动恢复 |
| FR6 | 双平台 | macOS 与 Windows 均可部署，配置通用 |
| NFR1 | 抗 Cloudflare | 请求带浏览器指纹，避免频繁触发盾 |
| NFR2 | 可观测 | 日志记录每集的处理结果与限额余额 |
| NFR3 | 凭据安全 | Cookie/token 不入库不入 Git，配置文件本地保存 |

## 3. 选型结论（TL;DR）

| 组件 | 选型 | 一句话理由 |
|---|---|---|
| 下载器 | **qBittorrent**（Mac/Win 双端原生） | 免费开源、带完整 WebUI REST API，是唯一能让脚本「添加/暂停/恢复/查流量」全程序化的主流双端客户端 |
| 订阅检查 | **自研 Python 常驻脚本**（本仓库） | 现成工具（qBt 内置 RSS / AutoBangumi / ani-rss）都无法同时满足「CF 镜像站 + 登录态抓种子 + 每日 30GB 限额」 |
| 过 Cloudflare | **Playwright 真浏览器首次登录 → 会话文件复用**，FlareSolverr 为兜底 | 你愿意提供一次人工登录，这是成功率最高、依赖最少的路线 |
| HTTP 客户端 | **curl-cffi**（模拟 Chrome TLS 指纹） | 携带过盾后的 Cookie 请求时不易被 JA3 指纹识别为脚本 |

## 4. 下载器选型对比

| | **qBittorrent ✅** | Transmission | Deluge |
|---|---|---|---|
| macOS / Windows 原生 | ✅ 双端官方安装包 | ✅（macOS 端体验一般） | ⚠️ macOS 支持弱 |
| WebUI REST API | ✅ Web API v2，功能最全（增删暂停恢复、分类、查每个种子流量） | ⚠️ RPC 可用但功能少 | ⚠️ WebUI API 偏弱 |
| 按分类（category）管理 | ✅ 天然适合隔离本项目的种子做限额统计 | ❌ 用标签模拟 | ⚠️ 有标签 |
| 4.x / 5.x 兼容 | ✅ 本项目已兼容 `paused/stopped`、`resume/start` 命名差异 | — | — |
| 社区生态（配合 Mikan 类工具） | ✅ 事实标准 | 一般 | 一般 |

**结论：qBittorrent。** 启用内置 WebUI（本机 127.0.0.1:8080 + 账号密码），脚本通过
`http://127.0.0.1:8080/api/v2/...` 全程控制。

## 5. 订阅自动化方案对比（为什么自研）

| 方案 | CF 镜像站支持 | 登录态抓种子 | 每日限额 | 双平台 | 结论 |
|---|---|---|---|---|---|
| qBittorrent 内置 RSS | ❌ 抓取器不带浏览器指纹，过不了盾 | ❌ | ❌ | ✅ | 排除 |
| AutoBangumi | ⚠️ 主要面向 mikanani.me，CF 会话管理弱 | ⚠️ | ❌ 无限额概念 | ⚠️ Docker 为主 | 排除 |
| ani-rss | ⚠️ 同上 | ⚠️ | ❌ | ⚠️ Docker 为主 | 排除 |
| **自研脚本（本仓库）** | ✅ 浏览器会话 + curl-cffi 指纹 | ✅ | ✅ 限额是核心功能 | ✅ Python 双端 | **采用** |

自研范围很小：Mikan 已提供「我的订阅」聚合 RSS（`/Feed/MyBangumi?token=...`），脚本只需
「拉 RSS → 找新条目 → 下载 .torrent → 交 qBittorrent → 记账限额」五步。

## 6. Cloudflare 会话方案

**关键约束：** 过盾得到的 `cf_clearance` Cookie 绑定 **IP + User-Agent**，且有时效。
因此脚本必须固定 UA、在固定网络环境运行；失效后重新跑一次登录命令即可。

| 方案 | 成本 | 可靠性 | 角色 |
|---|---|---|---|
| A. Playwright 打开真浏览器，人工登录+过盾一次，保存 storage_state | 低 | 高（你的指纹+你的 IP 亲手过盾） | **主路线**（对应你说的「提供初次登录」） |
| B. FlareSolverr 自动过盾 | 中（需常驻服务） | 中（CF 对抗升级时失效） | 兜底，暂不实现 |
| C. 手动从浏览器导出 Cookie 字符串填入配置 | 低 | 低（易过期、易漏字段） | 配置里保留为应急通道（你提到的「不推荐」方式） |

**流程：**

1. `python -m mqd.login` → 弹出真浏览器窗口 → 你登录账号 + 完成 Cloudflare 验证 → 回车；
2. 工具保存 `data/session.json`（cookies + 浏览器 UA）；
3. 日常轮询用 curl-cffi 伪装 Chrome 指纹 + 该会话直接访问；被拦时（HTTP 403/503 或
   challenge 页面特征）抛出明确错误并提示重新跑步骤 1。

## 7. 总体架构

```
┌─────────────────────────── 你的电脑（macOS / Windows）───────────────────────────┐
│                                                                                  │
│  mikan-quota-downloader（Python 常驻 / 计划任务每 20 分钟）                        │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────────────────┐     │
│  │ MikanClient │→│ 去重/记账    │→│ DailyQuota  │→│ QbtClient             │     │
│  │ 订阅RSS+种子 │  │ state.db    │  │ 30GB/日     │  │ add(pause/resume)    │     │
│  └─────┬──────┘   └────────────┘   └────────────┘   └──────────┬───────────┘     │
│        │ HTTPS(带会话+浏览器指纹)                                │ localhost API   │
│  ┌─────▼──────────────┐                              ┌──────────▼───────────┐     │
│  │ mikan.tangbai.cc    │                              │ qBittorrent(+WebUI)  │     │
│  │ (Cloudflare)        │                              │ 下载 → 番剧目录 → 做种 │     │
│  └─────────────────────┘                              └──────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────────────┘
```

模块职责（代码已搭好骨架，见 `src/mqd/`）：

- `session.py`：curl-cffi 客户端；加载/校验会话；识别 CF 拦截并给出明确提示；
- `mikan.py`：解析「我的订阅」聚合 RSS，取新集数；从条目页提取 `/Download/...` 并下载 `.torrent`；
- `torrents.py`：零依赖 bencode 解码，读取种子体积（限额判定要用）；
- `qbittorrent.py`：WebUI API v2 封装（登录、添加、恢复、列表，兼容 4.x/5.x）；
- `quota.py` + `store.py`：30GB 日限额判定 + SQLite 去重与按日流量记账；
- `main.py` / `__main__.py`：单轮检查逻辑与常驻循环（`--once` 供计划任务调用）；
- `login.py`：Playwright 首次登录工具（生成会话文件）。

## 8. 核心流程

### 8.1 首次部署（人工，约 10 分钟）

1. 安装 qBittorrent，开启 WebUI（127.0.0.1:8080，设置账号密码）；
2. `pip install -r requirements.txt`；
3. 复制 `config.example.yaml` → `config.yaml`，粘贴你的「我的订阅」RSS 链接
   （登录网站 → 订阅页右侧 RSS 图标 → 复制含 `token=` 的完整链接）；
4. `python -m mqd.login` 完成首次登录与过盾；
5. `python -m mqd --once` 试跑一轮，确认日志无 Cloudflare 拦截、种子正常入队。

### 8.2 每轮自动检查（每 20 分钟）

```
读 qBt 列表 → 按日归集流量 → 放行等待队列（预算允许时）
→ 拉订阅 RSS → 过滤已见条目（旧→新排序）
→ 逐集：下载 .torrent → 读体积 → 限额判定 → 开始 or 排队（添加为暂停）
→ 全部标记已见 → 记录日志
```

去重键为 RSS 条目 GUID，存 SQLite（`data/state.db`）；换目录重跑不会重复下载。

## 9. 每日 30GB 限额设计

> 注：V2 桌面端已将限额扩展为「下载 + 做种」双日限额、上限可自定义（测试期 1GB/1GB），
> 并新增 BT 流量网卡直连（绕过 VPN），见 [PLAN-DESKTOP.md](PLAN-DESKTOP.md) §6.4/§6.7。
> 本节为 V1 脚本的单一下载限额设计，维持不变。

按**自然日本地日**统计，只统计本管道添加的种子（qBt `category=bangumi` 隔离），
以**实际下载字节**计（qBt API 的 `downloaded` 字段增量记账）：

```
今日已用 used_today      = ledger 中当日新增下载字节之和
在途剩余 active_remaining = Σ 未完成种子的 (size − completed)
剩余预算 budget          = max(0, 30GB − used_today − active_remaining)

新种子体积 ≤ budget → 立即开始
新种子体积 > budget → 添加为「暂停」，进入等待队列
每轮检查：等待队列按加入先后放行，能装下几个放几个（小体积集数可插空）
跨天（本地 0 点）：当日账目封存，预算重置，队列自动恢复流动
```

设计要点：

- 「在途剩余」计入占用，避免 5 个 10GB 种子并发把首日直接打爆；
- 在途种子跨 0 点继续下载的部分**计入新的一天**（符合"按日计流量"直觉）；
- 边界：qBt 中被手动删除的种子停止记账；重新校验/强制恢复产生的重复流量由
  `downloaded` 单调增量自然忽略（校验不产生下载流量）。

## 10. 数据与配置

**SQLite（`data/state.db`）**

| 表 | 用途 |
|---|---|
| `seen(guid, torrent_hash, title, added_at)` | RSS 条目去重 |
| `ledger(hash, size, downloaded)` | 每个种子的已下载字节数快照（求增量） |
| `daily(day, used)` | 每自然日实际下载量 |

**配置（`config.yaml`，不入 Git；模板见 `config.example.yaml`）**：站点地址、订阅 RSS
（含 token）、会话文件路径、qBt 地址/账号/分类/保存路径/做种比率、`daily_limit_gb`、
检查间隔。应急通道：手动 Cookie 串（不推荐）。

## 11. 跨平台部署

| | macOS | Windows |
|---|---|---|
| qBittorrent | 官网 dmg（Apple Silicon 原生） | 官网安装器；可设为开机自启 |
| 脚本运行方式 A（常驻） | `launchd` plist（`RunAtLoad` + `KeepAlive`），日志重定向 | 任务计划程序「开机时启动」，或 NSSM 注册为 Windows 服务 |
| 脚本运行方式 B（定时拉起） | launchd `StartCalendarInterval` / cron 调 `python -m mqd --once` | 任务计划程序每 20 分钟调 `python -m mqd --once` |

两种方式等价，配置文件完全通用；推荐 A（常驻），失败自动重启、日志集中。

## 12. 开发里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 计划书 + 仓库 + 脚手架（限额/记账/qBt 客户端已含单测） | ✅ 本次交付 |
| M1 | qBt 客户端与限额逻辑真机联调（对 localhost API，不依赖站点） | 待做，纯本地 |
| M2 | 登录与会话链路（Playwright 首登、CF 拦截识别） | 待做，需真实网络环境 |
| M3 | 订阅→种子→入队全链路联调、`--once` 幂等验证 | 待做 |
| M4 | 部署产物（launchd plist / Windows 任务计划模板）、通知推送（Bark/TG，可选） | 待做 |

## 13. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Cloudflare 策略升级，会话失效更快 | 检查失败 | 拦截识别 + 明确报错提示重跑登录；必要时引入 FlareSolverr（M4 备选） |
| `cf_clearance` 与 IP/UA 绑定 | 换网络后失效 | 固定家庭网络运行；配置记录 UA；换网后重跑 `login` |
| 站点登录表单加 Turnstile 导致脚本无法账密登录 | 首登失败 | 本方案本就依赖真浏览器登录，不受影响 |
| Mikan 页面结构变化 | 提取下载链接失败 | 链接提取规则集中在 `mikan.py` 单点维护；RSS 结构由 feedparser 兜底 |
| qBt 大版本 API 变化（4→5 已发生） | 命令失效 | 客户端已做 `paused/stopped`、`resume/start` 双名兼容 |
| 磁盘写满 | 下载失败/损坏 | qBt 侧设置做种比率上限（`ratio_limit`）+ 定期清理；后续可加磁盘水位检查 |
| 版权合规 | — | 仅供个人学习研究，请遵守当地法律与站点规则，支持正版 |

## 14. 仓库目录结构

```
mikan-quota-downloader/
├── PLAN.md               ← 本计划书
├── README.md             ← 快速上手
├── requirements.txt
├── config.example.yaml   ← 配置模板（复制为 config.yaml 后填写）
├── src/mqd/
│   ├── __main__.py       # 入口：python -m mqd [--once]
│   ├── main.py           # 单轮检查：RSS→种子→限额→qBt
│   ├── login.py          # 首次登录：python -m mqd.login
│   ├── session.py        # 会话与 CF 拦截识别
│   ├── mikan.py          # 订阅 RSS 解析 + 种子下载
│   ├── torrents.py       # bencode 解码取种子体积
│   ├── qbittorrent.py    # qBt WebUI API v2 客户端
│   ├── quota.py          # 30GB 日限额判定
│   ├── store.py          # SQLite 去重 + 按日记账
│   └── config.py         # 配置加载与校验
└── tests/test_quota.py   # 限额/记账/体积解析单测
```
