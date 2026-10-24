#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Memphis 风格最小化图标 (.ico)。仅用标准库，无外部依赖。"""
import struct
import sys


def make_ico(path, size=256):
    # PNG 格式的 ICO（Vista+ 支持），32bpp BGRA
    px = bytearray()
    for y in range(size):
        px.append(0)  # PNG filter byte: none
        for x in range(size):
            # 背景：近白 oklch(0.985)≈#FAF9F6
            r, g, b, a = 0xFA, 0xF9, 0xF6, 0xFF
            # 黑色描边方块
            m = 14
            if m <= x < size - m and m <= y < size - m:
                r, g, b = 0x1A, 0x1A, 0x1A
            # 内部浅灰面
            m2 = 26
            if m2 <= x < size - m2 and m2 <= y < size - m2:
                r, g, b = 0xF2, 0xF1, 0xEC
            # 锈红点（Minimalism 的唯一强调色，只点一处）
            cx, cy, rad = size * 0.68, size * 0.34, size * 0.10
            if (x - cx) ** 2 + (y - cy) ** 2 <= rad * rad:
                r, g, b = 0xA8, 0x51, 0x2E
            # 三个黑色小方块（Memphis 母题）
            for (bx, by) in ((0.30, 0.62), (0.44, 0.62), (0.58, 0.62)):
                if (abs(x - size * bx) < size * 0.045) and (abs(y - size * by) < size * 0.045):
                    r, g, b = 0x1A, 0x1A, 0x1A
            px += bytes((b, g, r, a))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib_crc(tag + data))

    import zlib
    raw = bytes(px)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    # ICO 容器
    icondir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), 22)
    with open(path, "wb") as f:
        f.write(icondir + entry + png)


def zlib_crc(data):
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "panel.ico"
    make_ico(out)
    print("icon ->", out)
