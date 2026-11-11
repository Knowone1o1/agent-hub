"""Draws the app icons (no extra libraries needed). Run: py tools/make_icons.py"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static"
S = 256


def png(pixels):
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def draw(bg, node_colors, link):
    nodes = [(128, 70), (68, 176), (188, 176)]
    px = [[0] * (S * 4) for _ in range(S)]
    for y in range(S):
        for x in range(S):
            # rounded square
            r, m = 56, 8
            cx = min(max(x, m + r), S - m - r); cy = min(max(y, m + r), S - m - r)
            if math.hypot(x - cx, y - cy) > r:
                continue
            col = bg
            for (ax, ay) in nodes[1:]:  # links from the top node (the manager) to the team
                bx, by = nodes[0]
                t = max(0, min(1, ((x - bx) * (ax - bx) + (y - by) * (ay - by)) / ((ax - bx) ** 2 + (ay - by) ** 2)))
                if math.hypot(x - (bx + t * (ax - bx)), y - (by + t * (ay - by))) < 7:
                    col = link
            for (nx, ny), c, rad in zip(nodes, node_colors, (34, 28, 28)):
                d = math.hypot(x - nx, y - ny)
                if d < rad:
                    col = c
                elif d < rad + 6:
                    col = bg
            px[y][x * 4:x * 4 + 4] = [*col, 255]
    return png(px)


def ico(png_bytes, path):
    header = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
    path.write_bytes(header + png_bytes)


if __name__ == "__main__":
    app = draw((47, 91, 211), [(255, 255, 255), (255, 214, 170), (255, 214, 170)], (190, 205, 245))
    dash = draw((22, 24, 27), [(63, 178, 127), (245, 165, 36), (107, 140, 255)], (70, 74, 80))
    ico(app, OUT / "app.ico"); (OUT / "app.png").write_bytes(app)
    ico(dash, OUT / "dashboard.ico"); (OUT / "dashboard.png").write_bytes(dash)
    print("icons written to", OUT)
