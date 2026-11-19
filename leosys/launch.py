"""Where leo-sys lives and how to start it again. Standard library only, and no imports from the rest of the package.

Four ways it can be running, and each knows how to start itself again:
- the installed app: one `leo-sys.exe` with the code inside (see appinstall.py)
- an app file that has not been installed yet (the setup .exe you just downloaded)
- a checkout: `python leo.py ...`
- a pip-style install: `python -m leosys ...`
"""
import os
import sys
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent  # a checkout's folder, or the .exe itself when packed


def archive_path():
    """The .exe this code is running from, or None when running from files."""
    archive = getattr(globals().get("__loader__"), "archive", None)
    return Path(archive) if archive else None


def packed():
    return archive_path() is not None


def local_appdata():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def install_dir():
    return local_appdata() / "Programs" / "leo-sys"


def install_exe():
    return install_dir() / "leo-sys.exe"


def app_data_dir():
    """Settings, token and log. Outside the program folder, so updating or reinstalling never touches them."""
    return local_appdata() / "leo-sys"


def installed():
    return os.name == "nt" and install_exe().is_file()


def checkout_entry():
    return CHECKOUT / "leo.py"


def is_checkout():
    return checkout_entry().is_file()


def console_python():
    """A python.exe that has stdin and stdout (hooks and the MCP server need them)."""
    exe = Path(sys.executable)
    return exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists() else exe


def windowless_python():
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return pyw if pyw.exists() else exe


def entry_args():
    """What follows a Python executable on a command line to run leo-sys."""
    if installed():
        return [str(install_exe())]
    archive = archive_path()
    if archive:
        return [str(archive)]
    if is_checkout():
        return [str(checkout_entry())]
    return ["-m", "leosys"]


def service_command():
    """How to start the background service, with no console window."""
    if installed():
        return [str(install_exe()), "daemon"]
    return [str(windowless_python()), *entry_args(), "daemon"]


def tool_command():
    """Prefix for commands that need stdin and stdout: the hooks and the MCP server."""
    return [str(console_python()), *entry_args()]


def quote(parts):
    """A command line for the Windows Run key or a hook: every part in double quotes."""
    return " ".join(f'"{p}"' for p in parts)
