# mikan-quota-downloader

针对 Mikan 镜像站（`mikan.tangbai.cc`，Cloudflare 防护）的订阅自动追番下载器：
定期检查你的订阅更新，自动抓取种子交给 **qBittorrent** 下载，并强制执行
**每日 30GB** 下载限额（超出自动排队、次日续传）。macOS / Windows 双端可用。

> 📋 完整方案（选型对比、Cloudflare 会话策略、限额算法、部署方式、里程碑）见
> [PLAN.md](PLAN.md)。
>
> 🖥️ V2 规划中：抛弃 qBittorrent 界面，基于其内核 libtorrent 做一体化桌面应用
> （订阅管理 + 下载 + 限额可视化），见 [PLAN-DESKTOP.md](PLAN-DESKTOP.md)。

## 状态

🚧 规划与脚手架阶段：限额判定、按日记账、qBittorrent API 客户端已实现并带单元测试；
站点会话与订阅链路待真机联调（见 PLAN.md 里程碑 M1–M4）。

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

按自然日统计**实际下载字节**（只统计本工具添加的种子，通过 qBt 分类 `bangumi` 隔离）。
剩余预算 = 30GB − 今日已用 − 在途种子剩余量；装得下的新集立即下载，装不下的添加为
暂停进入等待队列，之后每轮检查按加入顺序自动放行，跨天预算重置。

## 安全须知

- `config.yaml` 与 `data/`（含登录会话）已在 `.gitignore` 中，**不要提交或外传**；
- 会话过期后重新运行 `python -m mqd.login` 即可；
- 仅供个人学习研究，请遵守当地法律与站点规则，支持正版。
