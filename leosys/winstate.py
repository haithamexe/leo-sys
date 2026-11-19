"""Small readings from Windows that the key lights use. Polled, never hooked: nothing typed is seen.

Only four things are read here: whether Caps Lock is on, whether Ctrl, Alt, Shift or the Windows key is held down (never a
letter key), and the name of the program whose window has focus (its .exe file name, nothing from inside the window), and
the owner of a window that flashed. (Counting key presses is a separate, opt-in feature: typingmeter.py.)
"""
import os
import time

VK_CAPITAL = 0x14
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
EXE_CACHE_S = 0.3           # how long the focused program's name is trusted before asking Windows again

_exe_cache = {"t": 0.0, "hwnd": None, "exe": ""}


def caps_on():
    if os.name != "nt":
        return False
    import ctypes
    return bool(ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 1)


def modifiers():
    """The modifier keys held down right now, as a frozenset of 'ctrl', 'alt', 'shift', 'win'."""
    if os.name != "nt":
        return frozenset()
    import ctypes
    down = ctypes.windll.user32.GetAsyncKeyState
    held = set()
    if down(VK_CONTROL) & 0x8000:
        held.add("ctrl")
    if down(VK_MENU) & 0x8000:
        held.add("alt")
    if down(VK_SHIFT) & 0x8000:
        held.add("shift")
    if down(VK_LWIN) & 0x8000 or down(VK_RWIN) & 0x8000:
        held.add("win")
    return frozenset(held)


def exe_of_window(hwnd):
    """The file name (lower case) of the program that owns a window, or '' when it cannot be read."""
    if os.name != "nt" or not hwnd:
        return ""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def foreground_exe():
    """The file name of the program that has focus, lower case ('chrome.exe'), or '' when it cannot be read."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    now = time.monotonic()
    if hwnd and hwnd == _exe_cache["hwnd"] and now - _exe_cache["t"] < EXE_CACHE_S:
        return _exe_cache["exe"]
    exe = ""
    if hwnd:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    exe = os.path.basename(buf.value).lower()
            finally:
                kernel32.CloseHandle(handle)
    _exe_cache.update(t=now, hwnd=hwnd, exe=exe)
    return exe


def input_state():
    """(modifiers held, focused program) in one call: what the smart key lights look at."""
    return modifiers(), foreground_exe()
