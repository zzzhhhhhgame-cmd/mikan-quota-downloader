# 使用手册（macOS / Windows）

> 适用：V2 桌面应用（开发预览）+ V1 订阅脚本 ｜ 更新日期：2026-09-08
> 你的 Mac 已完成的安装步骤标记 ✅；需要你亲自做的步骤都有「 👤 人工操作」标头，
> 并给出 **在哪 → 做什么 → 预期看到什么 → 如何确认成功**。

---

## 0. 五分钟上手（最少步骤）

```bash
cd /Users/<你的用户名>/Documents/Anime/mikan-quota-downloader
.venv/bin/python desktop/app.py --config config.yaml   # ✅ 环境已就绪，直接可跑
```

窗口打开后只需三件人工事项：
1. 在「下载目录」卡片设置你的番剧保存路径，点 **保存默认下载目录**；
2. 在「订阅（RSS 链接）」卡片粘贴你想追的番剧 RSS 链接，点 **添加订阅**（**无需登录账号**，见 §6.5）；
3. （可选，仅当订阅显示"异常：Cloudflare 拦截"时）按 §4 方式 B 导入浏览器 Cookie。

之后每一步的细节见下文对应章节。

---

## 1. 准备与安装

### 1.1 Python（👤 人工操作：仅新机器需要）

- **macOS**：终端执行 `python3 -V`，需 ≥ 3.10（你的机器已是 3.14.7 ✅）。
  若需安装：`brew install python@3.12`
- **Windows**：到 [python.org](https://www.python.org/downloads/) 装最新版，
  安装时勾选 **Add python.exe to PATH**。验证：PowerShell 执行 `py -V`。

### 1.2 虚拟环境与依赖（首次一次即可）

```bash
cd /Users/<你的用户名>/Documents/Anime/mikan-quota-downloader
python3 -m venv .venv
.venv/bin/pip install -e ".[desktop]"        # Windows: .venv\Scripts\pip install -e ".[desktop]"
```

✅ 你的机器已完成（`.venv`，Python 3.14.7）。

### 1.3 内嵌下载引擎 libtorrent（macOS 可选但推荐）

不装也能跑（自动回退 qBittorrent 引擎），装了则是"qBt 同款内核"进程内运行：
✅ 你的机器已完成。

```bash
brew install libtorrent-rasterbar
echo "$(brew --prefix libtorrent-rasterbar)/lib/python3.14/site-packages" \
  > .venv/lib/python3.14/site-packages/libtorrent-brew.pth
```

> 注意：`.pth` 里的 `python3.14` 要与 venv 的 Python 版本一致；brew 升级大版本后若失效，
> 重新执行第二条命令即可。Windows 用户不需要本步骤（`pip` 自动装轮子）。

### 1.4 qBittorrent（可选，仅后备引擎用）

只有在 `config.yaml` 里把 `desktop.engine` 设为 `qbittorrent` 时才需要：
安装后打开 qBittorrent → **工具 → 选项 → Web UI** → 勾选「Web 用户界面」，
端口保持 8080，设置账号密码（Windows 防火墙首次会弹窗，选「允许」）。

### 1.5 启动桌面应用

```bash
.venv/bin/python desktop/app.py --config config.yaml   # 窗口模式
.venv/bin/python desktop/app.py --browser              # 浏览器模式（窗口打不开时用）
```

关闭窗口即退出应用。**👤 人工操作**：启动后看主界面左上角——
「引擎：LibtorrentEngine」出现即成功；若显示「服务异常」，把红色文字截图发给开发者（我）。

---

## 2. 配置文件 config.yaml（逐项说明）

文件不存在时执行 `cp config.example.yaml config.yaml`。所有项都可在应用界面改的
会自动回写此文件，无需手动编辑。

| 配置项 | 说明 | 你需要做的 |
|---|---|---|
| `mikan.base_url` | 站点地址 | 保持 `https://mikan.tangbai.cc` |
| `mikan.rss_url` | 你的订阅聚合 RSS（V1 脚本用；桌面端 M3 起改为应用内订阅） | 见 §10.1 获取后填入 |
| `mikan.session_file` | 会话文件位置 | 保持默认 `data/session.json` |
| `mikan.cookie_string` | 手动 Cookie 兜底（不推荐长期用） | 一般留空，用界面导入即可 |
| `qbittorrent.*` | 后备引擎的 qBt 连接信息 | 仅 `desktop.engine=qbittorrent` 时需要 |
| `quota.daily_limit_gb` | **下载日限额** | 测试期保持 1；正式使用改 30 |
| `quota.seed_limit_gb` | **做种/上传日限额** | 测试期保持 1；不想限做种设 0 |
| `monitor.interval_minutes` | 订阅检查间隔（分钟） | 20 即可 |
| `desktop.engine` | `auto` / `libtorrent` / `qbittorrent` | 保持 `auto` |
| `desktop.save_path` | **默认下载目录**（绝对路径） | 界面里设置，见 §5 |
| `desktop.bind_ip` | BT 直连绑定的物理网卡 IP，留空=跟随系统路由 | 见 §9 |
| `desktop.bt_port` | BT 监听端口（仅内嵌引擎） | 保持 6881 |

---

## 3. 界面导览

| 卡片 | 功能 |
|---|---|
| 今日限额 | 下载/上传两条进度条 + 剩余预算；「保存限额」按钮；「上传闸门」徽章（做种触顶时变红=已停上传） |
| 站点会话 | 登录向导两步按钮、Cookie 导入兜底、「测试会话可用性」 |
| 下载目录 | 全局默认下载目录的查看与保存 |
| 手动添加种子 | 选 .torrent 文件、可选本次目录与「边下边看」 |
| 全局限速 | 速度层限速（KB/s），与容量限额相互独立 |
| 下载任务 | 任务表：状态/进度/速度/暂停/恢复/删除，每 2.5 秒自动刷新 |

---

## 4. 登录 Mikan（👤 人工操作 · 关键步骤）

### 方式 A：应用内登录向导（推荐，可过 Cloudflare）

1. **在哪**：主界面「站点会话」卡片。
2. **做什么**：点 **「① 登录 Mikan（弹窗）」**。
3. **预期**：约 1~2 秒后弹出一个新窗口，标题为「登录 Mikan（完成后回到应用窗口点
   『我已登录完成』）」，页面显示 Mikan 的登录表单。
   *若 30 秒没弹窗：先用浏览器模式（§1.5 第二条命令）确认服务正常，再向我反馈。*
4. 在新窗口中：输入你的 Mikan 账号密码 → 点站点自己的「登录」按钮。
5. 若出现 Cloudflare 的 **「确认您是真人」** 勾选框/旋转图标：按提示点击勾选，
   等页面自动跳转（通常 3~5 秒）。这是真实浏览器环境，与平时网页操作完全一样。
6. **如何确认登录成功**：页面右上角/个人区域显示你的用户名。
7. **回到主应用窗口**，点 **「② 我已登录完成」**。
8. **预期**：右下角弹出提示「会话已保存（N 条 Cookie）」，登录窗口自动关闭，
   卡片上方状态变为绿色的「已配置」。
9. **确认成功**：点 **「测试会话可用性」**，下方显示 **✓ 会话可用**。
   若显示「✗ Cloudflare 拦截…」，回到第 2 步重做一次（刚过盾就失效的情况罕见）。

> 会话过期后的表现：任务/限额正常但日志或测试按钮提示 Cloudflare 拦截。
> 处理：重做本节步骤 A 即可，约 1 分钟。

### 方式 B：手动导入 Cookie（兜底，不推荐长期使用）

适用于方式 A 报「收割失败」（Windows 旧版 WebView2 可能出现）。

1. 用你平时能打开 Mikan 的浏览器登录 `mikan.tangbai.cc`。
2. 按 **F12** 打开开发者工具 → 选 **网络 / Network** 标签 → 按 **F5 刷新页面**。
3. 在请求列表点**第一条**（文档请求）→ 右侧 **请求标头 / Request Headers** →
   找到 `Cookie:` 开头的一行 → **复制冒号后的完整内容**（不要带 `Cookie:` 三个字母）。
   *Chrome/Edge 也可走「应用/Application → Cookies → mikan.tangbai.cc」，逐条拼成
   `名字=值; 名字=值` 的格式，至少要包含 `cf_clearance`。*
4. 回到应用「站点会话」卡片最下方的文本框，粘贴 → 点 **「导入 Cookie（兜底）」**。
5. **👤 额外必做**：cf_clearance 绑定浏览器 UA。在开发者工具的 **控制台 / Console**
   输入 `navigator.userAgent` 回车，复制输出结果，填到 `config.yaml` 的
   `mikan.user_agent: "…"` 里（`session.json` 已存 UA 时以它为准）。
6. **确认成功**：同方式 A 第 9 步。

### 清除会话

「清除会话」按钮会删除 `data/session.json`，下次检查会被 Cloudflare 拦截——
除非换账号，否则不要点。

---

## 5. 指定下载地址（👤 人工操作）

1. **在哪**：「下载目录」卡片。
2. **做什么**：输入框填**绝对路径**，例如：
   - macOS：`/Users/<你的用户名>/Movies/Bangumi`
   - Windows：`D:\Bangumi`
3. 点 **「保存默认下载目录」**。目录不存在会自动创建；配置自动写回 `config.yaml`。
4. **预期**：按钮旁显示「当前：你的路径」。
5. **确认成功**：终端执行 `ls /Users/<你的用户名>/Movies/Bangumi`（或对应路径）能看到目录存在。

> 规则：修改只影响**之后新增**的任务，已在途任务保持原目录。
> 「手动添加种子」时可临时填「本次下载目录」覆盖默认值。
> ⚠️ 目录和请求都没指定时会明确报错，不会把文件下到未知位置。

---

## 6. 手动添加一个种子（👤 人工操作：推荐先测一次）

1. 任意渠道下载一个小的 `.torrent` 文件（或用 Mikan 任意种子页的下载按钮）。
2. **在哪**：「手动添加种子」卡片 → 点 **选择文件** 选中它。
3. 「本次下载目录」留空（用默认），需要边下边看就勾选「顺序下载」。
4. 点 **「添加任务」**。
5. **预期**：提示「已开始下载：<目录>」，「下载任务」表出现该任务，
   状态「下载中」，限额进度条微微上涨。
6. 若提示「今日限额不足，已进入等待队列」：说明剩余预算不够，任务以暂停态排队，
   明日 0 点自动开始（测试期限额只有 1GB，属正常现象）。
7. **确认成功**：任务表状态从「下载中」变为「已完成」（有 peer 时），
   或至少文件出现在下载目录（`ls` 一下）。

---

## 6.5 订阅（RSS 链接自动追更，无需登录）👤 人工操作

> 这是为"过不了 Cloudflare 登录"设计的路线：**全程不需要 Mikan 账号**。
> 实测不带 Cookie 也能直接拉取镜像站的 RSS；万一被拦，按第 6 步导入 Cookie 即可。

1. **获取 RSS 链接**：用浏览器打开 Mikan → 进入你想追的番剧详情页 → 页面右侧找到
   **「RSS订阅」图标/链接** → 右键 **复制链接地址**。链接形如：
   `https://mikan.tangbai.cc/RSS/Bangumi?bangumiId=3992&subgroupid=370`
   （一个番剧可以按字幕组分别订阅：不同 subgroupid 各复制一条。）
2. **在哪**：主界面「订阅（RSS 链接）」卡片。
3. **做什么**：把链接粘贴到输入框 → 「名称」可留空（自动取番剧名）→
   「目录」留空用默认下载目录（也可为本番单独指定）→ 点 **「添加订阅」**。
4. **预期**：提示「订阅成功：共 N 条，新开始 X 条，排队 Y 条」——该番剧**已有的集数
   立即开始下载**（受每日限额约束，装不下的自动排队）。
5. **自动追更**：之后每隔「检查间隔」（默认 20 分钟，可在卡片里修改并保存）自动检查
   该 RSS；出现新集就自动下载。已下载过的集数按 GUID 去重，**不会重复下载**；
   番剧 RSS 本身是滚动窗口（只保留最近若干条），不影响订阅。
6. **如果提示「被 Cloudflare 拦截」**：订阅已保存，不会丢。处理：
   ① 按手册 §4 方式 B，在能打开 Mikan 的浏览器按 F12 → 网络 → 复制整行 Cookie
   （**不需要登录账号**）→ 粘贴到「站点会话」卡片导入；② 回到订阅卡片点「立即检查」。
7. **确认成功**：订阅列表该行状态显示绿色「正常」+ 最近检查时间；
   「下载任务」表出现该番的集数；新周更集数播出后 20 分钟内自动出现在任务表。
8. **管理**：「立即检查」手动触发；「停用」暂停某个订阅；「删除」移除
   （已下载的文件不受影响）。

---

## 7. 限额调整（👤 人工操作）

- **在哪**：「今日限额」卡片。
- 测试期保持 **1 GB / 1 GB**；正式使用把下载改成 30，做种按需（0 = 不限）。
- 填数字 → 点 **「保存限额」** → 进度条刻度立即变化，配置自动回写 `config.yaml`。
- **确认成功**：重新打开应用，数值仍是保存的值。

限额语义速查：按**自然日（本地 0 点重置）**、只统计本应用添加的种子、按**实际流量**
计（不是文件大小）；下载触顶 → 新任务排队次日续传；做种触顶 → 当日「上传闸门」关闭
（停止上传），次日自动恢复。

---

## 8. 全局限速（可选）

「全局限速」卡片：填 KB/s（0 或留空 = 不限）→ 点「应用限速」。
这是"跑多快"，与每日限额的"每天跑多少"是两层，互不冲突。

---

## 9. BT 直连（下载/做种不走 VPN，👤 人工操作）

原理：把 BT 流量绑定到物理网卡，即使 VPN 开 TUN/全隧模式也走物理网络。

1. **关掉 VPN**（或断开 TUN 模式），查物理网卡 IP：
   - macOS 终端：`ipconfig getifaddr en0`（Wi-Fi 通常是 en0，以太网可能是 en1）
   - Windows PowerShell：`ipconfig` → 看「无线局域网适配器 WLAN」或「以太网」的 IPv4 地址
2. 打开 `config.yaml`，填 `desktop.bind_ip: "查到的IP"`（例如 `"192.168.1.8"`），保存。
3. 重启应用，点「测试会话可用性」+ 手动添加一个种子确认能连上 peer。
4. **回退**：想恢复走 VPN 时把 `bind_ip` 清空重启即可。
5. **确认成功**：开着 VPN 下载时，VPN 客户端的流量统计不再增长（BT 部分直连）。

> ⚠️ 若绑定后 tracker/peer 全部连不上（你的网络必须经代理出网），清空 `bind_ip` 回退。

---

## 10. V1 订阅脚本（RSS 自动追更，桌面端 M3 前的过渡方案）

### 10.1 获取订阅 RSS 链接（👤 人工操作）

1. 浏览器打开 `https://mikan.tangbai.cc` 并登录。
2. 顶部导航点 **「订阅」**。
3. 页面右侧找到 **RSS 图标**（订阅列表上方），**右键 → 复制链接地址**。
4. 链接形如 `https://mikan.tangbai.cc/Feed/MyBangumi?token=一串字符`——整段复制。
5. 打开 `config.yaml`，填入 `mikan.rss_url: "刚才复制的完整链接"`。
6. **确认成功**：浏览器地址栏粘贴该链接能打开一个 XML 页面，里面有你订阅的番剧条目。

### 10.2 试跑一轮

```bash
.venv/bin/python -m mqd --once
```

👤 看输出：`订阅无更新（今日下载 …）` 或逐条 `开始下载/排队: <集名>` 即成功；
出现 `Cloudflare 拦截` → 去桌面应用重做 §4 登录向导（会话文件两边通用）。

### 10.3 常驻运行

- **macOS（launchd）**：把下面内容存为
  `~/Library/LaunchAgents/cc.anime.mqd.plist`（路径如不同自行替换）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cc.anime.mqd</string>
  <key>ProgramArguments</key><array>
    <string>/Users/<你的用户名>/Documents/Anime/mikan-quota-downloader/.venv/bin/python</string>
    <string>-m</string><string>mqd</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/<你的用户名>/Documents/Anime/mikan-quota-downloader</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/<你的用户名>/Documents/Anime/mikan-quota-downloader/data/mqd.log</string>
  <key>StandardErrorPath</key><string>/Users/<你的用户名>/Documents/Anime/mikan-quota-downloader/data/mqd.err.log</string>
</dict></plist>
```

  加载：`launchctl load -w ~/Library/LaunchAgents/cc.anime.mqd.plist`；
  卸载：`launchctl unload -w …`；看日志：`tail -f data/mqd.log`。

- **Windows（任务计划程序）**：管理员 PowerShell 执行
  `schtasks /create /tn "mqd" /sc minute /mo 20 /tr "\"C:\path\to\.venv\Scripts\python.exe\" -m mqd --once" /working-dir "C:\path\to\repo"`
  （每 20 分钟跑一轮；常驻则去掉 `--once` 并用 `/sc onstart`）。

---

## 11. 常见问题排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 界面显示「引擎不可达」（qBt 引擎时） | qBt 未启动或 WebUI 密码不对 | 启动 qBt，核对 `qbittorrent.password` |
| 「引擎不可达」（内嵌引擎时） | 极少：6881 被占用 | 改 `desktop.bt_port` |
| 窗口闪退 / 提示 pywebview | Python < 3.10 或依赖缺失 | 重建 venv（§1.2）；临时用 `--browser` |
| 添加种子报「不是有效的 .torrent 文件」 | 文件损坏或下载到的是网页 | 重新从种子页下载 .torrent |
| 「测试会话可用性」显示 Cloudflare 拦截 | 会话过期 / 换了网络 | 重做 §4 方式 A |
| 导入 Cookie 后仍不可用 | UA 不一致 | 补做 §4 方式 B 第 5 步 |
| 限额进度条一直是 0 | 任务没有 peer、还没连上 | 正常，连上后开始计数 |
| 手动添加报「未指定下载目录」 | 默认目录未设置且没填本次目录 | 见 §5 |
| 添加订阅报「未指定下载目录」 | 同上 | 先保存默认目录，或添加时填「目录」 |
| 订阅状态显示「异常」 | Cloudflare 拦截或网络失败 | 鼠标悬停"异常"徽章看原因；CF 拦截按 §6.5 第 6 步导入 Cookie |
| 订阅新集一直没出现 | 检查间隔过长 / 订阅被停用 / 源滚动漏集 | 缩短间隔或点「立即检查」；漏集用手动添加补 |

---

## 12. 每次版本更新后的验证清单

我（开发者）每次改动后会自动跑：单元测试 + 实机接口冒烟，并在回复里给出结论。
**需要你人工做的验证**，我会按下面的固定格式给出：

> **👤 人工验证 #N：<标题>**
> 1. 在哪：…
> 2. 做什么：…（精确到点哪个按钮/敲哪条命令）
> 3. 预期看到：…
> 4. 判断成功：…；失败时把什么信息发给我

你现在就可以做的第一组人工验证：

> **👤 人工验证 #1：首次登录与下载闭环（约 5 分钟）**
> 1. 在哪：终端，项目目录。
> 2. 做什么：`.venv/bin/python desktop/app.py --config config.yaml`
> 3. 预期看到：应用窗口，左上角「引擎：LibtorrentEngine」。
> 4. 按 §4 方式 A 完成登录，按 §5 设下载目录，按 §6 添加任一小种子。
> 5. 判断成功：任务表出现「下载中/已完成」+ 下载目录出现文件；
>    失败时把界面提示文字 + 终端最后 20 行发我。

> **👤 人工验证 #2：RSS 订阅自动追更（约 3 分钟，无需登录）**
> 1. 在哪：桌面应用的「订阅（RSS 链接）」卡片。
> 2. 做什么：按 §6.5 粘贴你给的链接
>    `https://mikan.tangbai.cc/RSS/Bangumi?bangumiId=3992&subgroupid=370` → 添加订阅。
> 3. 预期看到：提示「订阅成功：共 N 条…」，任务表开始出现该番集数并下载。
> 4. 把「检查间隔」改成 5 分钟保存；点「立即检查」确认状态为绿色「正常」。
> 5. 判断成功：订阅行显示「正常」+ 最近检查时间；若显示红色「异常」，
>    鼠标悬停看原因（多为 Cloudflare 拦截 → 按 §6.5 第 6 步导入 Cookie 后重试），
>    并把悬停文字发我。

---

*本手册对应 commit `46028fe` 之后的版本。界面按钮或行为有变化时我会同步更新本节。*
