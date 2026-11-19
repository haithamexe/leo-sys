import zlib, struct, sys, os
out = sys.argv[1]
def png(path, w, h, rgb):
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))
    def ch(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b''))
def lzw(idx, mcs=2):
    clear, eoi = 1 << mcs, (1 << mcs) + 1
    bits, acc, nb, size, out = mcs + 1, 0, 0, eoi + 1, bytearray()
    def emit(c):
        nonlocal acc, nb
        acc |= c << nb; nb += bits
        while nb >= 8: out.append(acc & 255); acc >>= 8; nb -= 8
    emit(clear)
    for i, p in enumerate(idx):          # no dictionary growth: clear every few codes
        emit(p); size += 1
        if size >= (1 << bits) - 1: emit(clear); size = eoi + 1
    emit(eoi)
    if nb: out.append(acc & 255)
    return bytes(out)
def gif(path, w, h, colours, delay_cs=50):
    pal = b''.join(bytes(c) for c in colours) + b'\x00' * 3 * (4 - len(colours))
    b = b'GIF89a' + struct.pack('<HHBBB', w, h, 0xF1, 0, 0) + pal
    b += b'!\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00'
    for i in range(len(colours)):
        data = lzw([i] * (w * h))
        b += b'!\xf9\x04\x04' + struct.pack('<H', delay_cs) + b'\x00\x00'
        b += b',' + struct.pack('<HHHHB', 0, 0, w, h, 0) + b'\x02'
        for j in range(0, len(data), 255): b += bytes([len(data[j:j+255])]) + data[j:j+255]
        b += b'\x00'
    open(path, 'wb').write(b + b';')
png(os.path.join(out, '1-red.png'), 240, 240, (255, 0, 0))
png(os.path.join(out, '2-blue.png'), 240, 240, (0, 0, 255))
gif(os.path.join(out, '3-red-green-blue.gif'), 128, 128, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
print(sorted(os.listdir(out)))
