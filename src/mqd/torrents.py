"""零依赖 bencode 解码：从 .torrent 原始字节中取出总体积（限额判定用）。"""


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
