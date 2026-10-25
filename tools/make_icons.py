"""web/icon-*.png を作る（icon.svg と同じ図柄）。標準ライブラリだけで描く。

実行: python tools/make_icons.py
"""
import os
import struct
import zlib

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
BG = (0x14, 0x14, 0x14)
# icon.svg の 64x64 座標で (x, y, 幅, 高さ, 色)
BARS = [(12, 36, 9, 16, (0x2e, 0x6b, 0x45)),
        (27, 26, 9, 26, (0x3f, 0xae, 0x68)),
        (42, 14, 9, 38, (0x4a, 0xde, 0x80))]


def png(size: int) -> bytes:
    k = size / 64
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            x, y = px / k, py / k
            c = BG
            for bx, by, bw, bh, col in BARS:
                if bx <= x < bx + bw and by <= y < by + bh:
                    c = col
            row += bytes(c)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    # iOSは角丸を自動で付けるので、PNGは四角のまま塗りつぶす
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


if __name__ == "__main__":
    for s in (180, 192, 512):
        with open(os.path.join(WEB, f"icon-{s}.png"), "wb") as f:
            f.write(png(s))
        print("wrote", f"icon-{s}.png")
