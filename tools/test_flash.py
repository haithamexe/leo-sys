"""Manual test for the taskbar alerts: a throwaway window that flashes its taskbar button (as Discord or WhatsApp would),
then closes itself. With "Flash the keys when a program flashes in the taskbar" switched on, the keys should flash and
the matrix should say PYTHON !.   Usage: python tools/test_flash.py [seconds]

Windows only reports a flash for a window that is NOT the foreground window (a foreground window's flash is cancelled at
once), so the window hands the focus to the taskbar first.
"""
import ctypes
import sys
import time
import tkinter
from ctypes import wintypes

u = ctypes.windll.user32


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]


root = tkinter.Tk()
root.title("leo-sys flash test")
root.geometry("200x60+50+50")
root.update()
hwnd = u.GetParent(root.winfo_id())
tray = u.FindWindowW("Shell_TrayWnd", None)
for _ in range(60):                             # make sure another window has the focus before flashing
    u.SetForegroundWindow(tray)
    root.update()
    time.sleep(0.05)
    if u.GetForegroundWindow() != hwnd:
        break
info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x2 | 0xC, 0, 0)       # FLASHW_TRAY | FLASHW_TIMERNOFG
u.FlashWindowEx(ctypes.byref(info))
print("flashed", hwnd, flush=True)
end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 5)
while time.time() < end:
    root.update()
    time.sleep(0.05)
root.destroy()
