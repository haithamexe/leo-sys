"""Prayer times, calculated on this PC from the date and your city's coordinates. No internet, nothing sent anywhere.

It is the widely used PrayTimes.org method (sun position from the date, twilight angles for Fajr and Isha), written out
here in the standard library. Pick the calculation method your community follows: they differ in the sun angles for Fajr
and Isha, and some set Maghrib by an angle below the horizon instead of at sunset.

Times are for the PC's own time zone, so they are right while the PC is in the city you chose. Offsets in minutes let
you match a local timetable exactly.
"""
import datetime
import math
import time

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
NAMES = {"fajr": "FAJR", "sunrise": "SUNRISE", "dhuhr": "DHUHR", "asr": "ASR", "maghrib": "MAGHRIB", "isha": "ISHA"}

# fajr and isha: sun angle below the horizon in degrees. maghrib: an angle below the horizon, if not at sunset.
# isha_min: Isha this many minutes after Maghrib instead of by an angle.
METHODS = {
    "mwl": {"name": "Muslim World League", "fajr": 18, "isha": 17},
    "jafari": {"name": "Jafari (Leva Research Institute, Qom)", "fajr": 16, "isha": 14, "maghrib": 4},
    "ummalqura": {"name": "Umm al-Qura, Makkah", "fajr": 18.5, "isha_min": 90},
    "egypt": {"name": "Egyptian General Authority of Survey", "fajr": 19.5, "isha": 17.5},
    "karachi": {"name": "University of Islamic Sciences, Karachi", "fajr": 18, "isha": 18},
    "isna": {"name": "Islamic Society of North America", "fajr": 15, "isha": 15},
    "tehran": {"name": "Institute of Geophysics, Tehran", "fajr": 17.7, "isha": 14, "maghrib": 4.5},
}
ASR = {"standard": 1, "hanafi": 2}      # the shadow length (as a multiple of the object) that starts Asr


def _sin(d): return math.sin(math.radians(d))
def _cos(d): return math.cos(math.radians(d))
def _tan(d): return math.tan(math.radians(d))
def _arcsin(x): return math.degrees(math.asin(x))
def _arccos(x): return math.degrees(math.acos(x))
def _arctan2(y, x): return math.degrees(math.atan2(y, x))
def _arccot(x): return math.degrees(math.atan(1.0 / x))
def _fix_angle(a): return a - 360.0 * math.floor(a / 360.0)
def _fix_hour(a): return a - 24.0 * math.floor(a / 24.0)


def julian(year, month, day):
    if month <= 2:
        year, month = year - 1, month + 12
    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5


def sun_position(jd):
    """(declination in degrees, equation of time in hours) at a Julian date."""
    d = jd - 2451545.0
    g = _fix_angle(357.529 + 0.98560028 * d)
    q = _fix_angle(280.459 + 0.98564736 * d)
    lam = _fix_angle(q + 1.915 * _sin(g) + 0.020 * _sin(2 * g))
    e = 23.439 - 0.00000036 * d
    ra = _arctan2(_cos(e) * _sin(lam), _cos(lam)) / 15.0
    return _arcsin(_sin(e) * _sin(lam)), q / 15.0 - _fix_hour(ra)


def times(day, lat, lon, tz_hours, method="mwl", asr="standard", offsets=None, elevation=0.0):
    """{'fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha': datetime on `day`, or None if the sun never gets that far}.

    `tz_hours` is the UTC offset of the PC's time zone on that day (3 for Baghdad)."""
    m = METHODS.get(method, METHODS["mwl"])
    lat, lon = max(-89.9, min(89.9, float(lat))), max(-180.0, min(180.0, float(lon)))
    jd = julian(day.year, day.month, day.day) - lon / (15.0 * 24.0)

    def mid_day(t):
        return _fix_hour(12 - sun_position(jd + t)[1])

    def angle_time(angle, t, ccw=False):
        decl = sun_position(jd + t)[0]
        x = (-_sin(angle) - _sin(decl) * _sin(lat)) / (_cos(decl) * _cos(lat))
        if abs(x) > 1:
            return None
        part = _arccos(x) / 15.0
        return mid_day(t) + (-part if ccw else part)

    def asr_time(factor, t):
        decl = sun_position(jd + t)[0]
        return angle_time(-_arccot(factor + _tan(abs(lat - decl))), t)

    rise_set = 0.833 + 0.0347 * math.sqrt(max(0.0, elevation))
    raw = {"fajr": angle_time(m["fajr"], 5 / 24, ccw=True), "sunrise": angle_time(rise_set, 6 / 24, ccw=True),
           "dhuhr": mid_day(12 / 24), "asr": asr_time(ASR.get(asr, 1), 13 / 24)}
    sunset = angle_time(rise_set, 18 / 24)
    raw["maghrib"] = angle_time(m["maghrib"], 18 / 24) if "maghrib" in m else sunset
    if "isha_min" in m:
        raw["isha"] = None if raw["maghrib"] is None else raw["maghrib"] + m["isha_min"] / 60.0
    else:
        raw["isha"] = angle_time(m["isha"], 18 / 24)
    shift = tz_hours - lon / 15.0
    offsets = offsets or {}
    midnight = datetime.datetime(day.year, day.month, day.day)
    out = {}
    for name, hour in raw.items():
        if hour is None:
            out[name] = None
            continue
        hour = _fix_hour(hour + shift) + max(-120.0, min(120.0, float(offsets.get(name, 0)))) / 60.0
        out[name] = midnight + datetime.timedelta(minutes=round(hour * 60))     # to the nearest minute
    return out


def local_offset_hours(moment=None):
    """The PC's UTC offset in hours at `moment` (now by default)."""
    try:
        moment = moment or datetime.datetime.now().astimezone()
        if moment.tzinfo is None:
            moment = moment.astimezone()
        return moment.utcoffset().total_seconds() / 3600.0
    except (OSError, OverflowError, ValueError):       # a date Windows cannot place in the local zone: use today's offset
        dst = time.daylight and time.localtime().tm_isdst > 0
        return -(time.altzone if dst else time.timezone) / 3600.0


def next_prayer(now, lat, lon, method="mwl", asr="standard", offsets=None, tz_hours=None, enabled=PRAYERS):
    """(name, time, today's times) of the next prayer after `now` (a naive local datetime); tomorrow's Fajr after Isha."""
    tz = local_offset_hours(now) if tz_hours is None else tz_hours
    today = times(now.date(), lat, lon, tz, method, asr, offsets)
    for name in PRAYERS:
        if name in enabled and today.get(name) and today[name] > now:
            return name, today[name], today
    tomorrow = times(now.date() + datetime.timedelta(days=1), lat, lon, tz, method, asr, offsets)
    first = next((n for n in PRAYERS if n in enabled and tomorrow.get(n)), "fajr")
    return first, tomorrow.get(first), today
