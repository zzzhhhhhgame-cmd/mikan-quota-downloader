"""mikan-quota-downloader 桌面端后端（V2）。

模块布局：
- engine/   下载引擎抽象与双实现（内嵌 libtorrent / 外部 qBittorrent）
- services/ 业务服务（限额守门 QuotaGuard；M2+ 增加 FastAPI、订阅轮询）
"""
