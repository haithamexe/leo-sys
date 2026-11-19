import sys, collections; sys.path.insert(0, sys.argv[2]); from pcap import packets
P = [p for p in packets(sys.argv[1]) if p['dl']]; t0 = P[0]['t']
tx = None; out = []
def close(t):
    global tx
    if tx: out.append(tx)
    tx = None
for p in P:
    tt = p['t'] - t0; d = p['data']
    if p['xfer'] == 2 and p['out'] and len(d) == 9: close(tt); out.append(dict(t=tt, marker=d[8])); continue
    if p['ep'] == 0x03:
        if d[:2] == b'\x04\x18': close(tt); tx = dict(t=tt, cmd=None, data=[], bulk=[], tail=[]); continue
        if tx is None: tx = dict(t=tt, cmd=None, data=[], bulk=[], tail=[], orphan=True)
        if d[0] == 0x04 and tx['cmd'] is None: tx['cmd'] = d.rstrip(b'\0').hex(); continue
        if d[0] == 0x04 and len(d.rstrip(b'\0')) <= 3: tx['tail'].append(d.rstrip(b'\0').hex()); continue
        tx['data'].append(d)
    elif p['ep'] == 0x06 and p['out']:
        if tx is None: tx = dict(t=tt, cmd='(bulk only)', data=[], bulk=[], tail=[])
        tx['bulk'].append(d)
close(0)
for x in out:
    if 'marker' in x: print(f"{x['t']:7.2f} ---- MARKER {x['marker']:02x}"); continue
    nz = [i for i, d in enumerate(x['data']) if any(d)]
    first = x['data'][0].rstrip(b'\0').hex()[:70] if x['data'] else ''
    print(f"{x['t']:7.2f} cmd={x['cmd']} data={len(x['data'])} nonzero={len(nz)} bulk={len(x['bulk'])} tail={','.join(x['tail'])} first={first}")
