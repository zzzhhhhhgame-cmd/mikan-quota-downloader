"""零依赖 bencode 解码：从 .torrent 原始字节中取出体积与 infohash（限额判定/任务键用）；
磁力链接的 infohash 提取。"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit


def _decode(data, i):
    """返回 (值, 下一位置)。字符串/整数/列表/字典均可。"""
    c = data[i : i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1 : end]), end + 1
    if c == b"l":
        i += 1
        items = []
        while data[i : i + 1] != b"e":
            value, i = _decode(data, i)
            items.append(value)
        return items, i + 1
    if c == b"d":
        i += 1
        mapping = {}
        while data[i : i + 1] != b"e":
            key, i = _decode(data, i)
            value, i = _decode(data, i)
            mapping[key] = value
        return mapping, i + 1
    colon = data.index(b":", i)
    length = int(data[i:colon])
    return data[colon + 1 : colon + 1 + length], colon + 1 + length


def torrent_size(raw: bytes) -> int:
    """单文件种子取 info.length；多文件种子取各文件 length 之和。"""
    info = _decode(raw, 0)[0][b"info"]
    if b"files" in info:
        return sum(int(f[b"length"]) for f in info[b"files"])
    return int(info[b"length"])


def info_span(raw: bytes):
    """返回顶层 b"info" 值在原始字节中的区间 (start, end)。

    infohash 的定义是对该区间字节的 SHA-1，必须用原始字节而不是重编码。
    """
    if raw[0:1] != b"d":
        raise ValueError("种子根节点必须是字典")
    i = 1
    while raw[i : i + 1] != b"e":
        key, i = _decode(raw, i)
        start = i
        _value, i = _decode(raw, i)
        if key == b"info":
            return start, i
    raise ValueError("种子中缺少 info 字典")


def infohash_from_bytes(raw: bytes) -> str:
    """计算种子 v1 infohash（hex），与 qBittorrent/libtorrent 一致，作为任务唯一键。"""
    start, end = info_span(raw)
    return hashlib.sha1(raw[start : end]).hexdigest()


def magnet_infohash(uri: str) -> str:
    """从磁力链接提取 v1 infohash（hex）。支持 40 位 hex 与 32 位 base32 两种形式。"""
    xt = (parse_qs(urlsplit(uri.strip()).query).get("xt") or [""])[0]
    if not xt.lower().startswith("urn:btih:"):
        raise ValueError("磁力链接缺少 xt=urn:btih: 信息哈希")
    value = xt[9:]
    if len(value) == 40:
        int(value, 16)  # 校验 hex
        return value.lower()
    if len(value) == 32:
        return base64.b32decode(value.upper()).hex()
    raise ValueError(f"无法识别的 infohash 长度: {len(value)}")
