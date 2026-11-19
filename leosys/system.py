"""Start with Windows (one value under the HKCU Run key), standard library only."""
import os

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "leo-sys"


def _daemon_command():
    from . import launch
    return launch.quote(launch.service_command())


def autostart_enabled():
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    if os.name != "nt":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _daemon_command())
        else:
            try:
                winreg.DeleteValue(k, _RUN_NAME)
            except OSError:
                pass
