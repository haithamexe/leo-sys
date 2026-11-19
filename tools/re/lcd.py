import sys, collections; sys.path.insert(0, sys.argv[2]); from pcap import packets
P = [p for p in packets(sys.argv[1]) if p['dl'] and p['ep'] in (3, 6, 0x84)]; t0 = P[0]['t']
ups = []; cur = None
for p in P:
    d = p['data']
    if p['ep'] == 3 and d[:2] in (b'\x04\x72', b'\x04\x33'): cur = dict(t=p['t']-t0, cmd=d[:10].hex(), blk=[]); ups.append(cur); continue
    if p['ep'] == 3 and d[:2] == b'\x04\x02': cur = None; continue
    if p['ep'] == 6 and cur is not None: cur['blk'].append(d)
for u in ups:
    data = b''.join(u['blk'])
    words = collections.Counter(data[i:i+2] for i in range(0, len(data), 2))
    print(f"{u['t']:7.2f} cmd={u['cmd']} blocks={len(u['blk'])} bytes={len(data)}")
    print('   head', data[:48].hex())
    print('   top 2-byte words', [(w.hex(), n) for w, n in words.most_common(5)])
    # find where first long run of the top word begins
    top = words.most_common(1)[0][0]; run = top * 16
    print('   first run of top word at byte', data.find(run))
