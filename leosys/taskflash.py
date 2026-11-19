"""Know when a program flashes its taskbar button (Discord, WhatsApp, a browser tab with a message...).

Windows tells programs that register a "shell hook window" about taskbar events, among them a button starting to flash
(HSHELL_FLASH). That is a notification about windows, not keyboard input: nothing typed is seen, and only the file name of
the program that flashed is kept (never the window's title, which can contain message text).

The listener is a hidden window with its own thread and message loop; it runs only while the alert switch is on.
"""
import collections
import ctypes
import threading
import time
from ctypes import wintypes

from . import winstate

HSHELL_FLASH = 0x8006                     # HSHELL_REDRAW (6) | the high bit: the button is flashing
WM_CLOSE, WM_DESTROY = 0x0010, 0x0002
COOLDOWN_S = 3.0                          # repeated flashes of one program inside this count as one

if hasattr(ctypes, "WINFUNCTYPE"):
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR)]


class FlashWatch:
    """Collects 'this program flashed' events from Windows while started."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.events = collections.deque(maxlen=50)      # {"t", "exe"}
        self.last = {}                                  # exe -> time of the last event kept
        self.unseen = []
        self.trace = None                               # a list, for debugging: every shell notification (wParam, lParam)
        self.thread = None
        self.hwnd = None
        self.error = ""
        self.ready = threading.Event()

    # ---- what the window procedure reports -------------------------------------------------------------------
    def note(self, exe, hwnd=0):
        """Keep a flash event unless the same program flashed a moment ago."""
        exe = (exe or "").lower()
        if not exe:
            return False
        now = self.clock()
        with self.lock:
            if now - self.last.get(exe, -1e9) < COOLDOWN_S:
                return False
            self.last[exe] = now
            event = {"t": now, "exe": exe, "hwnd": hwnd}
            self.events.appendleft(event)
            self.unseen.append(event)
        return True

    def pop_new(self):
        """Events since the last call, oldest first."""
        with self.lock:
            out, self.unseen = self.unseen, []
        return out

    def recent(self):
        with self.lock:
            return list(self.events)[:8]

    def seen(self, limit=8):
        """Each program that flashed lately once, newest first: [{"exe", "t", "count"}]."""
        with self.lock:
            out = {}
            for e in self.events:                       # newest first
                if e["exe"] not in out:
                    out[e["exe"]] = {"exe": e["exe"], "t": e["t"], "count": 0}
                out[e["exe"]]["count"] += 1
            return list(out.values())[:limit]

    # ---- the listener thread -----------------------------------------------------------------------------------
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, wait=3.0):
        """Start listening. `wait` is how long to wait for it to be ready (0: do not wait)."""
        if self.running():
            return
        self.error = ""
        self.ready.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="taskbar-flash")
        self.thread.start()
        if wait:
            self.ready.wait(wait)

    def stop(self):
        if self.running() and self.hwnd:
            ctypes.windll.user32.PostMessageW(wintypes.HWND(self.hwnd), WM_CLOSE, 0, 0)
            self.thread.join(2.0)
        self.thread, self.hwnd = None, None

    def clear(self):
        """Forget every flash seen (done when the switch goes off)."""
        with self.lock:
            self.events.clear()
            self.last.clear()
            self.unseen = []

    def _run(self):
        try:
            self._listen()
        except Exception as e:                  # whatever goes wrong: say so and never leave a caller waiting
            self.error = str(e) or type(e).__name__
            self.ready.set()

    def _listen(self):
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        H = ctypes.c_void_p
        u.DefWindowProcW.restype, u.DefWindowProcW.argtypes = LRESULT, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, H, wintypes.HINSTANCE, H]
        u.RegisterShellHookWindow.argtypes = [wintypes.HWND]
        u.DeregisterShellHookWindow.argtypes = [wintypes.HWND]
        u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        k.GetModuleHandleW.restype = wintypes.HINSTANCE
        shell_msg = u.RegisterWindowMessageW("SHELLHOOK")

        def proc(hwnd, msg, wparam, lparam):
            if msg == shell_msg and self.trace is not None:
                self.trace.append((wparam, lparam))
            if msg == shell_msg and wparam == HSHELL_FLASH:
                try:
                    self.note(winstate.exe_of_window(lparam), lparam)
                except Exception:               # a window procedure must never raise
                    pass
                return 0
            if msg == WM_CLOSE:
                u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        callback = WNDPROC(proc)                # kept alive for as long as the window exists
        name = f"LeoSysShellHook{id(self)}"
        cls = WNDCLASSW()
        cls.lpfnWndProc, cls.hInstance, cls.lpszClassName = callback, k.GetModuleHandleW(None), name
        try:
            if not u.RegisterClassW(ctypes.byref(cls)):
                raise OSError("could not register the window class")
            hwnd = u.CreateWindowExW(0, name, "leo-sys shell hook", 0x80000000, 0, 0, 0, 0, None, None, cls.hInstance, None)
            if not hwnd:
                raise OSError("could not create the hidden window")
            if not u.RegisterShellHookWindow(hwnd):
                raise OSError("Windows would not register the shell hook")
        except OSError as e:
            self.error = str(e)
            self.ready.set()
            return
        self.hwnd = hwnd
        self.ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.DispatchMessageW(ctypes.byref(msg))
        u.DeregisterShellHookWindow(hwnd)


class FakeFlashWatch(FlashWatch):
    """Events you add by hand, for the demo and the tests. Never touches Windows."""

    def start(self, wait=3.0):
        self.thread = object()

    def stop(self):
        self.thread = None

    def running(self):
        return self.thread is not None
