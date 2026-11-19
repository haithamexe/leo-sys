"""Typing speed and a key heatmap: COUNTS ONLY.

What this keeps, in memory and nowhere else: how many times each key has been pressed, how many typing keys were pressed in
each of the last 36 ten-second stretches, and the best speed of the session. It never keeps which key followed which, the
time of any single key press (only the tally for each ten-second stretch), or any text; nothing is written to disk or sent
anywhere. Switching it off, pressing Reset or restarting leo-sys wipes it all. The heatmap key light shows each key's relative
use as a colour, so that picture (also drawn in the panel) is the one place the counts show, and only as colours.

How it counts: while switched on, about 100 times a second it asks Windows whether each of the keyboard's keys is down right now
(the same call games use) and counts a key each time it goes from up to down; holding a key counts once. It is not a keyboard
hook, but security software may still notice a program asking about letter keys, which is why this is off by default.
"""
import collections
import ctypes
import threading
import time

from . import cityapps, faces
from .keys import KEYS

# the keyboard's own key names -> Windows virtual-key codes
VK = {"Esc": 0x1B, "Tab": 0x09, "CapsLock": 0x14, "Enter": 0x0D, "Backspace": 0x08, "Space": 0x20, "Left": 0x25, "Up": 0x26,
      "Right": 0x27, "Down": 0x28, "Home": 0x24, "del": 0x2E, "page up": 0x21, "page down": 0x22, "Shift_L": 0xA0,
      "Shift_R": 0xA1, "Ctrl_L": 0xA2, "Ctrl_R": 0xA3, "Alt_L": 0xA4, "Win_L": 0x5B, "VOLDEC": 0xAE, "VOLINC": 0xAF,
      "!1": 0x31, "@2": 0x32, "#3": 0x33, "$4": 0x34, "%5": 0x35, "^6": 0x36, "&7": 0x37, "*8": 0x38, "(9": 0x39, ")0": 0x30,
      "_-": 0xBD, "+=": 0xBB, "{[": 0xDB, "}]": 0xDD, "|\\": 0xDC, ":;": 0xBA, "\"'": 0xDE, ",<": 0xBC, ".>": 0xBE, "/?": 0xBF}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK[_c] = ord(_c)
KEY_VK = {idx: VK[name] for name, idx, _, _ in KEYS if name in VK}           # light index -> virtual key
NOT_TEXT = {"Esc", "Tab", "CapsLock", "Enter", "Backspace", "Left", "Up", "Right", "Down", "Home", "del", "page up", "page down",
            "Shift_L", "Shift_R", "Ctrl_L", "Ctrl_R", "Alt_L", "Win_L", "VOLDEC", "VOLINC"}
TEXT_IDX = {idx for name, idx, _, _ in KEYS if name in VK and name not in NOT_TEXT}   # keys that make a character: letters, digits, punctuation, space
BUCKET_S, BUCKETS = 10, 36
WINDOW_S = 3 * BUCKET_S             # the speed is the characters typed over the last three ten-second stretches (20 to 30 s)
POLL_S = 0.01


def _async_down(vks):
    """The set of these virtual keys that are down right now."""
    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.restype, user32.GetAsyncKeyState.argtypes = ctypes.c_short, [ctypes.c_int]
    return {vk for vk in vks if user32.GetAsyncKeyState(vk) & 0x8000}


class TypingMeter:
    """Counts key presses while switched on. All of its state is counts."""

    def __init__(self, down=_async_down, clock=time.time, threads=True):
        self.down, self.clock, self.threads = down, clock, threads
        self.lock = threading.Lock()
        self.enabled = False
        self.thread = None
        self.stop_flag = threading.Event()
        self._clear()

    def _clear(self):
        self.counts = collections.Counter()
        self.prev = set()
        self.buckets = collections.deque([0] * BUCKETS, maxlen=BUCKETS)
        self.bucket_at = None
        self.total = 0
        self.best = 0.0
        self.started_at = self.clock()

    # ---- switching on and off ---------------------------------------------------------------------------------------------
    def set_enabled(self, on):
        on = bool(on)
        with self.lock:
            if on == self.enabled:
                return
            self.enabled = on
            if not on:
                self._clear()                        # switching off throws everything away
        if on and self.threads:
            self.stop_flag.clear()
            self.thread = threading.Thread(target=self._run, daemon=True, name="typing-meter")
            self.thread.start()
        elif not on:
            self.stop_flag.set()

    def reset(self):
        with self.lock:
            self._clear()

    # ---- counting ----------------------------------------------------------------------------------------------------------
    def poll(self, now=None):
        """One look at the keys: count those that just went down."""
        now = self.clock() if now is None else now
        down_vks = self.down(KEY_VK.values())
        with self.lock:
            if not self.enabled:
                return
            down_idx = {idx for idx, vk in KEY_VK.items() if vk in down_vks}
            pressed = down_idx - self.prev
            self.prev = down_idx
            if not pressed:
                self._roll(now, 0)
                return
            chars = 0
            for idx in pressed:
                self.counts[idx] += 1
                self.total += 1
                chars += idx in TEXT_IDX
            self._roll(now, chars)
            if chars:
                wpm = self._wpm(now)
                if sum(list(self.buckets)[-3:]) >= 15 and wpm > self.best:       # a fair speed needs a few words behind it
                    self.best = wpm

    def _roll(self, now, chars):
        index = int(now // BUCKET_S)
        if self.bucket_at is None:
            self.bucket_at = index
        for _ in range(min(BUCKETS, max(0, index - self.bucket_at))):
            self.buckets.append(0)
        self.bucket_at = index
        self.buckets[-1] += chars

    def _wpm(self, now):
        """Words per minute (five characters a word) over the last two whole ten-second stretches and this one so far."""
        chars = sum(list(self.buckets)[-3:])
        if not chars:
            return 0.0
        elapsed = 2 * BUCKET_S + (now % BUCKET_S)
        elapsed = max(5.0, min(elapsed, now - self.started_at))     # not counting time before the meter was switched on
        return chars / 5.0 * (60.0 / elapsed)

    def snapshot(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            if self.enabled:
                self._roll(now, 0)                    # time passing with no typing still moves the graph on
            return {"enabled": self.enabled, "wpm": round(self._wpm(now), 1) if self.enabled else 0.0,
                    "best": round(self.best, 1), "total": self.total, "counts": dict(self.counts),
                    "buckets": list(self.buckets)}

    def _run(self):
        while not self.stop_flag.is_set():
            try:
                self.poll()
            except Exception:                         # never take the service down over a key-state call
                time.sleep(1.0)
            time.sleep(POLL_S)


class FakeTypingMeter(TypingMeter):
    """Made-up typing for the demo and the tests. Never asks Windows about keys."""

    def __init__(self, clock=time.time):
        super().__init__(down=lambda vks: set(), clock=clock, threads=False)


# ---- what is drawn ------------------------------------------------------------------------------------------------------------
SPEED_RAMP = [(0, (0, 120, 255)), (40, (0, 255, 60)), (80, (255, 200, 0)), (120, (255, 0, 0))]
HEAT_RAMP = [(0.0, (0, 70, 200)), (0.25, (0, 200, 255)), (0.5, (0, 255, 60)), (0.75, (255, 200, 0)), (1.0, (255, 0, 0))]


def _ramp(stops, x):
    if x <= stops[0][0]:
        return stops[0][1]
    for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
        if x <= x1:
            k = (x - x0) / (x1 - x0)
            return tuple(round(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
    return stops[-1][1]


def speed_colour(wpm):
    return _ramp(SPEED_RAMP, wpm)


def typing_frame(now, cfg, stats=None):
    """'62 WPM' and a bar for each of the last ten-second stretches (newest at the right)."""
    if not cfg["typing"]["enabled"]:
        return cityapps.note("TYPING OFF")
    t = (stats or {}).get("typing") or {}
    wpm = int(round(t.get("wpm", 0)))
    f = faces.blank()
    end = faces.draw_text(f, f"{wpm} WPM", 1, speed_colour(wpm) if wpm else faces.dim(cityapps.LABEL_GREY, 0.7))
    buckets = list(t.get("buckets") or [])
    start = end + 3
    width = faces.WIDTH - start
    if width >= 8 and buckets:
        shown = buckets[-width:]
        shown = [0] * (width - len(shown)) + shown
        speeds = [n / 5.0 * (60.0 / BUCKET_S) for n in shown]
        top = max(60.0, max(speeds))
        for i, v in enumerate(speeds):
            height = 0 if v <= 0 else 1 + round(min(1.0, v / top) * 4)
            if height == 0:
                faces.put(f, start + i, faces.HEIGHT - 1, (14, 14, 14))
            for k in range(height):
                faces.put(f, start + i, faces.HEIGHT - 1 - k, faces.dim(speed_colour(v), 0.8))
    return f


def heatmap(now, cfg, stats=None):
    """Every key coloured by how often it has been pressed: blue (rarely) through green and yellow to red (the most)."""
    t = (stats or {}).get("typing") or {}
    counts = t.get("counts") or {}
    if not t.get("enabled"):                        # counting is off: a faint blue, so it is clear nothing is being shown
        return {idx: faces.dim((0, 70, 200), 0.12) for _, idx, _, _ in KEYS}
    top = max(counts.values(), default=0)
    out = {}
    for _, idx, _, _ in KEYS:
        n = counts.get(idx, 0)
        out[idx] = faces.dim((0, 70, 200), 0.12) if n == 0 or top == 0 else _ramp(HEAT_RAMP, (n / top) ** 0.5)
    return out
