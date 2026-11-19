"""Live per-key backlight colours for the AMG65, op 0x20 - no save step, like the matrix frames.

Payload: 8 chunks of 64 bytes = up to 128 entries of [light index, R, G, B], zero-padded.
Byte layout copied from the official driver; see docs/PROTOCOL.md section 4.
"""
from .hid import HidError

CHUNKS = 8
CHUNK = 64

# (name, light index, row 0-4, x centre in the driver picture 0-1000) - from the driver's KeyboardLayout.xml
KEYS = [
    ('Esc', 0, 0, 56),
    ('!1', 17, 0, 106),
    ('@2', 18, 0, 156),
    ('#3', 19, 0, 206),
    ('$4', 20, 0, 256),
    ('%5', 21, 0, 306),
    ('^6', 22, 0, 356),
    ('&7', 23, 0, 406),
    ('*8', 24, 0, 456),
    ('(9', 25, 0, 506),
    (')0', 26, 0, 556),
    ('_-', 27, 0, 606),
    ('+=', 28, 0, 656),
    ('Backspace', 92, 0, 730),
    ('Home', 104, 0, 805),
    ('VOLDEC', 14, 0, 941),
    ('VOLINC', 13, 0, 941),
    ('Tab', 32, 1, 70),
    ('Q', 33, 1, 132),
    ('W', 34, 1, 182),
    ('E', 35, 1, 232),
    ('R', 36, 1, 282),
    ('T', 37, 1, 332),
    ('Y', 38, 1, 382),
    ('U', 39, 1, 432),
    ('I', 40, 1, 482),
    ('O', 41, 1, 532),
    ('P', 42, 1, 582),
    ('{[', 43, 1, 631),
    ('}]', 44, 1, 681),
    ('|\\', 60, 1, 743),
    ('del', 106, 1, 805),
    ('CapsLock', 48, 2, 76),
    ('A', 49, 2, 144),
    ('S', 50, 2, 194),
    ('D', 51, 2, 244),
    ('F', 52, 2, 294),
    ('G', 53, 2, 344),
    ('H', 54, 2, 394),
    ('J', 55, 2, 444),
    ('K', 56, 2, 494),
    ('L', 57, 2, 544),
    (':;', 58, 2, 593),
    ('"\'', 59, 2, 643),
    ('Enter', 76, 2, 723),
    ('page up', 105, 2, 805),
    ('Shift_L', 64, 3, 89),
    ('Z', 65, 3, 169),
    ('X', 66, 3, 219),
    ('C', 67, 3, 269),
    ('V', 68, 3, 319),
    ('B', 69, 3, 369),
    ('N', 70, 3, 419),
    ('M', 71, 3, 469),
    (',<', 72, 3, 519),
    ('.>', 73, 3, 569),
    ('/?', 74, 3, 618),
    ('Shift_R', 75, 3, 687),
    ('Up', 90, 3, 755),
    ('page down', 108, 3, 805),
    ('Ctrl_L', 80, 4, 63),
    ('Win_L', 81, 4, 127),
    ('Alt_L', 82, 4, 190),
    ('Space', 83, 4, 375),
    ('Fn', 85, 4, 563),
    ('Ctrl_R', 87, 4, 625),
    ('Left', 88, 4, 705),
    ('Down', 89, 4, 755),
    ('Right', 91, 4, 805),
]

BY_NAME = {name: idx for name, idx, _, _ in KEYS}


def encode(colours):
    """colours: {light index: (r, g, b)} -> 512-byte payload. Keys not listed are sent dark."""
    buf = bytearray(CHUNKS * CHUNK)
    for n, (_, idx, _, _) in enumerate(KEYS):
        r, g, b = colours.get(idx, (0, 0, 0))
        buf[4 * n:4 * n + 4] = bytes((idx, r & 255, g & 255, b & 255))
    return bytes(buf)


def send(cmd, colours):
    """Send one set of key colours: 04 18, 04 20 .. 08, 8 chunks, 04 02."""
    payload = encode(colours)
    cmd.flush()
    cmd.write(b'\x04\x18')
    if cmd.read(500) is None:
        raise HidError('no reply to op 0x18')
    cmd.write(b'\x04\x20\x00\x00\x00\x00\x00\x00\x08')
    if cmd.read(500) is None:
        raise HidError('no reply to op 0x20')
    for i in range(0, len(payload), CHUNK):
        cmd.write(payload[i:i + CHUNK])
        if cmd.read(500) is None:
            raise HidError('no reply to key chunk')
    cmd.write(b'\x04\x02')
    if cmd.read(500) is None:
        raise HidError('no reply to op 0x02')


def all_keys(rgb):
    return {idx: rgb for _, idx, _, _ in KEYS}
