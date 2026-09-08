# mikan-quota-downloader

针对 Mikan 镜像站（`mikan.tangbai.cc`，Cloudflare 防护）的订阅自动追番下载器：
定期检查订阅更新，自动抓取种子交给下载引擎（内嵌 libtorrent / qBittorrent），执行
**下载量 + 做种上传量双日限额**（上限可自定义，测试期 1GB/1GB；超出自动排队、次日续传），
BT 流量绑定物理网卡**直连不经过 VPN**。macOS / Windows 双端可用。

> 📋 完整方案（选型对比、Cloudflare 会话策略、限额算法、部署方式、里程碑）见
> [PLAN.md](PLAN.md)。
>
> 🖥️ V2 进行中：抛弃 qBittorrent 界面，基于其内核 libtorrent 做一体化桌面应用
> （订阅管理 + 下载 + 限额可视化），见 [PLAN-DESKTOP.md](PLAN-DESKTOP.md)。

## 状态

🚧 V1 脚本与 V2 桌面端并行推进：V1 的限额/记账/qBt 客户端可用；V2 已完成引擎抽象
（内嵌 libtorrent 主线 + qBt 后备）、**下载/做种双限额守门**（测试期 1GB/1GB）、
**BT 网卡直连**、**指定下载地址**（全局默认 + 任务级覆盖）、桌面壳与登录向导，
63 项单测全绿；订阅追更闭环（M3）待做。

## 快速开始

```bash
# 1. 安装 qBittorrent 并启用 WebUI（127.0.0.1:8080 + 账号密码）
# 2. 安装依赖
pip install -r requirements.txt

# 3. 写配置
cp config.example.yaml config.yaml
#    填入：站点地址、你的「我的订阅」RSS 链接（含 token）、qBt 密码

# 4. 首次登录（弹出真浏览器，登录账号 + 过 Cloudflare 验证后回车）
pip install playwright && playwright install chromium
python -m mqd.login

# 5. 试跑一轮
python -m mqd --once

# 6. 常驻运行
python -m mqd
```

### 桌面端（V2，开发预览）

```bash
# 依赖（需 Python 3.10+，本仓库已验证 3.14）
python3 -m venv .venv
.venv/bin/pip install -e ".[desktop]"

# 桌面应用（默认弹窗；pywebview 不可用时自动退化为浏览器）
.venv/bin/python desktop/app.py --config config.yaml
# 或强制浏览器模式
.venv/bin/python desktop/app.py --browser
```

应用内提供：

- **双限额仪表盘**与在线调整（自动回写 config.yaml）；
- **指定下载地址**：设置页保存全局默认下载目录（绝对路径校验、自动建目录、持久化），
  手动添加种子时可按次覆盖；改动只影响之后新增的任务；
- **手动添加种子**：选 `.torrent` 文件 → 可选本次目录与「边下边看」→ 添加；
  同样受每日下载限额约束（装不下自动进等待队列）；
- **任务控制**：暂停/恢复/删除、限速；
- **会话向导**：「登录 Mikan → 我已登录完成」两步（弹真浏览器过 Cloudflare 后自动收割
  会话），另有手动 Cookie 导入兜底；
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
- 会话过期后重新运行 `python -m mqd.login` 即可；
- 仅供个人学习研究，请遵守当地法律与站点规则，支持正版。
