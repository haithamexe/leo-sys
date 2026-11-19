import sys; sys.path.insert(0, sys.argv[2]); from pcap import packets
P = [p for p in packets(sys.argv[1]) if p['dl'] and p['ep'] == 3]; t0 = P[0]['t']
frames = []; cur = None
for p in P:
    d = p['data']
    if d[:2] == b'\x04\x35': cur = bytearray(); start = p['t']; continue
    if cur is not None:
        if d[:2] == b'\x04\x02': frames.append((start - t0, bytes(cur))); cur = None; continue
        cur += d
idx = [int(x) for x in sys.argv[3].split(',')]
H, W = int(sys.argv[4]), int(sys.argv[5])
def at(f, i): return any(f[3*i:3*i+3])
L = {'col-major': lambda r, c: c*H + r, 'col-serp': lambda r, c: c*H + (r if c % 2 == 0 else H-1-r),
     'row-major': lambda r, c: r*W + c, 'row-serp': lambda r, c: r*W + (c if r % 2 == 0 else W-1-c)}
for name, fn in L.items():
    print('===', name)
    for k in idx:
        t, f = frames[k]; print(f't={t:.2f}')
        for r in range(H): print(''.join('#' if at(f, fn(r, c)) else '.' for c in range(W)))
