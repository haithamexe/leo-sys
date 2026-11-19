"""Is a program using the microphone or the camera right now? Read from Windows' own record, the one behind Settings,
Privacy, Microphone/Camera ("recently used"): HKCU ...\\CapabilityAccessManager\\ConsentStore\\<microphone|webcam>.

Each program there has LastUsedTimeStart and LastUsedTimeStop; Stop is 0 while it is using the device. Nothing is hooked,
nothing is recorded by leo-sys, and only program names are read.

That record has stale entries: a program that was killed or updated mid-call keeps Stop = 0 forever (this PC has two old
Discord versions like that). So "in use" also needs the entry to have started after this boot and, for programs that
are not store apps, a process running from exactly that file.
"""
import threading
import time

BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
KINDS = {"mic": "microphone", "cam": "webcam"}
FILETIME_UNIX = 116444736000000000        # 100 ns ticks from 1601 to 1970
IDLE_AFTER = 5.0
POLL_S = 1.5


def entries(kind, winreg=None):
    """[{name, start, stop, path}] for every program in the record of this device (kind: 'mic' or 'cam')."""
    if winreg is None:
        import winreg
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, BASE + "\\" + KINDS[kind])
    except OSError:
        return out

    def times(key):
        try:
            return int(winreg.QueryValueEx(key, "LastUsedTimeStart")[0]), int(winreg.QueryValueEx(key, "LastUsedTimeStop")[0])
        except (OSError, TypeError, ValueError):
            return None

    def names(key):
        i = 0
        while True:
            try:
                yield winreg.EnumKey(key, i)
            except OSError:
                return
            i += 1

    with root:
        for name in names(root):
            try:
                with winreg.OpenKey(root, name) as k:
                    if name == "NonPackaged":
                        for sub in names(k):
                            with winreg.OpenKey(k, sub) as kk:
                                t = times(kk)
                                if t:
                                    out.append({"name": sub, "start": t[0], "stop": t[1], "path": sub.replace("#", "\\")})
                    else:
                        t = times(k)
                        if t:
                            out.append({"name": name, "start": t[0], "stop": t[1], "path": None})
            except OSError:
                continue
    return out


def friendly(entry):
    """'C:#Program Files#Zoom#bin#Zoom.exe' -> 'Zoom.exe'; 'MSTeams_8wekyb3d8bbwe' -> 'MSTeams'."""
    if entry["path"]:
        return entry["path"].rsplit("\\", 1)[-1]
    return entry["name"].split("_")[0]


def in_use(items, boot_filetime, running_paths):
    """Names of the programs using the device now. `running_paths` is a callable returning lower-case exe paths
    (only asked for when there is a candidate)."""
    candidates = [e for e in items if e["start"] > 0 and e["stop"] == 0 and e["start"] >= boot_filetime]
    if not candidates:
        return []
    running = None
    names = []
    for e in candidates:
        if e["path"]:                                           # not a store app: a process must run from that very file
            if running is None:
                running = running_paths()
            if not any(p.endswith(e["path"].lower()) for p in running):
                continue
        names.append(friendly(e))
    return sorted(set(names))


def boot_filetime(now=None):
    """When this PC started, as a Windows FILETIME."""
    import ctypes
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    ms = ctypes.windll.kernel32.GetTickCount64()
    return int((now if now is not None else time.time()) * 1e7) + FILETIME_UNIX - ms * 10000


def running_exe_paths():
    """Lower-case full paths of the programs running now (psapi + query, no admin)."""
    import ctypes
    from ctypes import wintypes
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.OpenProcess.restype = wintypes.HANDLE
    pids = (wintypes.DWORD * 4096)()
    size = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(size)):
        return []
    paths = []
    buf = ctypes.create_unicode_buffer(1024)
    for pid in pids[:size.value // ctypes.sizeof(wintypes.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            n = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                paths.append(buf.value.lower())
        finally:
            k32.CloseHandle(h)
    return paths


def read_in_use():
    """{'mic': [names], 'cam': [names]} from the real registry and process list."""
    boot = boot_filetime()
    return {k: in_use(entries(k), boot, running_exe_paths) for k in KINDS}


class UseWatch:
    """Reads the record every 1.5 seconds while something wants it (the key indicators), keeps the latest answer."""

    def __init__(self, reader=read_in_use, clock=time.time, threads=True):
        self.reader, self.clock, self.threads = reader, clock, threads
        self.lock = threading.Lock()
        self.wanted_at = -1e9
        self.value, self.error = {"mic": [], "cam": []}, ""
        self.started = False

    def want(self):
        with self.lock:
            self.wanted_at = self.clock()
            start = self.threads and not self.started
            self.started = self.started or start
        if start:
            threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return {"mic": list(self.value["mic"]), "cam": list(self.value["cam"]), "error": self.error}

    def _step(self):
        try:
            value, err = self.reader(), ""
        except Exception as e:                      # never take the service down for a registry hiccup
            value, err = {"mic": [], "cam": []}, (str(e) or type(e).__name__)[:120]
        with self.lock:
            self.value, self.error = value, err

    def _run(self):
        while True:
            with self.lock:
                active = self.clock() - self.wanted_at <= IDLE_AFTER
            if active:
                self._step()
            else:
                with self.lock:
                    self.value = {"mic": [], "cam": []}
            time.sleep(POLL_S)


class FakeUseWatch(UseWatch):
    """Settable answers for the demo and the tests. Never touches the registry."""

    def __init__(self, clock=time.time):
        super().__init__(clock=clock, threads=False)
        self.wanted = 0

    def want(self):
        self.wanted += 1
