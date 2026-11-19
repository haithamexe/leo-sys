"""The app icon: the critter from the matrix, on a dark rounded square. Standard library only (PNG inside ICO)."""
import struct
import zlib
from pathlib import Path

from . import faces

SIZES = (16, 32, 48, 64, 256)   # each a multiple of the 8-cell grid, so it stays crisp
BG = (21, 17, 14)


def _cells():
    """8 x 8 grid: the 7 x 5 critter centred, background elsewhere."""
    grid = [[None] * 8 for _ in range(8)]
    for y, row in enumerate(faces.CRITTER["open"]):
        for x, bit in enumerate(row):
            grid[y + 2][x] = faces.ORANGE if bit == "o" else BG
    return grid


def _png(size):
    grid, scale = _cells(), size // 8
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            c = grid[y // scale][x // scale] or BG
            row += bytes((*c, 255))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def png_images():
    """[(size, png bytes)] for every icon size, for stamping into leo-sys.exe."""
    return [(s, _png(s)) for s in SIZES]


def ico_bytes():
    images = [_png(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


def write_icon(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico_bytes())
    return path
