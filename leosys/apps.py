"""What the keyboard shows when Claude is quiet: matrix apps (clock, PC monitor, message, face, rotation) and key-light
bases.

A matrix app is a function (now, cfg, stats) -> 63 x 5 frame, where stats is the PC monitor's snapshot (pcstats).
A key base is a function (now, cfg) -> {light index: rgb}, or None to leave the keys to the keyboard's own lighting.
Later phases add more of both (media, screen colours); each one is a single entry in MATRIX_APPS or KEY_BASES.
"""
import colorsys
import datetime
import math
import unicodedata

from . import albums, cityapps, faces, screen, typingmeter
from .keys import KEYS

NO_STATS = {"values": {}, "history": {}}


def _colour(c, default=faces.ORANGE):
    h = str(c or "").lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


# ---- matrix apps --------------------------------------------------------------------------------
def clock(now, cfg, stats=None):
    """HH:MM:SS on the left, the day and date on the right."""
    c = cfg["clock"]
    t = datetime.datetime.fromtimestamp(now)
    hour = t.hour if c["24h"] else (t.hour % 12 or 12)
    text = f"{hour:02d}:{t.minute:02d}" + (f":{t.second:02d}" if c["seconds"] else "")
    f = faces.blank()
    colour = _colour(c["colour"])
    end = faces.draw_text(f, text, 1, colour)
    if c["date"]:
        date = t.strftime("%a %d").upper()
        faces.draw_text(f, date, max(end + 2, faces.WIDTH - faces.text_width(date) - 1), faces.dim(colour, 0.45))
    return f


def message(now, cfg, stats=None):
    m = cfg["message"]
    text = (m["text"] or "").strip().upper()
    if not text:
        return None
    return faces.text_frame(text, now, _colour(m["colour"]))


def claude(now, cfg, stats=None):
    """Claude's live status: the busy faces while a session works, the calm critter when it is quiet, asleep when no
    session is open. Picking this app (or putting it in the rotation) gives Claude full priority (see claude_picked)."""
    c = (stats or {}).get("claude") or {}
    mood = c.get("mood") or "idle"
    t = c.get("t", 0.0) if mood not in ("idle", "sleep") else now % 60
    return faces.face(mood, t, c.get("label"), c.get("dots"))


# ---- PC monitor ---------------------------------------------------------------------------------
LABEL_GREY = (150, 150, 150)
EMPTY = (14, 14, 14)            # the unlit part of a bar or graph: just visible, so you can see its length
DOWN, UP = (0, 200, 255), faces.ORANGE


def _mix(a, b, f):
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def heat(v):
    """Green when low, yellow by half, orange at 80, red when full."""
    v = max(0.0, min(100.0, float(v or 0)))
    if v < 50:
        return _mix(faces.GREEN, faces.YELLOW, v / 50)
    if v < 80:
        return _mix(faces.YELLOW, faces.ORANGE, (v - 50) / 30)
    return _mix(faces.ORANGE, faces.RED, (v - 80) / 20)


def _graph(f, x0, x1, values, level, colour=None):
    """Columns x0..x1 (inclusive) over an unlit baseline, newest at the right. level(v) -> 0..5 dots or None."""
    for x in range(x0, x1 + 1):
        faces.put(f, x, 4, EMPTY)
    vals = list(values)[-(x1 - x0 + 1):]
    for i, v in enumerate(vals):
        x = x1 - (len(vals) - 1 - i)
        n = level(v)
        for k in range(n or 0):
            faces.put(f, x, 4 - k, colour(v, k) if colour else heat(k * 20 + 10))


def _pct_level(v):
    if v is None:
        return None
    return max(1 if v >= 4 else 0, min(5, int(v / 20 + 0.5)))


def _meter(label, metric):
    """A three-letter label, the last half minute as a graph, and the value in per cent."""
    def draw(now, cfg, stats=None):
        stats = stats or NO_STATS
        v = stats["values"].get(metric)
        f = faces.blank()
        faces.draw_text(f, label, 0, LABEL_GREY)
        _graph(f, 13, faces.WIDTH - 18, stats["history"].get(metric, []), _pct_level)
        text = "--" if v is None else f"{int(round(v))}%"
        faces.draw_text(f, text, faces.WIDTH - faces.text_width(text), LABEL_GREY if v is None else heat(v))
        return f
    return draw


def rate_text(bytes_per_s, bits=True):
    """At most four characters: 0K, 850K, 1.2M, 12M, 120M. Bits per second by default, like speed tests."""
    v = max(0.0, float(bytes_per_s or 0)) * (8 if bits else 1)
    for unit, div in (("G", 1e9), ("M", 1e6), ("K", 1e3)):
        n = v / div
        if n >= 0.995 or unit == "K":
            if n < 0.5:
                return "0K"
            return f"{n:.1f}{unit}" if n < 9.95 else f"{int(round(n))}{unit}"
    return "0K"


def net(now, cfg, stats=None):
    """Download and upload speed, then a short graph of the download."""
    stats = stats or NO_STATS
    v = stats["values"].get("net")
    bits = cfg.get("monitor", {}).get("net_bits", True)
    f = faces.blank()
    faces.draw_text(f, "↓", 0, DOWN)
    faces.draw_text(f, "--" if v is None else rate_text(v[0], bits), 4, DOWN)
    faces.draw_text(f, "↑", 21, UP)
    faces.draw_text(f, "--" if v is None else rate_text(v[1], bits), 25, UP)
    hist = [h[0] if h else None for h in stats["history"].get("net", [])]
    top = max([h for h in hist[-21:] if h] or [1.0])
    _graph(f, 42, 62, hist, lambda h: None if h is None else (0 if h <= 0 else max(1, min(5, round(h / top * 5)))),
           lambda h, k: faces.dim(DOWN, 0.45 + 0.11 * k))
    return f


def _ping_level(ms):
    if ms is None:
        return 5
    return 1 if ms <= 20 else 2 if ms <= 50 else 3 if ms <= 100 else 4 if ms <= 200 else 5


def _ping_colour(ms, k=0):
    return faces.RED if ms is None else heat(_ping_level(ms) * 20 - 10)


def ping(now, cfg, stats=None):
    """Ping time to the host in the settings, with a graph; a lost ping is a full red column."""
    stats = stats or NO_STATS
    hist = stats["history"].get("ping", [])
    v = stats["values"].get("ping")
    f = faces.blank()
    faces.draw_text(f, "PING", 0, LABEL_GREY)
    _graph(f, 17, 45, hist, _ping_level, _ping_colour)
    if v is not None:
        text, colour = f"{min(999, int(round(v)))}MS", _ping_colour(v)
    else:
        text, colour = ("LOST", faces.RED) if hist else ("--", LABEL_GREY)
    faces.draw_text(f, text, faces.WIDTH - faces.text_width(text), colour)
    return f


def pc(now, cfg, stats=None):
    """CPU, memory, GPU and disk side by side: a letter and a bar each."""
    stats = stats or NO_STATS
    f = faces.blank()
    for i, (letter, metric) in enumerate((("C", "cpu"), ("R", "ram"), ("G", "gpu"), ("D", "disk"))):
        x0 = 16 * i
        v = stats["values"].get(metric)
        faces.draw_text(f, letter, x0, LABEL_GREY if v is not None else faces.dim(LABEL_GREY, 0.3))
        lit = 0 if v is None else int(round(v / 100 * 11))
        for x in range(11):
            for y in range(5):
                faces.put(f, x0 + 4 + x, y, heat(v) if x < lit else EMPTY)
    return f


# ---- media ------------------------------------------------------------------------------------------
def font_text(s):
    """What of `s` the 3 x 5 font can draw: accents dropped (é -> E). Returns '' when too little survives (Arabic,
    Japanese... the matrix cannot show those)."""
    s = str(s or "").strip()
    if not s:
        return ""
    plain = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    kept = "".join(c if c in faces.FONT else " " for c in plain)
    kept = " ".join(kept.split())
    letters = sum(c.isalnum() for c in s)
    if sum(c.isalnum() for c in kept) < max(2, 0.6 * letters):
        return ""
    return kept


def media_text(n):
    """TITLE - ARTIST, using whichever parts the font can show, or the player's name."""
    parts = [p for p in (font_text(n.get("title")), font_text(n.get("artist"))) if p]
    return " - ".join(parts) or font_text(n.get("app")) or "PLAYING"


def _scroll(f, text, x0, x1, colour, now, speed=12.0, gap=12):
    """Draw text in columns x0..x1: still if it fits, else scrolling to the left in a loop."""
    width = x1 - x0 + 1
    tw = faces.text_width(text)
    if tw <= width:
        faces.draw_text(f, text, x0, colour)
        return
    tmp = faces.blank()
    period = tw + gap
    off = int(now * speed) % period
    faces.draw_text(tmp, text, x0 - off, colour)
    faces.draw_text(tmp, text, x0 - off + period, colour)
    for y in range(faces.HEIGHT):
        f[y][x0:x1 + 1] = tmp[y][x0:x1 + 1]


def now_playing(now, cfg, stats=None):
    """What Windows says is playing: a play or pause sign, then title and artist, scrolling when long."""
    n = ((stats or {}).get("media") or {}).get("now")
    f = faces.blank()
    if not n or (n.get("status") in ("closed", "stopped") and not n.get("title")):
        faces.draw_text(f, "♪", 0, faces.dim(LABEL_GREY, 0.5))
        faces.draw_text(f, "NOTHING ON", 5, faces.dim(LABEL_GREY, 0.5))
        return f
    colour = _colour(cfg.get("media", {}).get("colour"), DOWN)
    playing = n.get("status") == "playing"
    faces.draw_text(f, "▶" if playing else "‖", 0, faces.GREEN if playing else LABEL_GREY)
    _scroll(f, media_text(n), 5, faces.WIDTH - 1, colour if playing else faces.dim(colour, 0.45), now)
    return f


_hold = {"t": 0.0, "peak": [0.0, 0.0]}


def level(now, cfg, stats=None):
    """The sound level of what plays, left channel on top and right below, with a falling peak dot."""
    p = ((stats or {}).get("media") or {}).get("peaks") or (0.0, 0.0)
    f = faces.blank()
    dt = max(0.0, min(1.0, now - _hold["t"]))
    _hold["t"] = now
    for ch, rows in enumerate(((0, 1), (3, 4))):
        v = max(float(p[ch] or 0), 1e-5)
        n = int(max(0.0, min(1.0, (20 * math.log10(v) + 42) / 42)) * faces.WIDTH)   # -42 dB .. 0 dB across
        _hold["peak"][ch] = max(float(n), _hold["peak"][ch] - 40 * dt)
        peak = int(_hold["peak"][ch])
        for x in range(faces.WIDTH):
            c = heat(x / (faces.WIDTH - 1) * 100) if x < n else EMPTY
            if x == peak - 1 and peak > n:
                c = faces.WHITE
            for y in rows:
                faces.put(f, x, y, c)
    return f


def album(now, cfg, stats=None):
    """Pictures and GIFs from the folder chosen in the panel, scaled to 63 x 5 and played in a loop."""
    a = (stats or {}).get("album") or {"state": "loading"}
    if a["state"] == "ok":
        return albums.rows_of_at(a, now)
    f = faces.blank()
    text = {"loading": "LOADING", "empty": "NO PICS"}.get(a["state"], "NO FOLDER")
    faces.draw_text(f, text, (faces.WIDTH - faces.text_width(text)) // 2, faces.dim(LABEL_GREY, 0.5))
    return f


MATRIX_APPS = {
    "clock": {"label": "Clock", "desc": "The time with seconds, and the date", "frame": clock},
    "pc": {"label": "PC", "desc": "CPU, memory, GPU and disk as four bars", "frame": pc,
           "needs": ("cpu", "ram", "gpu", "disk")},
    "cpu": {"label": "CPU", "desc": "Processor use, with the last half minute", "frame": _meter("CPU", "cpu"),
            "needs": ("cpu",)},
    "ram": {"label": "Memory", "desc": "How much memory is in use", "frame": _meter("RAM", "ram"), "needs": ("ram",)},
    "gpu": {"label": "GPU", "desc": "Graphics card use, as Task Manager counts it", "frame": _meter("GPU", "gpu"),
            "needs": ("gpu",)},
    "disk": {"label": "Disk", "desc": "How busy the drives are", "frame": _meter("DSK", "disk"), "needs": ("disk",)},
    "net": {"label": "Network", "desc": "Download and upload speed", "frame": net, "needs": ("net",)},
    "ping": {"label": "Ping", "desc": "Ping time to the internet; lost pings in red", "frame": ping,
             "needs": ("ping",)},
    "now": {"label": "Now playing", "desc": "The song or video Windows says is playing, from any player",
            "frame": now_playing, "media": ("now",)},
    "level": {"label": "Sound level", "desc": "A live level meter of what your speakers play", "frame": level,
              "media": ("meter",)},
    "album": {"label": "Album", "desc": "Pictures and GIFs from a folder you choose", "frame": album,
              "album": ("matrix",)},
    "weather": {"label": "Weather", "desc": "The temperature, today's high and low and the coming hours (Open-Meteo)",
                "frame": cityapps.weather_frame, "city": ("weather",), "enabled": lambda cfg: cfg["weather"]["enabled"]},
    "prayer": {"label": "Prayer times", "desc": "The next prayer and the time left, calculated on your PC",
               "frame": cityapps.prayer_frame, "enabled": lambda cfg: cfg["prayer"]["enabled"]},
    "typing": {"label": "Typing speed", "desc": "Words per minute and the last six minutes, counts only (opt-in)",
               "frame": typingmeter.typing_frame, "typing": ("typing",), "enabled": lambda cfg: cfg["typing"]["enabled"]},
    "message": {"label": "Message", "desc": "Your own text, scrolling when it is long", "frame": message},
    "claude": {"label": "Claude", "desc": "Claude's live status. While picked or in the rotation, Claude comes first "
                                          "whenever a session is busy", "frame": claude},
}


ALIASES = {"face": "claude"}     # the Critter app became the Claude app


def _app(name):
    return ALIASES.get(name, name)


def claude_picked(cfg):
    """Is the Claude app picked, or in the rotation? Then Claude's status beats every other app."""
    if _app(cfg["matrix"]) == "claude":
        return True
    return cfg["matrix"] == "rotation" and any(_app(i.get("app")) == "claude" for i in cfg["rotation"])


def needs(cfg, kind="needs"):
    """Which PC readings ("needs") or media readings ("media") the chosen app needs. For a rotation, all of its apps,
    so graphs are full when they come up."""
    choice = cfg["matrix"]
    names = [i.get("app") for i in cfg["rotation"]] if choice == "rotation" else [choice]
    out = set()
    for n in names:
        app = MATRIX_APPS.get(_app(n), {})
        if "enabled" in app and not app["enabled"](cfg):
            continue                                    # a switched-off app asks for nothing
        out.update(app.get(kind, ()))
    return out


def rotation_item(now, items, cfg=None):
    """Which app of the rotation is up at `now`. Items: [{"app", "seconds"}]."""
    items = [dict(i, app=_app(i.get("app"))) for i in items]
    items = [i for i in items if i["app"] in MATRIX_APPS and float(i.get("seconds", 0)) > 0]
    if cfg is not None:                                 # apps that are switched off are skipped, not shown blank
        items = [i for i in items if "enabled" not in MATRIX_APPS[i["app"]] or MATRIX_APPS[i["app"]]["enabled"](cfg)]
    if not items:
        return None
    total = sum(float(i["seconds"]) for i in items)
    pos, acc = now % total, 0.0
    for i in items:
        acc += float(i["seconds"])
        if pos < acc:
            return i["app"]
    return items[-1]["app"]


def matrix_frame(now, cfg, stats=None):
    """(frame, label) for the chosen matrix app, or (None, None) to give the matrix back to the keyboard."""
    choice = cfg["matrix"]
    if choice == "rotation":
        choice = rotation_item(now, cfg["rotation"], cfg)
    app = MATRIX_APPS.get(_app(choice))
    if not app:
        return None, None
    frame = app["frame"](now, cfg, stats)
    return (frame, app["label"]) if frame is not None else (None, None)


# ---- key bases --------------------------------------------------------------------------------------
def _all(c):
    return {idx: c for _, idx, _, _ in KEYS}


def solid(now, cfg, stats=None):
    return _all(_colour(cfg["colour"]))


def breathe(now, cfg, stats=None):
    return _all(faces.dim(_colour(cfg["colour"]), 0.2 + 0.8 * (math.sin(now * 1.6) + 1) / 2))


def rainbow(now, cfg, stats=None):
    out = {}
    for _, idx, _, x in KEYS:
        r, g, b = colorsys.hsv_to_rgb((x / 1000 - now * 0.15) % 1.0, 1.0, 1.0)
        out[idx] = (int(r * 255), int(g * 255), int(b * 255))
    return out


def screen_colours(now, cfg, stats=None):
    """The keys follow the colours of the screen (screen.py). Dark until the first reading arrives."""
    grid = ((stats or {}).get("screen") or {}).get("grid")
    if not grid:
        return {}
    c = cfg.get("screen", {})
    bal = c.get("balance") or {}
    return screen.key_colours(grid, c.get("mode", "edges"), float(c.get("saturation", 1.5)), float(c.get("gain", 1.5)),
                              (bal.get("r", 100), bal.get("g", 100), bal.get("b", 100)))


KEY_BASES = {
    "own": {"label": "Keyboard's own", "desc": "leo-sys leaves the keys alone when Claude is quiet", "colours": None},
    "solid": {"label": "One colour", "desc": "Every key in your colour", "colours": solid},
    "breathe": {"label": "Breathing", "desc": "Your colour, slowly fading in and out", "colours": breathe},
    "rainbow": {"label": "Rainbow", "desc": "A rainbow drifting across the keyboard", "colours": rainbow},
    "heatmap": {"label": "Typing heatmap", "desc": "Each key coloured by how often you press it (counts only, opt-in, needs "
                                                  "Typing counting switched on)", "colours": typingmeter.heatmap},
    "screen": {"label": "Screen colours", "desc": "The keys follow the colours of your screen (a tiny 32 x 18 grid, kept only "
                                                 "in memory)", "colours": screen_colours},
}


def key_colours(now, cfg, stats=None):
    base = KEY_BASES.get(cfg["base"])
    return base["colours"](now, cfg, stats) if base and base["colours"] else None


def _sample_album():
    """A one-frame stand-in so the Album card has a picture: a rainbow strip."""
    rgb = bytearray()
    for y in range(faces.HEIGHT):
        for x in range(faces.WIDTH):
            r, g, b = colorsys.hsv_to_rgb(x / faces.WIDTH, 1.0, 0.35 + 0.65 * (y + 1) / faces.HEIGHT)
            rgb += bytes((int(r * 255), int(g * 255), int(b * 255)))
    return {"state": "ok", "frames": [(bytes(rgb), 1.0)], "starts": [0.0], "total": 1.0}


def samples():
    """A still frame of each matrix app for the panel's cards (fixed time, sample text, made-up PC readings)."""
    from .media import FakeMediaWatch
    from .pcstats import FakeMonitor
    t = datetime.datetime(2026, 11, 19, 21, 50, 42).timestamp()
    demo = {"clock": {"24h": True, "seconds": True, "date": True, "colour": "#ff6018"},
            "message": {"text": "HELLO", "colour": "#ff6018"}, "monitor": {"net_bits": True},
            "media": {"colour": "#00c8ff"}, "city": {"lat": 33.32, "lon": 44.37},
            "weather": {"enabled": True, "fahrenheit": False}, "typing": {"enabled": True},
            "prayer": {"enabled": True, "method": "mwl", "asr": "standard", "show": {}, "offsets": {}}}
    from .weather import FakeWeatherWatch
    stats = dict(FakeMonitor(clock=lambda: t).snapshot(), media=FakeMediaWatch(clock=lambda: t).snapshot(),
                 album=_sample_album(), weather=FakeWeatherWatch(clock=lambda: t).snapshot(),
                 typing={"wpm": 62.0, "buckets": [0, 20, 35, 40, 28, 45, 52, 30, 10, 48, 55, 41] * 3})
    return {k: faces.to_hex(v["frame"](t, demo, stats)) for k, v in MATRIX_APPS.items()}
