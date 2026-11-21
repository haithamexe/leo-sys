"""The leo-sys command line. Runs from a checkout (`python leo.py ...`) and from inside leo-sys.exe."""
import json
import os
import subprocess
import sys
import tempfile
import time
import webbrowser

from . import __version__, launch

USAGE = """leo-sys {version}: Claude Code's live status and more on a LEOBOG AMG65 keyboard (matrix and key lights only)

  setup               one-step install: puts leo-sys.exe in your programs folder, starts with Windows, adds the
                      Claude Code hooks and MCP server and a desktop icon
                      (options: --no-app --no-hooks --no-mcp --no-autostart --no-shortcut)
  uninstall           undo everything setup did
  gui                 open the control panel (starts the service if needed)
  shortcut            put a leo-sys icon on your desktop (also: shortcut startmenu | shortcut remove)
  install             only build and install leo-sys.exe
  build-installer     build leo-sys-setup.exe, the file to give to other people (option: --out FOLDER)
  start | stop        run / stop the background service
  off | on            the master switch: off lets go of the keyboard completely (for the Leobog app) and keeps the
                      hooks, the MCP server and Windows start-up from using it; on gives it back
  daemon            run the service in the foreground (--demo: with a pretend keyboard)
  demo                try the panel without the keyboard (a pretend one on port 47843)
  face <mood>         show a face: idle thinking working attention done error sleep splash
  text <message>      show a message on the matrix
  push                show text or a colour from a script (see below)
  status              what is connected and showing
  hook                for Claude Code hooks (reads the event on stdin)
  mcp                 the MCP server (stdio)
  version             print the version
"""


PUSH_USAGE = """leo-sys push: let a script show something (only while "Allow pushes from scripts" is on in the panel)

  push text "BUILD OK" [--colour green] [--keys green] [--seconds 10]     scroll text on the matrix (use - to read it from stdin)
  push colour red [--seconds 10]                                          every key in one colour
  push bar 40 [--colour blue] [--keys blue] [--seconds 10]                a progress bar on the matrix
  push clear                                                              hand everything back

Colours are names (red green blue orange yellow white purple cyan pink amber) or #rrggbb. Exit code 0 means it was shown."""


def parse_push(rest, stdin=None):
    """The request for `push ...`, or ValueError with what to tell the user."""
    args, flags, words, i = list(rest), {}, [], 0
    while i < len(args):
        a = args[i]
        if a in ("--colour", "--color", "--keys", "--seconds"):
            if i + 1 >= len(args):
                raise ValueError(f"{a} needs a value")
            flags["--colour" if a == "--color" else a] = args[i + 1]
            i += 2
        else:
            words.append(a)
            i += 1
    if not words:
        raise ValueError(PUSH_USAGE)
    what, words = words[0], words[1:]
    if what == "text":
        text = " ".join(words)
        if text == "-":
            text = (stdin if stdin is not None else sys.stdin.read())
        text = " ".join(text.split())
        if not text:
            raise ValueError("push text needs some text (or - to read it from stdin)")
        req = {"cmd": "text", "text": text}
    elif what in ("colour", "color"):
        if len(words) != 1:
            raise ValueError("push colour needs one colour, like red or #ff0000")
        req = {"cmd": "keys", "colour": words[0]}
    elif what == "bar":
        try:
            req = {"cmd": "bar", "percent": float(words[0])} if len(words) == 1 else None
        except ValueError:
            req = None
        if req is None:
            raise ValueError("push bar needs a percentage from 0 to 100, like 40")
    elif what == "clear":
        req = {"cmd": "clear"}
    else:
        raise ValueError(PUSH_USAGE)
    if "--colour" in flags and what not in ("colour", "color"):
        req["colour"] = flags["--colour"]
    if "--keys" in flags:
        req["keys"] = flags["--keys"]
    if "--seconds" in flags:
        try:
            req["seconds"] = float(flags["--seconds"])
        except ValueError:
            raise ValueError("--seconds needs a number")
    return req


def _say(message, error=False):
    """Print, or pop a message box when there is no console (the desktop icon and the app run without one)."""
    if sys.stdout is None and os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "leo-sys", 0x10 if error else 0x40)
    else:
        print(message)


def _message_box(text, style=0x40):
    import ctypes
    return ctypes.windll.user32.MessageBoxW(0, text, "leo-sys", style)


def interactive(ask=None, show=None, open_panel=None):
    """What happens when leo-sys.exe is double-clicked: it has no console, so it talks in dialogs.
    The installed app opens the panel; a downloaded setup file offers to install (or update) it."""
    ask = ask or (lambda text: _message_box(text, 0x24) == 6)  # Yes / No
    show = show or _message_box
    open_panel = open_panel or (lambda: main(["gui"]))
    here = launch.archive_path()
    if launch.installed() and here and here.resolve() == launch.install_exe().resolve():
        open_panel()
        return 0
    verb = "Update" if launch.installed() else "Install"
    if not ask(f"{verb} leo-sys {__version__}?\n\nIt starts with Windows and runs in the background. It adds the Claude "
               "Code hooks, an MCP server and a desktop icon. It only drives the keyboard's matrix and key lights, "
               "never its LCD. You can remove it any time from Settings, Apps."):
        return 0
    from .setup_cmd import setup
    lines = []
    code = setup(say=lines.append)
    show("\n".join(lines))
    if code == 0:
        open_panel()
    return code


def main(argv):
    try:
        return _main(argv)
    except ConnectionError as e:             # no service and none could be started (it is switched off, or would not start)
        print(e)
        return 1


def _main(argv):
    if not argv:
        if sys.stdout is None and os.name == "nt" and launch.packed():  # double-clicked: no console to print to
            return interactive()
        print(USAGE.format(version=__version__))
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "daemon" and "--demo" in rest:   # a pretend keyboard, its own port and throw-away settings
        os.environ.update(LEO_DEMO="1", LEO_PORT="47843", LEO_DATA=tempfile.mkdtemp(prefix="leo-demo-"))
    if cmd == "daemon":
        from .daemon import run
        run()
    elif cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    elif cmd == "hook":
        from .hook import run
        run()
    elif cmd in ("version", "--version", "-V"):
        print(__version__)
    elif cmd == "setup":
        from .setup_cmd import setup
        return setup(app="--no-app" not in rest, hooks="--no-hooks" not in rest, mcp="--no-mcp" not in rest,
                     autostart="--no-autostart" not in rest, shortcut="--no-shortcut" not in rest)
    elif cmd == "install":
        from . import appinstall
        appinstall.install()
        print("installed. `leo-sys.exe setup` (or `python leo.py setup`) finishes the setup.")
    elif cmd == "build-installer":
        from . import appinstall
        out = rest[rest.index("--out") + 1] if "--out" in rest and rest.index("--out") + 1 < len(rest) else "dist"
        path = appinstall.build_installer(os.path.join(out, "leo-sys-setup.exe"))
        print(f"built {path} ({os.path.getsize(path) // 1024} KB). It needs Python 3.9+ on the PATH of the PC it runs on.")
    elif cmd == "uninstall":
        from .setup_cmd import uninstall
        lines = []
        code = uninstall(say=lines.append)
        _say("\n".join(lines))
        return code
    elif cmd == "shortcut":
        from . import setup_cmd
        which = rest[0] if rest else "desktop"
        try:
            if which == "remove":
                print(f"removed {sum(setup_cmd.remove_shortcut(w) for w in ('desktop', 'startmenu'))} shortcut(s)")
            else:
                print("created", setup_cmd.create_shortcut(which))
        except (RuntimeError, ValueError, OSError) as e:
            print("could not create the shortcut:", e)
            return 1
    elif cmd == "demo":
        env = dict(os.environ, LEO_DEMO="1", LEO_DATA=tempfile.mkdtemp(prefix="leo-demo-"), LEO_PORT="47843")
        proc = subprocess.Popen([*launch.tool_command(), "daemon"], env=env)
        time.sleep(2)
        webbrowser.open("http://127.0.0.1:47843/")
        print("Demo running on http://127.0.0.1:47843/ (no keyboard needed). Press Ctrl+C to close it.")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
    elif cmd in ("off", "on"):
        from . import power
        sw = power.Switch()
        sw.set_off(cmd == "off")             # the service notices the file by itself within a moment
        print("leo-sys is switched off: it holds nothing, and the hooks and the MCP server will not start it" if cmd == "off"
              else "leo-sys is switched on")
    elif cmd == "start":
        from .client import ensure_daemon
        print("running" if ensure_daemon(force=True) else "could not start")
    elif cmd == "stop":
        from .client import stop_daemon
        print("stopped" if stop_daemon() else "not running")
    elif cmd == "gui":
        from .client import ensure_daemon
        from .config import HOST, PORT
        if not ensure_daemon(force=True):          # you asked for the panel, so it starts (idle, if switched off)
            _say("leo-sys could not start its background service. See daemon.log in the leo-sys data folder.", True)
            return 1
        webbrowser.open(f"http://{HOST}:{PORT}/")
    elif cmd == "face" and rest:
        from .client import send
        print(send({"cmd": "face", "mood": rest[0]}, "mcp"))
    elif cmd == "text" and rest:
        from .client import send
        print(send({"cmd": "text", "text": " ".join(rest)}, "mcp"))
    elif cmd == "push":
        from .client import send
        try:
            req = parse_push(rest)
        except ValueError as e:
            print(e)
            return 1
        try:
            reply = send(req, "push")
        except ConnectionError as e:
            print(e)
            return 1
        if not reply.get("ok"):
            print(reply.get("denied") or reply.get("error") or "not shown")
            return 1
        print("ok")
    elif cmd == "status":
        from .client import get
        s = get("/api/state")
        print(json.dumps({"switched_off": s.get("off", False), "connected": s["connected"], "error": s["error"], "pinned": s["pinned"],
                          "stepped_aside": s.get("stepped_aside", False), "leobog_app_open": s["official_app"], "showing": s["showing"]["label"],
                          "sessions": len(s["sessions"])}, indent=2))
    else:
        print(USAGE.format(version=__version__))
        return 1
    return 0
