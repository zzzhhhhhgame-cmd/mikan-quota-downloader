"""入口：python -m mqd [--once]"""

import argparse
import logging
import time

from .config import load_config
from .main import run_once


def main():
    parser = argparse.ArgumentParser(prog="mqd", description="Mikan 订阅监控 + qBittorrent + 每日限额")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出（供计划任务调用）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = load_config(args.config)

    if args.once:
        run_once(cfg)
        return

    interval = float(cfg["monitor"].get("interval_minutes", 20)) * 60
    while True:
        try:
            run_once(cfg)
        except Exception:
            logging.exception("本轮检查失败，下轮重试")
        time.sleep(interval)


if __name__ == "__main__":
    main()
