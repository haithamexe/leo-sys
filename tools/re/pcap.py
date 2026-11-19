import struct, sys
def packets(path):
    b = open(path, 'rb').read()
    magic, = struct.unpack('<I', b[:4]); assert magic == 0xa1b2c3d4, hex(magic)
    lt, = struct.unpack('<I', b[20:24]); assert lt == 249, lt
    o = 24
    while o + 16 <= len(b):
        ts, tu, incl, orig = struct.unpack('<IIII', b[o:o+16]); o += 16
        p = b[o:o+incl]; o += incl
        hl, irp, st, fn, info, bus, dev, ep, xfer, dl = struct.unpack('<HQIHBHHBBI', p[:27])
        stage = p[27] if xfer == 2 and hl > 27 else None
        yield dict(t=ts + tu / 1e6, irp=irp, status=st, fn=fn, out=not (info & 1), dev=dev,
                   ep=ep, xfer=xfer, stage=stage, data=p[hl:hl+dl], dl=dl)
