"""PC readings for the monitor apps, straight from Windows with ctypes (no extra packages, no admin).

- CPU, GPU, disk and network come from Windows' performance counters (PDH, the same numbers Task Manager uses).
  English counter names are used, so this works on an Arabic or any other Windows.
- Memory comes from GlobalMemoryStatusEx.
- Ping uses Windows' ICMP helper (IcmpSendEcho), which needs no admin rights.

Nothing is read until an app asks for it: `Monitor.want()` names what the matrix needs, a background thread reads
only those once a second, and stops reading a few seconds after nothing asks any more. Ping only runs while the
ping app is shown or is in the rotation.
CPU temperature is not offered: Windows does not give it out without admin rights or a kernel driver.
"""
import collections
import ctypes
import os
import socket
import threading
import time
from ctypes import wintypes

METRICS = ("cpu", "ram", "gpu", "disk", "net", "ping")
HISTORY = 64            # one sample a second, a little more than the matrix is wide
IDLE_AFTER = 5.0        # stop reading a metric this long after the last app asked for it
PING_EVERY = 2.0


# ---- PDH (performance counters) ------------------------------------------------------------------
PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


_pdh = None


def _pdh_dll():
    global _pdh
    if _pdh is None:
        d = ctypes.WinDLL("pdh")
        H, P = ctypes.c_void_p, ctypes.POINTER
        for name, args in [("PdhOpenQueryW", [ctypes.c_wchar_p, ctypes.c_void_p, P(H)]),
                           ("PdhAddEnglishCounterW", [H, ctypes.c_wchar_p, ctypes.c_void_p, P(H)]),
                           ("PdhCollectQueryData", [H]),
                           ("PdhGetFormattedCounterValue", [H, wintypes.DWORD, P(wintypes.DWORD), P(_FmtValue)]),
                           ("PdhGetFormattedCounterArrayW", [H, wintypes.DWORD, P(wintypes.DWORD), P(wintypes.DWORD),
                                                             ctypes.c_void_p]),
                           ("PdhCloseQuery", [H])]:
            f = getattr(d, name)
            f.argtypes, f.restype = args, wintypes.DWORD
        _pdh = d
    return _pdh


class Counters:
    """One PDH query holding a few counters, collected together."""

    def __init__(self, paths, fmt=PDH_FMT_DOUBLE):
        self.pdh, self.fmt = _pdh_dll(), fmt
        self.query = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)):
            raise OSError("PdhOpenQuery failed")
        self.handles = {}
        for key, path in paths.items():
            h = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(h)) == 0:
                self.handles[key] = h
        if not self.handles:
            self.close()
            raise OSError("none of the counters exist: " + ", ".join(paths.values()))
        self.pdh.PdhCollectQueryData(self.query)    # rates need two collections; this is the first

    def collect(self):
        return self.pdh.PdhCollectQueryData(self.query) == 0

    def value(self, key):
        h = self.handles.get(key)
        if h is None:
            return None
        v = _FmtValue()
        if self.pdh.PdhGetFormattedCounterValue(h, self.fmt, None, ctypes.byref(v)) or v.CStatus > 1:
            return None
        return v.doubleValue

    def array(self, key):
        """{instance name: value} for a counter with a (*) wildcard."""
        h = self.handles.get(key)
        if h is None:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        if self.pdh.PdhGetFormattedCounterArrayW(h, self.fmt, ctypes.byref(size), ctypes.byref(count), None) \
                != PDH_MORE_DATA or not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(h, self.fmt, ctypes.byref(size), ctypes.byref(count), buf):
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        return {items[i].szName or "": items[i].FmtValue.doubleValue
                for i in range(count.value) if items[i].FmtValue.CStatus <= 1}

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


def _clamp(v):
    return None if v is None else max(0.0, min(100.0, v))


class _Cpu:
    def __init__(self):
        # Processor Utility matches Task Manager (it counts turbo, so it can pass 100). Older Windows: Processor Time.
        self.c = Counters({"util": r"\Processor Information(_Total)\% Processor Utility",
                           "time": r"\Processor(_Total)\% Processor Time"}, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100)

    def read(self):
        if not self.c.collect():
            return None
        v = self.c.value("util")
        return _clamp(v if v is not None else self.c.value("time"))


class _Gpu:
    """Like Task Manager: per graphics card, add up each engine type across processes, and show the busiest."""

    def __init__(self):
        self.c = Counters({"eng": r"\GPU Engine(*)\Utilization Percentage"})

    def read(self):
        if not self.c.collect():
            return None
        items = self.c.array("eng")
        if not items:
            return None if items is None else 0.0
        per = collections.defaultdict(float)
        for name, v in items.items():
            luid = name.split("_luid_")[-1].split("_phys_")[0] if "_luid_" in name else ""
            per[(luid, name.rsplit("engtype_", 1)[-1])] += v
        return _clamp(max(per.values()))


class _Disk:
    def __init__(self):
        self.c = Counters({"idle": r"\PhysicalDisk(_Total)\% Idle Time"}, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100)

    def read(self):
        if not self.c.collect():
            return None
        idle = self.c.value("idle")
        return None if idle is None else _clamp(100.0 - idle)


class _Net:
    SKIP = ("isatap", "teredo", "pseudo")

    def __init__(self):
        self.c = Counters({"down": r"\Network Interface(*)\Bytes Received/sec",
                           "up": r"\Network Interface(*)\Bytes Sent/sec"})

    def read(self):
        if not self.c.collect():
            return None
        out = []
        for key in ("down", "up"):
            items = self.c.array(key)
            if items is None:
                return None
            out.append(sum(v for n, v in items.items() if not any(s in n.lower() for s in self.SKIP)))
        return tuple(out)       # bytes per second


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _Ram:
    def read(self):
        m = _MemStatus()
        m.dwLength = ctypes.sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) or not m.ullTotalPhys:
            return None
        return _clamp((m.ullTotalPhys - m.ullAvailPhys) * 100.0 / m.ullTotalPhys)


READERS = {"cpu": _Cpu, "ram": _Ram, "gpu": _Gpu, "disk": _Disk, "net": _Net}


# ---- ping --------------------------------------------------------------------------------------------
class Pinger:
    """One ICMP echo at a time through iphlpapi (no admin, no ping.exe). Returns milliseconds, or None if lost."""

    RESOLVE_EVERY = 300

    def __init__(self):
        d = ctypes.WinDLL("iphlpapi")
        d.IcmpCreateFile.restype = ctypes.c_void_p
        d.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ushort, ctypes.c_void_p,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        d.IcmpSendEcho.restype = wintypes.DWORD
        d.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
        self.d = d
        self.handle = d.IcmpCreateFile()
        if not self.handle or self.handle == ctypes.c_void_p(-1).value:
            raise OSError("IcmpCreateFile failed")
        self.host, self.addr, self.resolved = None, None, 0.0

    def _resolve(self, host):
        if host != self.host or time.time() - self.resolved > self.RESOLVE_EVERY:
            self.host, self.resolved = host, time.time()
            try:
                ip = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
                self.addr = int.from_bytes(socket.inet_aton(ip), "little")   # IPAddr: network-order bytes in memory
            except (OSError, IndexError):
                self.addr = None
        return self.addr

    def ping(self, host, timeout_ms=1000):
        addr = self._resolve(host)
        if addr is None:
            return None
        data = b"leo-sys!"
        reply = ctypes.create_string_buffer(256)
        if not self.d.IcmpSendEcho(self.handle, addr, data, len(data), None, reply, len(reply), timeout_ms):
            return None
        status = int.from_bytes(reply.raw[4:8], "little")
        rtt = int.from_bytes(reply.raw[8:12], "little")
        return float(rtt) if status == 0 else None

    def close(self):
        if self.handle:
            self.d.IcmpCloseHandle(self.handle)
            self.handle = None


def clean_host(host, default="1.1.1.1"):
    """A host name or IPv4 address, or the default if it holds anything else."""
    h = str(host or "").strip()
    return h if 0 < len(h) <= 253 and all(c.isalnum() or c in ".-" for c in h) and h.isascii() else default


# ---- the monitor --------------------------------------------------------------------------------------
class Monitor:
    """Reads what the apps want, keeps the last minute of each, and hands out a snapshot."""

    def __init__(self, readers=None, pinger=Pinger, clock=time.time, threads=True):
        self.reader_types = readers or READERS
        self.pinger_type = pinger
        self.clock = clock
        self.threads = threads
        self.lock = threading.Lock()
        self.wanted = {}                                   # metric -> last time an app asked for it
        self.ping_host = "1.1.1.1"
        self.values = {m: None for m in METRICS}
        self.history = {m: collections.deque(maxlen=HISTORY) for m in METRICS}
        self.last_sample = {}
        self.readers, self.broken = {}, {}
        self.pinger = None
        self.started = False

    def want(self, metrics, ping_host=None):
        now = self.clock()
        with self.lock:
            for m in metrics:
                if m in METRICS:
                    self.wanted[m] = now
            if ping_host:
                self.ping_host = ping_host
            start = bool(metrics) and not self.started and self.threads
            self.started = self.started or start
        if start:
            threading.Thread(target=self._loop, args=(self._step, 1.0), daemon=True).start()
            threading.Thread(target=self._loop, args=(self._ping_step, PING_EVERY), daemon=True).start()

    def active(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            return {m for m, t in self.wanted.items() if now - t <= IDLE_AFTER}

    def snapshot(self):
        with self.lock:
            return {"values": dict(self.values), "history": {m: list(h) for m, h in self.history.items()}}

    def _record(self, metric, value, now):
        with self.lock:
            if now - self.last_sample.get(metric, 0) > IDLE_AFTER + 2:
                self.history[metric].clear()                # a gap: old samples would draw a false graph
            self.values[metric] = value
            self.history[metric].append(value)
            self.last_sample[metric] = now

    def _reader(self, metric):
        if metric in self.readers:
            return self.readers[metric]
        if self.clock() < self.broken.get(metric, 0):
            return None
        try:
            r = self.reader_types[metric]()
        except (OSError, AttributeError, KeyError) as e:      # counter missing on this PC, or not Windows
            self.broken[metric] = self.clock() + 60
            self.last_error = f"{metric}: {e}"
            return None
        self.readers[metric] = r
        return r

    def _step(self):
        now = self.clock()
        for m in self.active(now) - {"ping"}:
            r = self._reader(m)
            try:
                v = r.read() if r else None
            except OSError:
                v = None
            self._record(m, v, now)

    def _ping_step(self):
        now = self.clock()
        if "ping" not in self.active(now):
            return
        if self.pinger is None:
            try:
                self.pinger = self.pinger_type()
            except (OSError, AttributeError):
                self._record("ping", None, now)
                return
        with self.lock:
            host = self.ping_host
        self._record("ping", self.pinger.ping(host), now)

    def _loop(self, step, every):
        while True:
            t0 = self.clock()
            try:
                step()
            except Exception:       # a reading must never take the service down
                pass
            time.sleep(max(0.2, every - (self.clock() - t0)))


class FakeMonitor(Monitor):
    """Made-up readings that move, for the demo panel and the tests. Never touches Windows."""

    def __init__(self, clock=time.time):
        super().__init__(clock=clock, threads=False)
        import math
        self._math = math

    def fill(self, now=None):
        now = self.clock() if now is None else now
        sin = self._math.sin
        for i in range(HISTORY):
            t = now - (HISTORY - 1 - i)
            vals = {"cpu": 35 + 30 * sin(t / 7), "ram": 58 + 3 * sin(t / 40), "gpu": 20 + 18 * sin(t / 5 + 1),
                    "disk": max(0.0, 12 * sin(t / 3)), "net": (3e6 * (1.1 + sin(t / 6)), 2.5e5 * (1.1 + sin(t / 4))),
                    "ping": None if int(t) % 29 == 0 else 24 + 10 * (1 + sin(t / 2))}
            for m, v in vals.items():
                self.values[m] = v
                self.history[m].append(v)

    def snapshot(self):
        self.fill()
        return super().snapshot()


def default_monitor():
    return Monitor() if os.name == "nt" else FakeMonitor()
