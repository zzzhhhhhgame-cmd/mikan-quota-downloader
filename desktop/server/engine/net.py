"""网卡枚举：为「BT 直连（绕过 VPN）」提供可绑定的物理网卡候选。"""

from __future__ import annotations

import socket


def list_bind_candidates() -> list[tuple[str, str]]:
    """返回 [(网卡名, IPv4)]，供设置页下拉选择 BT 绑定网卡。

    优先用 psutil 精确枚举；未安装时退化为探测默认路由出口 IP——注意若 VPN 以
    TUN/全隧模式接管了路由，该值可能是 VPN 虚拟网卡，需在设置页人工核对。
    """
    try:
        import psutil

        out = []
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    out.append((name, addr.address))
        return out
    except ImportError:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            probe.close()
            return [("default-route", ip)]
        except OSError:
            return []
