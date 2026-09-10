"""生成应用图标（纯 Python，无第三方依赖）：蓝底圆角方块 + 白色下载箭头。

用法：python make_icon.py 输出路径.png
"""

import struct
import sys
import zlib

SIZE = 1024
RADIUS = 230
ACCENT_TOP = (37, 99, 235)     # #2563eb
ACCENT_BOTTOM = (29, 78, 216)  # #1d4ed8
WHITE = (255, 255, 255, 255)


def inside_rounded(x, y):
    if x < RADIUS and y < RADIUS:
        return (x - RADIUS) ** 2 + (y - RADIUS) ** 2 <= RADIUS ** 2
    if x >= SIZE - RADIUS and y < RADIUS:
        return (x - (SIZE - RADIUS)) ** 2 + (y - RADIUS) ** 2 <= RADIUS ** 2
    if x < RADIUS and y >= SIZE - RADIUS:
        return (x - RADIUS) ** 2 + (y - (SIZE - RADIUS)) ** 2 <= RADIUS ** 2
    if x >= SIZE - RADIUS and y >= SIZE - RADIUS:
        return (x - (SIZE - RADIUS)) ** 2 + (y - (SIZE - RADIUS)) ** 2 <= RADIUS ** 2
    return True


def in_arrow(x, y):
    """下箭头：箭杆 + 三角箭头。"""
    if 462 <= x <= 562 and 250 <= y <= 560:
        return True
    if 520 <= y <= 700:
        half = (y - 520) * 190 / 180
        return abs(x - 512) <= half
    return False


def in_tray(x, y):
    return 270 <= x <= 754 and 760 <= y <= 820


def pixel(x, y):
    if not inside_rounded(x, y):
        return (0, 0, 0, 0)
    r = int(ACCENT_TOP[0] + (ACCENT_BOTTOM[0] - ACCENT_TOP[0]) * y / SIZE)
    g = int(ACCENT_TOP[1] + (ACCENT_BOTTOM[1] - ACCENT_TOP[1]) * y / SIZE)
    b = int(ACCENT_TOP[2] + (ACCENT_BOTTOM[2] - ACCENT_TOP[2]) * y / SIZE)
    if in_arrow(x, y) or in_tray(x, y):
        return WHITE
    return (r, g, b, 255)


def write_png(path):
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)  # 每行前置过滤字节
        for x in range(SIZE):
            rows += bytes(pixel(x, y))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    write_png(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
    print("icon written")
