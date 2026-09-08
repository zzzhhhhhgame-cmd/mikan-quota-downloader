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
（内嵌 libtorrent 主线 + qBt 后备）、**下载/做种双限额守门**（测试期 1GB/1GB）与
**BT 网卡直连**支持，单测全绿；站点会话与订阅链路待真机联调。

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
