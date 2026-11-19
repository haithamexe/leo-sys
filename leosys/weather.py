"""Weather from Open-Meteo (https://open-meteo.com): free, no account, no key.

This is the one place leo-sys talks to the internet besides the ping app, and it is opt-in. What is sent: your city's
coordinates (rounded to about a kilometre) in the address of one request. Like any web request, it also lets Open-Meteo see
your internet address. It asks at most every 30 minutes, and only while a weather app is on the matrix or in the rotation
(it stops about a minute after); a failed request is retried after 5 minutes.
"""
import json
import math
import threading
import time
import urllib.parse
import urllib.request

URL = "https://api.open-meteo.com/v1/forecast"
REFRESH_S = 1800
RETRY_S = 300
TIMEOUT_S = 15
IDLE_AFTER = 60.0           # stop asking this long after nothing used the weather (the engine asks every tick while in use)
STALE_S = 3 * 3600          # older than this, the matrix shows it dimmed
HOURS = 21                  # how many coming hours the graph shows


def build_url(lat, lon):
    lat, lon = max(-90.0, min(90.0, float(lat))), max(-180.0, min(180.0, float(lon)))
    query = urllib.parse.urlencode({
        "latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}",
        "current": "temperature_2m,weather_code,is_day", "hourly": "temperature_2m",
        "daily": "temperature_2m_max,temperature_2m_min", "forecast_days": 2, "timezone": "auto"})
    return f"{URL}?{query}"


def parse(data):
    """The forecast reduced to what the matrix shows: now, today's high and low, the next hours. Raises ValueError."""
    try:
        cur = data["current"]
        hours, temps = data["hourly"]["time"], data["hourly"]["temperature_2m"]
        now_hour = str(cur["time"])[:13]
        start = next((i for i, t in enumerate(hours) if str(t)[:13] >= now_hour), 0)
        coming = [float(t) for t in temps[start:start + HOURS] if t is not None]
        return {"temp": float(cur["temperature_2m"]), "code": int(cur["weather_code"]), "day": bool(cur.get("is_day", 1)),
                "high": float(data["daily"]["temperature_2m_max"][0]), "low": float(data["daily"]["temperature_2m_min"][0]),
                "hours": coming}
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"unexpected weather answer ({e})")


def fetch(lat, lon, opener=urllib.request.urlopen):
    req = urllib.request.Request(build_url(lat, lon), headers={"User-Agent": "leo-sys (keyboard weather)"})
    with opener(req, timeout=TIMEOUT_S) as r:
        return parse(json.loads(r.read().decode("utf-8")))


class WeatherWatch:
    """Fetches the forecast in the background while something wants it, keeps the last good one."""

    def __init__(self, fetcher=fetch, clock=time.time, threads=True):
        self.fetcher, self.clock, self.threads = fetcher, clock, threads
        self.lock = threading.Lock()
        self.where = None
        self.wanted_at = -1e9
        self.data, self.fetched_at, self.error, self.next_try = None, 0.0, "", 0.0
        self.started = False

    def want(self, lat, lon):
        key = (round(float(lat), 2), round(float(lon), 2))
        with self.lock:
            self.wanted_at = self.clock()
            if key != self.where:                       # a different city: the old forecast is not this one's
                self.where, self.data, self.fetched_at, self.error, self.next_try = key, None, 0.0, "", 0.0
            start = self.threads and not self.started
            self.started = self.started or start
        if start:
            threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            age = self.clock() - self.fetched_at if self.data else None
            return {"data": self.data, "age": age, "stale": age is not None and age > STALE_S, "error": self.error}

    def due(self):
        with self.lock:
            now = self.clock()
            return (self.where is not None and now - self.wanted_at <= IDLE_AFTER and now >= self.next_try
                    and (self.data is None or now - self.fetched_at >= REFRESH_S))

    def _step(self):
        with self.lock:
            where = self.where
        try:
            data, err = self.fetcher(*where), ""
        except Exception as e:                      # no internet, a bad answer: keep the old forecast, try again later
            data, err = None, (str(e) or type(e).__name__)[:120]
        with self.lock:
            if where != self.where:
                return                              # the city changed while asking: drop this answer
            if data is not None:
                self.data, self.fetched_at, self.error, self.next_try = data, self.clock(), "", 0.0
            else:
                self.error, self.next_try = err, self.clock() + RETRY_S

    def _run(self):
        while True:
            if self.due():
                self._step()
            time.sleep(1.0)


class FakeWeatherWatch(WeatherWatch):
    """A made-up forecast for the demo and the tests. Never touches the network."""

    def __init__(self, clock=time.time):
        super().__init__(clock=clock, threads=False)
        self.wanted = 0

    def want(self, lat, lon):
        self.wanted += 1

    def snapshot(self):
        hours = [24 + 6 * math.sin((i - 6) / 24 * 6.283) for i in range(HOURS)]
        return {"data": {"temp": 27.0, "code": 2, "day": True, "high": 30.0, "low": 18.0, "hours": hours},
                "age": 10.0, "stale": False, "error": ""}
