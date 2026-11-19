"""What is playing, the volume, and the sound level, straight from Windows with ctypes (no extra packages, no admin).

- Now playing comes from Windows' media sessions (Windows.Media.Control, the source of the volume pop-up's media
  card), so Spotify, YouTube and other sites in any browser, VLC and most players show up. Interface IDs and method
  order were read from Windows' own metadata (C:\\Windows\\System32\\WinMetadata).
- Volume, mute, the microphone's mute and the sound level come from Core Audio (IAudioEndpointVolume and
  IAudioMeterInformation on the default devices).

COM objects belong to the thread that made them, so everything here runs on MediaWatch's one thread; other threads
(the panel, Claude's tools) hand it requests and wait for the answer.
"""
import ctypes
import queue
import threading
import time
import uuid
from ctypes import POINTER, byref, c_float, c_int, c_int64, c_uint, c_void_p, wintypes

HRESULT = ctypes.HRESULT


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


def _guid(s):
    g = GUID()
    ctypes.memmove(byref(g), uuid.UUID(s).bytes_le, 16)
    return g


def _call(obj, slot, argtypes, *args, restype=HRESULT):
    """Call method number `slot` of a COM/WinRT object's vtable."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[slot])(obj, *args)


def _release(obj):
    if obj:
        _call(obj, 2, [], restype=wintypes.ULONG)


def _out(obj, slot):
    """A method whose only argument is an out pointer to an object."""
    p = c_void_p()
    _call(obj, slot, [POINTER(c_void_p)], byref(p))
    return p


# ---- WinRT: media sessions ------------------------------------------------------------------------
IID_MANAGER_STATICS = "2050c4ee-11a0-57de-aed7-c97c70338245"
IID_ASYNC_INFO = "00000036-0000-0000-c000-000000000046"
MANAGER_CLASS = "Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager"
STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
FILETIME_UNIX = 116444736000000000
APP_NAMES = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "308046b0af4a39cb": "Firefox",
             "vlc": "VLC", "brave": "Brave", "opera": "Opera", "zunemusic": "Media Player", "microsoft.zunemusic": "Media Player",
             "microsoft.media.player": "Media Player", "applemusic": "Apple Music", "itunes": "iTunes", "deezer": "Deezer",
             "discord": "Discord", "foobar2000": "foobar2000", "musicbee": "MusicBee", "aimp": "AIMP"}

_combase = None


def _rt():
    global _combase
    if _combase is None:
        d = ctypes.WinDLL("combase")
        d.WindowsCreateString.argtypes = [ctypes.c_wchar_p, c_uint, POINTER(c_void_p)]
        d.WindowsDeleteString.argtypes = [c_void_p]
        d.WindowsGetStringRawBuffer.argtypes = [c_void_p, POINTER(c_uint)]
        d.WindowsGetStringRawBuffer.restype = ctypes.c_void_p
        d.RoGetActivationFactory.argtypes = [c_void_p, POINTER(GUID), POINTER(c_void_p)]
        d.RoInitialize.argtypes = [c_int]
        _combase = d
    return _combase


def _hstring_text(h):
    if not h:
        return ""
    n = c_uint()
    p = _rt().WindowsGetStringRawBuffer(h, byref(n))
    text = ctypes.wstring_at(p, n.value) if p else ""
    _rt().WindowsDeleteString(h)
    return text


def _get_string(obj, slot):
    h = c_void_p()
    _call(obj, slot, [POINTER(c_void_p)], byref(h))
    return _hstring_text(h)


def _await(op, timeout=2.0):
    """Wait for a WinRT IAsyncOperation and return it (GetResults is slot 8 on the operation itself)."""
    info = c_void_p()
    _call(op, 0, [POINTER(GUID), POINTER(c_void_p)], byref(_guid(IID_ASYNC_INFO)), byref(info))
    try:
        end = time.monotonic() + timeout
        while True:
            st = c_int()
            _call(info, 7, [POINTER(c_int)], byref(st))
            if st.value == 1:
                return True
            if st.value > 1 or time.monotonic() > end:
                return False
            time.sleep(0.01)
    finally:
        _release(info)


def app_name(aumid):
    """'Spotify.exe' -> 'Spotify', 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> 'Media Player'."""
    key = (aumid or "").split("!")[0].split("_")[0].lower()
    key = key[:-4] if key.endswith(".exe") else key
    key = key.rsplit("\\", 1)[-1]
    return APP_NAMES.get(key, key.split(".")[-1].capitalize() if key else "")


class Sessions:
    """Windows' current media session: what it plays, how far in, and play/pause/skip."""

    def __init__(self):
        rt = _rt()
        name = c_void_p()
        rt.WindowsCreateString(MANAGER_CLASS, len(MANAGER_CLASS), byref(name))
        statics = c_void_p()
        try:
            hr = rt.RoGetActivationFactory(name, byref(_guid(IID_MANAGER_STATICS)), byref(statics))
        finally:
            rt.WindowsDeleteString(name)
        if hr:
            raise OSError(f"media sessions unavailable (0x{hr & 0xffffffff:08x})")
        try:
            op = _out(statics, 6)                       # RequestAsync
            try:
                if not _await(op, 5.0):
                    raise OSError("media session manager did not answer")
                self.manager = _out(op, 8)
            finally:
                _release(op)
        finally:
            _release(statics)

    def _session(self):
        s = c_void_p()
        _call(self.manager, 6, [POINTER(c_void_p)], byref(s))   # GetCurrentSession (null when nothing plays)
        return s if s.value else None

    def now_playing(self):
        s = self._session()
        if not s:
            return None
        try:
            app = _get_string(s, 6)
            title = artist = album = ""
            op = _out(s, 7)                                     # TryGetMediaPropertiesAsync
            try:
                if _await(op):
                    props = _out(op, 8)
                    try:
                        title, artist, album = _get_string(props, 6), _get_string(props, 9), _get_string(props, 10)
                    finally:
                        _release(props)
            finally:
                _release(op)
            pb = _out(s, 9)                                     # GetPlaybackInfo
            try:
                st = c_int()
                _call(pb, 7, [POINTER(c_int)], byref(st))
            finally:
                _release(pb)
            tl = _out(s, 8)                                     # GetTimelineProperties
            try:
                vals = []
                for slot in (6, 7, 10, 11):                     # start, end, position, last updated
                    v = c_int64()
                    _call(tl, slot, [POINTER(c_int64)], byref(v))
                    vals.append(v.value)
            finally:
                _release(tl)
            start, end, pos, updated = vals
            status = STATUS.get(st.value, "unknown")
            position = (pos - start) / 1e7
            if status == "playing" and updated > 0:             # extrapolate from the last time the player reported
                position += max(0.0, time.time() - (updated - FILETIME_UNIX) / 1e7)
            duration = (end - start) / 1e7
            if duration > 0:
                position = min(position, duration)
            return {"title": title, "artist": artist, "album": album, "app": app_name(app), "app_id": app,
                    "status": status, "position": round(max(0.0, position), 1),
                    "duration": round(duration, 1) if duration > 0 else None}
        finally:
            _release(s)

    def control(self, action):
        """play | pause | toggle | next | previous | stop. True if the player accepted it."""
        slot = {"play": 10, "pause": 11, "stop": 12, "next": 16, "previous": 17, "toggle": 20}[action]
        s = self._session()
        if not s:
            return False
        try:
            op = _out(s, slot)
            try:
                if not _await(op):
                    return False
                ok = ctypes.c_ubyte()
                _call(op, 8, [POINTER(ctypes.c_ubyte)], byref(ok))
                return bool(ok.value)
            finally:
                _release(op)
        finally:
            _release(s)

    def close(self):
        _release(self.manager)
        self.manager = None


# ---- Core Audio: volume, mute, level ------------------------------------------------------------------
CLSID_ENUMERATOR = "bcde0395-e52f-467c-8e3d-c4579291692e"
IID_ENUMERATOR = "a95664d2-9614-4f35-a746-de8db63617e6"
IID_ENDPOINT_VOLUME = "5cdf2c82-841e-4546-9722-0cf74078229a"
IID_METER = "c02216f6-8c67-4b5b-9d00-d008e73e0064"
E_RENDER, E_CAPTURE, E_CONSOLE = 0, 1, 0
CLSCTX_ALL = 23


class Audio:
    """The default speakers' volume, mute and level, and the default microphone's mute."""

    def __init__(self):
        ole = ctypes.OleDLL("ole32")
        self.enum = c_void_p()
        ole.CoCreateInstance(byref(_guid(CLSID_ENUMERATOR)), None, CLSCTX_ALL, byref(_guid(IID_ENUMERATOR)),
                             byref(self.enum))
        self.vol = self.meter = self.mic = None
        self.devices_at = 0.0

    def _activate(self, flow, iid):
        dev = c_void_p()
        _call(self.enum, 4, [c_int, c_int, POINTER(c_void_p)], flow, E_CONSOLE, byref(dev))  # GetDefaultAudioEndpoint
        try:
            out = c_void_p()
            _call(dev, 3, [POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p)],
                  byref(_guid(iid)), CLSCTX_ALL, None, byref(out))                              # Activate
            return out
        finally:
            _release(dev)

    def _devices(self):
        """Look the default devices up again every few seconds, so switching to headphones is followed."""
        if time.monotonic() - self.devices_at < 3 and self.vol:
            return
        for o in (self.vol, self.meter, self.mic):
            _release(o)
        self.vol = self.meter = self.mic = None
        self.devices_at = time.monotonic()
        try:
            self.vol = self._activate(E_RENDER, IID_ENDPOINT_VOLUME)
            self.meter = self._activate(E_RENDER, IID_METER)
        except OSError:
            self.vol = self.meter = None
        try:
            self.mic = self._activate(E_CAPTURE, IID_ENDPOINT_VOLUME)
        except OSError:
            self.mic = None                     # no microphone

    def volume(self):
        """(level 0..100, muted) of the default speakers, or (None, None)."""
        self._devices()
        if not self.vol:
            return None, None
        level, muted = c_float(), c_int()
        _call(self.vol, 9, [POINTER(c_float)], byref(level))   # GetMasterVolumeLevelScalar
        _call(self.vol, 15, [POINTER(c_int)], byref(muted))   # GetMute
        return round(level.value * 100), bool(muted.value)

    def set_volume(self, percent):
        self._devices()
        if self.vol:
            _call(self.vol, 7, [c_float, c_void_p], max(0.0, min(1.0, percent / 100)), None)

    def set_mute(self, on):
        self._devices()
        if self.vol:
            _call(self.vol, 14, [c_int, c_void_p], int(bool(on)), None)

    def mic_muted(self):
        self._devices()
        if not self.mic:
            return None
        muted = c_int()
        _call(self.mic, 15, [POINTER(c_int)], byref(muted))
        return bool(muted.value)

    def peaks(self):
        """(left, right) sound level 0..1 of what the speakers play right now."""
        self._devices()
        if not self.meter:
            return None
        n = c_uint()
        _call(self.meter, 4, [POINTER(c_uint)], byref(n))     # GetMeteringChannelCount
        if n.value >= 2:
            arr = (c_float * n.value)()
            _call(self.meter, 5, [c_uint, POINTER(c_float)], n.value, arr)   # GetChannelsPeakValues
            return arr[0], arr[1]
        p = c_float()
        _call(self.meter, 3, [POINTER(c_float)], byref(p))    # GetPeakValue
        return p.value, p.value

    def close(self):
        for o in (self.vol, self.meter, self.mic, self.enum):
            _release(o)
        self.vol = self.meter = self.mic = self.enum = None


# ---- the watcher ---------------------------------------------------------------------------------------
IDLE_AFTER = 5.0
CONTROL_ACTIONS = ("play", "pause", "toggle", "next", "previous", "stop", "volume_up", "volume_down", "mute",
                   "unmute", "set_volume")


class MediaWatch:
    """One thread that owns the COM objects. Reads only what is wanted; runs requests from other threads."""

    def __init__(self, sessions=Sessions, audio=Audio, clock=time.time, threads=True):
        self.sessions_type, self.audio_type, self.clock, self.threads = sessions, audio, clock, threads
        self.lock = threading.Lock()
        self.wanted = {}            # "now" | "meter" | "volume" -> last time asked
        self.state = {"now": None, "volume": None, "muted": None, "mic_muted": None, "peaks": None,
                      "volume_changed": 0.0, "error": ""}
        self.requests = queue.Queue()
        self.started = False
        self.sessions = self.audio = None
        self.next_now = self.next_volume = 0.0

    # -- asked from any thread
    def want(self, *what):
        now = self.clock()
        with self.lock:
            for w in what:
                self.wanted[w] = now
            start = not self.started and self.threads
            self.started = self.started or start
        if start:
            threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def request(self, fn, timeout=4.0):
        """Run fn(self) on the media thread and return its result (raises on failure or timeout)."""
        self.want()
        if not self.threads:
            return fn(self)
        box, done = {}, threading.Event()
        self.requests.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("Windows media did not answer in time")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def now_playing(self):
        """{"now": what plays or None, "volume", "muted"}, read fresh."""
        def read(w):
            info = w._read_now(force=True)
            try:
                w._read_volume()
            except OSError:
                pass
            s = w.snapshot()
            return {"now": info, "volume": s["volume"], "muted": s["muted"]}
        return self.request(read)

    def control(self, action, value=None):
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"unknown action {action!r}; use one of {', '.join(CONTROL_ACTIONS)}")
        return self.request(lambda w: w._control(action, value))

    # -- on the media thread
    def _active(self, now):
        with self.lock:
            return {w for w, t in self.wanted.items() if now - t <= IDLE_AFTER}

    def _get_sessions(self):
        if self.sessions is None:
            self.sessions = self.sessions_type()
        return self.sessions

    def _get_audio(self):
        if self.audio is None:
            self.audio = self.audio_type()
        return self.audio

    def _read_now(self, force=False):
        try:
            info = self._get_sessions().now_playing()
        except OSError as e:
            self.sessions = None
            with self.lock:
                self.state["error"] = str(e)
            return None
        with self.lock:
            self.state["now"] = info
        return info

    def _read_volume(self):
        a = self._get_audio()
        level, muted = a.volume()
        mic = a.mic_muted()
        with self.lock:
            if (level, muted) != (self.state["volume"], self.state["muted"]) and self.state["volume"] is not None:
                self.state["volume_changed"] = self.clock()
            self.state["volume"], self.state["muted"], self.state["mic_muted"] = level, muted, mic

    def _control(self, action, value):
        if action in ("play", "pause", "toggle", "next", "previous", "stop"):
            ok = self._get_sessions().control(action)
            self.next_now = 0.0
            return {"ok": ok, "action": action}
        a = self._get_audio()
        level, muted = a.volume()
        if level is None:
            return {"ok": False, "action": action, "error": "no speakers found"}
        if action in ("mute", "unmute"):
            a.set_mute(action == "mute")
        else:
            step = 10 if value is None else max(1, min(100, int(value)))
            target = {"volume_up": level + step, "volume_down": level - step}.get(action)
            if action == "set_volume":
                if value is None:
                    raise ValueError("set_volume needs a value from 0 to 100")
                target = int(value)
            a.set_volume(max(0, min(100, target)))
        self._read_volume()
        with self.lock:
            return {"ok": True, "action": action, "volume": self.state["volume"], "muted": self.state["muted"]}

    def _step(self):
        now = self.clock()
        active = self._active(now)
        if "meter" in active:
            p = self._get_audio().peaks()
            with self.lock:
                self.state["peaks"] = p
        if "volume" in active and now >= self.next_volume:
            self.next_volume = now + 0.25
            self._read_volume()
        if "now" in active and now >= self.next_now:
            self.next_now = now + 1.0
            self._read_now()

    def _run(self):
        try:
            _rt().RoInitialize(1)                   # multithreaded apartment: also initialises COM
        except OSError:
            pass
        while True:
            try:
                fn, box, done = self.requests.get(timeout=0.05)
                try:
                    box["value"] = fn(self)
                except Exception as e:      # handed back to the caller
                    box["error"] = e
                done.set()
            except queue.Empty:
                pass
            try:
                self._step()
            except Exception as e:          # a reading must never take the service down
                with self.lock:
                    self.state["error"] = str(e)
                self.audio = None


class FakeMediaWatch(MediaWatch):
    """A made-up song and a moving level, for the panel's samples and the tests. Never touches Windows."""

    def __init__(self, clock=time.time):
        super().__init__(clock=clock, threads=False)
        self.state.update({"now": {"title": "Sample song", "artist": "Some band", "album": "", "app": "Spotify",
                                   "app_id": "Spotify.exe", "status": "playing", "position": 62.0, "duration": 214.0},
                           "volume": 40, "muted": False, "mic_muted": False, "peaks": (0.6, 0.45)})
        self.controls = []

    def snapshot(self):
        import math
        t = self.clock()
        with self.lock:
            self.state["peaks"] = (0.5 + 0.4 * math.sin(t * 3), 0.5 + 0.4 * math.sin(t * 3.7 + 1))
        return super().snapshot()

    def request(self, fn, timeout=4.0):
        return fn(self)

    def _read_now(self, force=False):
        return self.state["now"]

    def _read_volume(self):
        pass

    def _control(self, action, value):
        self.controls.append((action, value))
        if action == "set_volume":
            self.state["volume"] = int(value)
        elif action in ("mute", "unmute"):
            self.state["muted"] = action == "mute"
        elif action in ("pause", "play", "toggle"):
            n = self.state["now"]
            n["status"] = {"pause": "paused", "play": "playing"}.get(
                action, "paused" if n["status"] == "playing" else "playing")
        return {"ok": True, "action": action, "volume": self.state["volume"], "muted": self.state["muted"]}
