"""Builds and installs leo-sys.exe: one file with the program inside, with its own name and icon.

Why: Windows names a startup entry after the program it runs. Running `pythonw.exe leo.py` shows up as "Python".
A real leo-sys.exe shows up as "leo-sys", with its own icon, and there are no loose .py files to keep track of.

How, with nothing but the standard library:
1. Take the small windowless launcher stub that pip ships (the one pip uses for pip.exe and friends).
2. Stamp leo-sys's icon and version information onto it with Windows' own resource functions.
3. Append one line naming the Python to use, then the program itself as a zip. When started, the stub runs
   `<that python> leo-sys.exe <arguments>` and Python runs the zip at the end of the file.

The launcher is pip's small windowless stub with the program appended as a zip.
"""
import ctypes
import importlib.util
import io
import os
import platform
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import __version__, icon, launch

PRODUCT = COMPANY = DESCRIPTION = "leo-sys"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\leo-sys"
RT_ICON, RT_GROUP_ICON, RT_VERSION, LANG_US, LANG_NEUTRAL = 3, 14, 16, 0x0409, 0
MAIN = "import sys\nfrom leosys.cli import main\nsys.exit(main(sys.argv[1:]))\n"


# ---- the launcher stub --------------------------------------------------------------------
def stub_name():
    if struct.calcsize("P") == 8:
        return "w64-arm.exe" if "arm" in platform.machine().lower() else "w64.exe"
    return "w32.exe"


def find_stub():
    """pip's windowless launcher for this Python."""
    spec = importlib.util.find_spec("pip")
    if spec and spec.submodule_search_locations:
        path = Path(list(spec.submodule_search_locations)[0]) / "_vendor" / "distlib" / stub_name()
        if path.is_file():
            return path
    raise RuntimeError("pip's launcher stub was not found. Run: python -m ensurepip")


# ---- version information ------------------------------------------------------------------
def _block(key, value=b"", children=(), text=False, value_len=0):
    """One node of a Windows version resource: header, key, value, children, each aligned to 4 bytes."""
    body = bytearray(6) + bytearray((key + "\0").encode("utf-16-le"))
    body += b"\0" * (-len(body) % 4)
    body += value
    for child in children:
        body += b"\0" * (-len(body) % 4)
        body += child
    struct.pack_into("<HHH", body, 0, len(body), value_len, 1 if text else 0)
    return bytes(body)


def version_resource(version, strings):
    """The binary VS_VERSIONINFO for `strings` (name -> text) and a dotted version like 0.2.0."""
    nums = ([int(n) for n in version.split(".")] + [0, 0, 0, 0])[:4]
    ms, ls = (nums[0] << 16) | nums[1], (nums[2] << 16) | nums[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    entries = [_block(k, (v + "\0").encode("utf-16-le"), text=True, value_len=len(v) + 1) for k, v in strings.items()]
    table = _block("040904B0", children=entries, text=True)
    string_info = _block("StringFileInfo", children=[table], text=True)
    translation = _block("Translation", struct.pack("<HH", LANG_US, 0x04B0), value_len=4)
    var_info = _block("VarFileInfo", children=[translation], text=True)
    return _block("VS_VERSION_INFO", fixed, [string_info, var_info], value_len=len(fixed))


def existing_resources(path, kinds):
    """[(type, name, language)] of the resources of these types already in an .exe (numeric names only)."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    k32.LoadLibraryExW.restype = wintypes.HMODULE
    k32.FreeLibrary.argtypes = [wintypes.HMODULE]
    names_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    langs_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                  ctypes.c_void_p)
    k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, names_cb, ctypes.c_ssize_t]
    k32.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, langs_cb, ctypes.c_ssize_t]
    module = k32.LoadLibraryExW(str(path), None, 0x2)  # LOAD_LIBRARY_AS_DATAFILE: look, do not run
    if not module:
        return []
    found = []

    def per_kind(kind):
        @names_cb
        def on_name(mod, rtype, name, _):
            if name is not None and name < 65536:  # a numeric id (a text name is a pointer)
                @langs_cb
                def on_lang(m, t, n, lang, _p):
                    found.append((kind, name, lang))
                    return True
                k32.EnumResourceLanguagesW(mod, rtype, name, on_lang, 0)
            return True
        k32.EnumResourceNamesW(module, kind, on_name, 0)

    try:
        for kind in kinds:
            per_kind(kind)
    finally:
        k32.FreeLibrary(module)
    return found


def stamp(path, version, strings, images):
    """Write the version information and the icon into an .exe (before anything is appended to it)."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k32.BeginUpdateResourceW.restype = wintypes.HANDLE
    k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                    ctypes.c_void_p, wintypes.DWORD]
    k32.UpdateResourceW.restype = wintypes.BOOL
    k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    k32.EndUpdateResourceW.restype = wintypes.BOOL
    old = existing_resources(path, (RT_ICON, RT_GROUP_ICON, RT_VERSION))  # the stub's own icon and version info
    handle = k32.BeginUpdateResourceW(str(path), False)
    if not handle:
        raise OSError(f"could not open {path} for stamping (error {ctypes.get_last_error()})")

    def put(kind, name, data, lang=LANG_NEUTRAL):
        buf = ctypes.create_string_buffer(data, len(data))
        if not k32.UpdateResourceW(handle, kind, name, lang, buf, len(data)):
            raise OSError(f"could not write a resource (error {ctypes.get_last_error()})")

    try:
        # Two icons or two version blocks in one file would compete, and Windows may pick the stub's. Replace them.
        for kind, name, lang in old:
            if not k32.UpdateResourceW(handle, kind, name, lang, None, 0):
                raise OSError(f"could not remove the stub's own resource (error {ctypes.get_last_error()})")
        put(RT_VERSION, 1, version_resource(version, strings))
        for i, (_, png) in enumerate(images, start=1):
            put(RT_ICON, i, png)
        group = struct.pack("<HHH", 0, 1, len(images)) + b"".join(
            struct.pack("<BBBBHHIH", size % 256, size % 256, 0, 0, 1, 32, len(png), i)
            for i, (size, png) in enumerate(images, start=1))
        put(RT_GROUP_ICON, 1, group)
    except Exception:
        k32.EndUpdateResourceW(handle, True)  # throw the changes away
        raise
    if not k32.EndUpdateResourceW(handle, False):
        raise OSError(f"could not save the stamped file (error {ctypes.get_last_error()})")


def _replace(src, dst, tries=40, delay=0.5):
    """os.replace that waits out a short lock: a hook, the running service or antivirus may hold the file for a moment."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay)


# ---- building ---------------------------------------------------------------------------
def program_zip():
    """The program as a zip: the leosys package (code and the panel page), plus a __main__ that runs the command line."""
    src = launch.CHECKOUT / "leosys"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", MAIN)
        for p in sorted(src.rglob("*")):
            if p.is_file() and p.suffix in (".py", ".html") and "__pycache__" not in p.parts:
                z.write(p, "leosys/" + p.relative_to(src).as_posix())
    return buf.getvalue()


def shebang(python):
    text = str(python)
    return b"#!" + (f'"{text}"' if " " in text else text).encode("utf-8") + b"\n"  # the format pip's launcher reads


def build_exe(dest, python=None, original="leo-sys.exe"):
    """Make leo-sys.exe at `dest`. `python` is the interpreter the launcher will use (default: pythonw)."""
    if os.name != "nt":
        raise RuntimeError("the app is only built on Windows")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".new")
    shutil.copyfile(find_stub(), tmp)
    stamp(tmp, __version__, {"CompanyName": COMPANY, "FileDescription": DESCRIPTION, "FileVersion": __version__,
                             "InternalName": "leo-sys", "OriginalFilename": original, "ProductName": PRODUCT,
                             "ProductVersion": __version__,
                             "LegalCopyright": "MIT License"}, icon.png_images())
    with open(tmp, "ab") as f:  # appended after stamping: rewriting resources would drop it
        f.write(shebang(python or launch.windowless_python()) + program_zip())
    _replace(tmp, dest)
    return dest


PORTABLE_PYTHON = "pythonw.exe"  # no folder: the launcher looks it up on PATH, so one build works on any PC with Python


def build_installer(dest):
    """leo-sys-setup.exe, the file to hand to other people. Python is found through PATH, so the same file works
    everywhere; installing it writes a copy that names the exact Python it found."""
    return build_exe(dest, python=PORTABLE_PYTHON, original="leo-sys-setup.exe")


def split_app(path):
    """(stub, program zip) of a built app file: everything before its '#!' line, and the zip."""
    data = Path(path).read_bytes()
    with zipfile.ZipFile(path) as z:
        zip_start = z.infolist()[0].header_offset  # absolute, even with the stub in front
    return data[:data.rfind(b"#!", 0, zip_start)], data[zip_start:]


def rewrite_shebang(src, dest, python):
    """Copy an app file, changing only which Python it uses."""
    stub, program = split_app(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".new")
    tmp.write_bytes(stub + shebang(python) + program)
    _replace(tmp, dest)
    return dest


# ---- installing -------------------------------------------------------------------------
def register_uninstall(exe):
    """An entry in Settings, Apps, Installed apps, so it can be removed like any other app."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in {"DisplayName": PRODUCT, "DisplayVersion": __version__, "Publisher": COMPANY,
                            "DisplayIcon": f"{exe},0", "InstallLocation": str(Path(exe).parent),
                            "UninstallString": f'"{exe}" uninstall'}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 1)


def unregister_uninstall():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        return True
    except OSError:
        return False


def migrate_settings(say=print):
    """Bring a checkout's settings over to the app's data folder. Only when the app has none yet, so it never
    overwrites choices made since. (From inside the exe the checkout is unknown, so this runs at install time.)"""
    source = launch.CHECKOUT / "data" / "settings.json"
    target = launch.app_data_dir() / "settings.json"
    if launch.packed() or not source.is_file() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    say(f"  settings: carried over from {source.parent}")
    return True


def install(say=print):
    """Put leo-sys.exe in the user's programs folder (no administrator rights needed) and register it."""
    from . import client
    dest = launch.install_exe()
    if client.stop_daemon():  # a running copy locks its own file
        time.sleep(2)
    migrate_settings(say)
    if launch.packed():
        if launch.archive_path().resolve() != dest.resolve():  # the setup file: install a copy that names this Python
            rewrite_shebang(launch.archive_path(), dest, launch.windowless_python())
    else:
        build_exe(dest)
    register_uninstall(dest)
    say(f"  app: installed {dest} (shows as 'leo-sys' in Startup apps and Installed apps)")
    return dest


_REMOVE_LATER = (
    "import os, sys, time\n"
    "exe, folder = sys.argv[1], sys.argv[2]\n"
    "for _ in range(60):\n"
    "    try:\n"
    "        os.remove(exe)\n"
    "        break\n"
    "    except FileNotFoundError:\n"
    "        break\n"
    "    except OSError:\n"
    "        time.sleep(0.5)\n"
    "try:\n"
    "    os.rmdir(folder)\n"  # only if empty: never a recursive delete
    "except OSError:\n"
    "    pass\n"
)


def uninstall(say=print):
    """Remove the Installed-apps entry and the exe. The exe cannot delete itself while it runs, so a
    short-lived helper waits for it to close, then removes exactly that one file."""
    unregister_uninstall()
    exe = launch.install_exe()
    if exe.is_file():
        subprocess.Popen([str(launch.windowless_python()), "-c", _REMOVE_LATER, str(exe), str(exe.parent)],
                         creationflags=0x08000008, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        say(f"  app: removed {exe}")
    else:
        say("  app: not installed")
