"""The weather and prayer-time screens for the matrix (63 x 5)."""
import datetime

from . import faces, prayer

LABEL_GREY = (150, 150, 150)
ICON_COLOURS = {"y": (255, 200, 0), "Y": (255, 230, 0), "w": (170, 175, 185), "W": (255, 255, 255), "b": (0, 140, 255)}
ICONS = {
    "sun": [".yyy.", "yyyyy", "yyyyy", "yyyyy", ".yyy."],
    "moon": [".WW..", "WW...", "WW...", "WW...", ".WW.."],
    "partly": ["yy.w.", "yywww", ".wwww", ".wwww", "....."],
    "cloud": [".....", ".ww..", "wwwww", "wwwww", "....."],
    "fog": ["wwwww", ".....", "wwwww", ".....", "wwwww"],
    "rain": [".ww..", "wwwww", "wwwww", ".b.b.", "b.b.."],
    "snow": [".ww..", "wwwww", "wwwww", ".W.W.", "W.W.."],
    "storm": [".ww..", "wwwww", "wwwww", "..Y..", ".Y..."],
}
SHORT = {"fajr": "FAJR", "dhuhr": "DHUHR", "asr": "ASR", "maghrib": "MGHRB", "isha": "ISHA"}
# temperature in Celsius -> colour: cold blue, cyan, green, yellow, orange, red
RAMP = [(0, (0, 120, 255)), (10, (0, 200, 255)), (20, (0, 255, 60)), (28, (255, 200, 0)), (36, (255, 96, 24)), (44, (255, 0, 0))]


def icon_name(code, day=True):
    """Windows-free mapping of a WMO weather code (what Open-Meteo reports) to one of the icons."""
    if code == 0:
        return "sun" if day else "moon"
    if code in (1, 2):
        return "partly" if day else "cloud"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloud"


def temp_colour(c):
    if c <= RAMP[0][0]:
        return RAMP[0][1]
    for (t0, c0), (t1, c1) in zip(RAMP, RAMP[1:]):
        if c <= t1:
            k = (c - t0) / (t1 - t0)
            return tuple(round(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
    return RAMP[-1][1]


def draw_icon(f, name, x0, dim=1.0):
    for y, row in enumerate(ICONS[name]):
        for x, ch in enumerate(row):
            if ch != ".":
                faces.put(f, x0 + x, y, faces.dim(ICON_COLOURS[ch], dim))


def note(text):
    """A short grey message in the middle: for a switched-off app or a missing reading."""
    f = faces.blank()
    faces.draw_text(f, text, max(0, (faces.WIDTH - faces.text_width(text)) // 2), faces.dim(LABEL_GREY, 0.5))
    return f


def weather_frame(now, cfg, stats=None):
    """Icon, temperature now, today's high and low, and a bar for each of the coming hours."""
    w = cfg["weather"]
    if not w["enabled"]:
        return note("WEATHER OFF")
    snap = (stats or {}).get("weather") or {}
    d = snap.get("data")
    f = faces.blank()
    if d is None:
        draw_icon(f, "cloud", 0, 0.35)
        faces.draw_text(f, "NO NET" if snap.get("error") else "--°", 7, faces.dim(LABEL_GREY, 0.6))
        return f
    dim = 0.5 if snap.get("stale") else 1.0
    show = (lambda c: round(c * 9 / 5 + 32)) if w["fahrenheit"] else round
    draw_icon(f, icon_name(d["code"], d["day"]), 0, dim)
    end = faces.draw_text(f, f"{show(d['temp'])}°", 7, faces.dim(temp_colour(d["temp"]), dim))
    end = faces.draw_text(f, f"{show(d['high'])}/{show(d['low'])}", end + 3, faces.dim(LABEL_GREY, dim))
    start = end + 3
    hours = d["hours"][:faces.WIDTH - start]
    if len(hours) >= 4:
        lo, hi = min(hours), max(hours)
        for i, t in enumerate(hours):
            height = 3 if hi - lo < 1 else 1 + round((t - lo) / (hi - lo) * 4)
            for k in range(height):
                faces.put(f, start + i, faces.HEIGHT - 1 - k, faces.dim(temp_colour(t), dim * 0.8))
    return f


def countdown_text(remaining):
    minutes = int(remaining.total_seconds() // 60)
    if minutes < 1:
        return "NOW"
    return f"{minutes // 60}H{minutes % 60:02d}" if minutes >= 60 else f"{minutes}M"


def prayer_frame(now, cfg, stats=None):
    """The next prayer, its time, and the time left; the time left turns amber, then red, as it nears."""
    p = cfg["prayer"]
    if not p["enabled"]:
        return note("PRAYER OFF")
    city = cfg["city"]
    shown = tuple(n for n in prayer.PRAYERS if p["show"].get(n, True)) or prayer.PRAYERS
    moment = datetime.datetime.fromtimestamp(now)
    try:
        name, when, _ = prayer.next_prayer(moment, city["lat"], city["lon"], p["method"], p["asr"], p["offsets"], enabled=shown)
    except (ValueError, ArithmeticError, TypeError, OSError):      # a nonsense setting: say so instead of failing
        return note("NO TIMES")
    if when is None:
        return note("NO TIMES")
    left = when - moment
    minutes = left.total_seconds() / 60
    colour = faces.RED if minutes <= 10 else (255, 200, 0) if minutes <= 30 else (0, 255, 60)
    f = faces.blank()
    x = faces.draw_text(f, SHORT[name], 1, faces.ORANGE)
    faces.draw_text(f, when.strftime("%H:%M"), x + 3, faces.dim(LABEL_GREY, 1.0))
    text = countdown_text(left)
    faces.draw_text(f, text, faces.WIDTH - faces.text_width(text) - 1, colour)
    return f
