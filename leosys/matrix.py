"""Live frames for the AMG65 dot matrix (63 x 5, RGB), op 0x35 - RAM only, no flash write.

Byte layout copied exactly from the official driver; see docs/PROTOCOL.md section 1.
"""
from .hid import HidError

WIDTH, HEIGHT = 63, 5
LEDS = WIDTH * HEIGHT          # 315
FRAME_BYTES = 960              # 15 chunks of 64
CHUNK = 64


def led_index(x, y):
    """Screen position -> wire index. The matrix is five panels side by side (14, 14, 14, 14
    and 7 columns wide), each numbered row by row. Same map as the driver's own table."""
    panel, px = (x // 14, x % 14) if x < 56 else (4, x - 56)
    width = 14 if panel < 4 else 7
    return panel * 70 + y * width + px


def encode(pixels):
    """pixels: list of HEIGHT rows, each WIDTH (r, g, b) tuples -> 960-byte frame buffer."""
    buf = bytearray(FRAME_BYTES)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = pixels[y][x]
            i = 3 * led_index(x, y)
            buf[i:i + 3] = bytes((r & 255, g & 255, b & 255))
    buf[946:948] = b'\xaa\x55'
    return bytes(buf)


def _expect(cmd, op, timeout_ms=500):
    r = cmd.read(timeout_ms)
    if r is None:
        raise HidError(f'no reply to op 0x{op:02x}')
    return r


def send_frame(cmd, pixels):
    """Send one live frame. Mirrors the driver: 04 18, 04 35 .. 0f, 15 data + 1 zero chunk, 04 02."""
    frame = encode(pixels)
    cmd.flush()
    cmd.write(b'\x04\x18'); _expect(cmd, 0x18)
    cmd.write(b'\x04\x35\x00\x00\x00\x00\x00\x00\x0f'); _expect(cmd, 0x35)
    for i in range(0, FRAME_BYTES, CHUNK):
        cmd.write(frame[i:i + CHUNK]); _expect(cmd, 0x41)
    cmd.write(bytes(CHUNK)); cmd.read(200)          # trailing zero chunk the driver always sends
    cmd.write(b'\x04\x02'); _expect(cmd, 0x02)


def blank():
    return [[(0, 0, 0)] * WIDTH for _ in range(HEIGHT)]
