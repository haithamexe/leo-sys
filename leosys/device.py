"""The link to the keyboard: one object the engine's worker thread owns. A pretend one for the demo and tests."""


class Keyboard:
    """The real AMG65 over USB HID. Every method raises on a lost link; the engine reconnects."""

    name = "LEOBOG AMG65"

    def __init__(self):
        self.cmd = self.bulk = None

    def connect(self):
        from .hid import open_channels
        self.cmd, self.bulk = open_channels()

    def matrix(self, pixels):
        from . import matrix
        matrix.send_frame(self.cmd, pixels)

    def keys(self, colours):
        from . import keys
        keys.send(self.cmd, colours)

    def close(self):
        for ch in (self.cmd, self.bulk):
            try:
                if ch:
                    ch.close()
            except Exception:
                pass
        self.cmd = self.bulk = None


class DemoKeyboard:
    """Accepts everything and remembers the last of each, so the panel and the tests have something to look at."""

    name = "pretend keyboard"

    def __init__(self):
        self.frames = self.key_sets = 0
        self.last_matrix = self.last_keys = None

    def connect(self):
        pass

    def matrix(self, pixels):
        self.frames += 1
        self.last_matrix = pixels

    def keys(self, colours):
        self.key_sets += 1
        self.last_keys = colours

    def close(self):
        pass


OFFICIAL_EXE = "DeviceDriver.exe"


def _process_listed(name):
    """Is a program with this file name running? One look at Windows' process list (about a millisecond, no new process)."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)                      # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        raise OSError("could not list the running programs")
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = k32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == name.lower():
                return True
            more = k32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        k32.CloseHandle(snap)


def official_app_running():
    """Is the Leobog driver app open? It writes to the same channels, so leo-sys steps aside for it (or the panel warns)."""
    import os
    import subprocess
    if os.name != "nt":
        return False
    try:
        return _process_listed(OFFICIAL_EXE)
    except (OSError, AttributeError, ValueError):
        pass                                                          # fall back to asking tasklist, which is slower
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DeviceDriver.exe", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "DeviceDriver.exe" in out
