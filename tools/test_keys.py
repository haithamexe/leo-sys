"""One-off test: all keys orange for 5 s, then a left-to-right wave for 5 s. Sends only op 0x20."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

import time
from leosys.hid import open_channels
from leosys import keys as K

ORANGE = (255, 90, 0)
cmd, bulk = open_channels()
try:
    t = time.perf_counter(); K.send(cmd, K.all_keys(ORANGE)); print('all orange sent in %.0f ms' % ((time.perf_counter() - t) * 1000))
    end = time.time() + 5
    while time.time() < end:                    # keep it up, like the matrix needs
        time.sleep(0.5); K.send(cmd, K.all_keys(ORANGE))
    t0 = time.perf_counter(); n = 0
    while time.perf_counter() - t0 < 5:
        front = ((time.perf_counter() - t0) / 2.5 % 1) * 1100 - 50   # sweeps x 0..1000 every 2.5 s
        K.send(cmd, {idx: ORANGE if abs(x - front) < 90 else (0, 0, 0) for _, idx, _, x in K.KEYS}); n += 1
    print('wave: %d updates in 5 s (%.1f per s)' % (n, n / 5))
finally:
    cmd.close()
    if bulk: bulk.close()
