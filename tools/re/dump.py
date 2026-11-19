import sys; sys.path.insert(0, sys.argv[2]); from pcap import packets
a, b = float(sys.argv[3]), float(sys.argv[4]); full = len(sys.argv) > 5
P = [p for p in packets(sys.argv[1]) if p['dl']]; t0 = P[0]['t']
for p in P:
    tt = p['t'] - t0
    if not (a <= tt <= b) or p['ep'] in (0x81,) or p['xfer'] == 2: continue
    d = p['data']; name = {3: 'CMD', 0x84: 'RPL', 6: 'BLK'}.get(p['ep'], hex(p['ep']))
    if p['ep'] == 6 and not full: print(f"{tt:8.3f} BLK len={len(d)} head={d[:32].hex()}"); continue
    print(f"{tt:8.3f} {name} {d.hex()}")
