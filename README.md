# mikan-quota-downloader

针对 Mikan 镜像站（`mikan.tangbai.cc`，Cloudflare 防护）的订阅自动追番下载器：
定期检查订阅更新，自动抓取种子交给下载引擎（内嵌 libtorrent / qBittorrent），执行
**下载量 + 做种上传量双日限额**（上限可自定义，测试期 1GB/1GB；超出自动排队、次日续传），
BT 流量绑定物理网卡**直连不经过 VPN**。macOS / Windows 双端可用。

> 📋 完整方案（选型对比、Cloudflare 会话策略、限额算法、部署方式、里程碑）见
> [PLAN.md](PLAN.md)。
>
> 📖 **使用手册（安装、订阅、下载目录、限额调整、排查，含逐步人工操作说明）见
> [USAGE.md](USAGE.md)。**
>
> 🖥️ V2 进行中：抛弃 qBittorrent 界面，基于其内核 libtorrent 做一体化桌面应用
> （订阅管理 + 下载 + 限额可视化），见 [PLAN-DESKTOP.md](PLAN-DESKTOP.md)。

## 状态

🚧 V1 脚本与 V2 桌面端并行推进：V2 已完成引擎抽象（内嵌 libtorrent 主线 + qBt 后备）、
**下载/做种双限额守门**（测试期 1GB/1GB）、**BT 网卡直连**、**指定下载地址**、
**RSS 链接订阅（无需登录，定时自动追更，可重命名，删除可恢复，未下完自动补拉）**、
**镜像域名免疫（订阅与域名解耦，多镜像自动切换）**、**磁力链接添加**、**做种可视化**
与**侧栏式浅色 UI**。102 项单测全绿；真实引擎与真实 RSS 均已实机验证。
剩余：番剧页注入订阅。

## 快速开始

```bash
# 1. 安装 qBittorrent 并启用 WebUI（127.0.0.1:8080 + 账号密码）
# 2. 安装依赖
pip install -r requirements.txt

# 3. 写配置
cp config.example.yaml config.yaml
#    填入：站点地址、你的「我的订阅」RSS 链接（含 token）、qBt 密码

# 4. 试跑一轮（订阅下载无需登录；被 CF 拦截时按 USAGE.md §4 导入 Cookie）
python -m mqd --once

# 5. 常驻运行
python -m mqd
```

### 桌面端（V2，开发预览）

```bash
# 依赖（需 Python 3.10+，本仓库已验证 3.14）
python3 -m venv .venv
.venv/bin/pip install -e ".[desktop]"

# macOS 内嵌引擎（可选；不装则自动回退 qBt 引擎）
brew install libtorrent-rasterbar
echo "$(brew --prefix libtorrent-rasterbar)/lib/python3.14/site-packages" \
  > .venv/lib/python3.14/site-packages/libtorrent-brew.pth

# 桌面应用（默认弹窗；pywebview 不可用时自动退化为浏览器）
.venv/bin/python desktop/app.py --config config.yaml
# 或强制浏览器模式
.venv/bin/python desktop/app.py --browser
```

应用内提供：

- **订阅（RSS 链接，无需登录）**：粘贴任意 Mikan RSS 链接（如番剧页的
  `/RSS/Bangumi?bangumiId=…&subgroupid=…`）→ 立即下载全量集数，之后按可设定的间隔
  自动检查新集（GUID 去重）；订阅可重命名；被 Cloudflare 拦截时按提示导入 Cookie
  （**无需登录账号**，浏览器能打开站点即可）；
- **双限额仪表盘**与在线调整（自动回写 config.yaml）；
- **指定下载地址**：设置页保存全局默认下载目录（绝对路径校验、自动建目录、持久化），
  手动添加时可按次覆盖；改动只影响之后新增的任务；
- **手动添加**：磁力链接或 .torrent 文件直接添加，可勾选「边下边看」；同样受每日
  下载限额约束（磁链体积在元数据到达后补计，超预算自动排队）；
- **任务控制与做种可视化**：暂停/恢复/删除、限速；「做种中」状态与上传速度一目了然；
- **站点 Cookie 兜底**：被 Cloudflare 拦截时手动导入浏览器 Cookie（**无需登录账号**；
  登录向导已于 2026-09-09 移除；订阅下载通常无需任何 Cookie）；
- 引擎选择 `desktop.engine: auto`（装了 libtorrent 用内嵌引擎，否则自动连 qBt WebUI）；
  BT 直连绑定网卡用 `desktop.bind_ip`。

## 每日限额怎么算

**下载量**与**做种上传量**是两个独立的自然日限额，上限均可在设置页自定义（测试期均 1GB/日），
只统计本工具添加的种子（通过分类/标签隔离）：

- **下载触顶**：装不下的新集以暂停状态进入等待队列，之后按加入顺序自动放行，跨天预算重置；
  剩余预算 = 下载上限 − 今日已用 − 在途种子剩余量；
- **做种触顶**：当日关闭「上传闸门」（全局上传限速压到 ≈0），次日自动恢复，任务不删除；
- **BT 流量直连**：下载与做种绑定物理网卡，不经过 VPN；网页/API 会话不受影响。

## 安全须知

- `config.yaml` 与 `data/`（含登录会话）已在 `.gitignore` 中，**不要提交或外传**；
- 被 Cloudflare 拦截时在应用「站点 Cookie」卡片导入浏览器 Cookie 即可（无需登录）；
- 仅供个人学习研究，请遵守当地法律与站点规则，支持正版。
