"""One-off test: an orange bar, then a 3-second sweep, then stop. Sends only op 0x35 live frames."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

import sys, time
from leosys.hid import open_channels
from leosys import matrix as M

ORANGE = (255, 90, 0)          # vivid, fully saturated
mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
cmd, bulk = open_channels()
print('opened', cmd.path.split('#')[1], 'out', cmd.out_len, 'in', cmd.in_len)
if mode == 'probe':
    sys.exit(0)
try:
    f = M.blank()
    for y in range(M.HEIGHT):
        for x in range(20):
            f[y][x] = ORANGE
    t = time.perf_counter(); M.send_frame(cmd, f); print('bar sent in %.0f ms' % ((time.perf_counter() - t) * 1000))
    time.sleep(5)
    t0 = time.perf_counter(); n = 0
    while time.perf_counter() - t0 < 5:
        x0 = n % M.WIDTH; f = M.blank()
        for y in range(M.HEIGHT):
            for dx in range(6):
                f[y][(x0 + dx) % M.WIDTH] = ORANGE
        M.send_frame(cmd, f); n += 1
    print('sweep: %d frames in 5 s (%.1f fps)' % (n, n / 5))
finally:
    cmd.close()
    if bulk: bulk.close()
