"""`python leo.py setup` and `uninstall`: wire leo-sys into Windows and Claude Code in one step.

Everything that touches your own configuration is idempotent, keeps a backup of settings.json, and refuses to
overwrite a settings file it cannot parse.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import launch, power
from .config import DATA_DIR

HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification",
               "SubagentStart", "SubagentStop", "Stop", "SessionEnd"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
_OURS = re.compile(r"(?:leo\.py|leo-sys\.exe|leosys)[\"']?\s+hook")  # how we recognise our own hook entries
BACKUP_SUFFIX = ".bak-before-leo-sys"


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def hook_command():
    return launch.quote(launch.tool_command()) + " hook"


def mcp_args():
    return launch.tool_command() + ["mcp"]


def _is_ours(entry):
    """Does this hook entry run our script? Looks at the command text itself, not its JSON-escaped form."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    return any(isinstance(h, dict) and _OURS.search(str(h.get("command", ""))) for h in (hooks or []))


def remove_hooks(settings):
    """Drop our entries (and any event left empty). Returns how many entries were removed."""
    removed = 0
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    for event in list(hooks):
        entries = hooks[event] if isinstance(hooks[event], list) else []
        kept = [e for e in entries if not _is_ours(e)]
        removed += len(entries) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return removed


def merge_hooks(settings, command=None):
    """Add our hook for every event, replacing older copies of it and leaving everything else alone."""
    command = command or hook_command()
    remove_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entry = {"hooks": [{"type": "command", "command": command, "timeout": 10, "async": True}]}
        if event in TOOL_EVENTS:
            entry["matcher"] = "*"
        hooks.setdefault(event, []).append(entry)
    return settings


def read_settings(path):
    """The parsed settings file, {} if it does not exist. Raises ValueError if it exists but is not valid JSON."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"{path} is not valid JSON ({e}); fix it by hand first, nothing was changed") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object; nothing was changed")
    return data


def write_settings(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


SHORTCUT_NAME = "leo-sys.lnk"
_FOLDERS = {"desktop": "Desktop", "startmenu": "Programs"}


def _ps(text):
    """A PowerShell single-quoted string (a quote inside is doubled)."""
    return "'" + str(text).replace("'", "''") + "'"


def shortcut_script(folder_kind, target, arguments, workdir, icon, description, folder=None):
    """PowerShell that creates the shortcut and prints its path. `folder` overrides the Windows folder (for tests)."""
    where = _ps(folder) if folder else "[Environment]::GetFolderPath(" + _ps(folder_kind) + ")"
    return ("$dir = " + where + "; $path = Join-Path $dir " + _ps(SHORTCUT_NAME) + "; "
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($path); "
            f"$s.TargetPath = {_ps(target)}; $s.Arguments = {_ps(arguments)}; $s.WorkingDirectory = {_ps(workdir)}; "
            f"$s.IconLocation = {_ps(str(icon) + ',0')}; $s.Description = {_ps(description)}; $s.Save(); $path")


def create_shortcut(where="desktop", folder=None):
    """A clickable shortcut that starts the service if needed and opens the panel, with no console window.
    Returns the path of the .lnk file."""
    if os.name != "nt":
        raise RuntimeError("shortcuts are only supported on Windows")
    if where not in _FOLDERS:
        raise ValueError("where must be 'desktop' or 'startmenu'")
    if launch.installed():  # the app: the icon comes from the exe itself
        target, arguments, workdir, ico = launch.install_exe(), "gui", launch.install_dir(), launch.install_exe()
    else:
        from . import icon
        ico = icon.write_icon(DATA_DIR / "leo-sys.ico")
        target, arguments = launch.windowless_python(), launch.quote(launch.entry_args()) + " gui"
        workdir = launch.CHECKOUT if launch.is_checkout() else Path.home()
    script = shortcut_script(_FOLDERS[where], target, arguments, workdir, ico, "Open the leo-sys control panel", folder)
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, timeout=30, creationflags=0x08000000)
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        raise RuntimeError((done.stderr or "could not create the shortcut").strip()[:200])
    return lines[-1]


def remove_shortcut(where="desktop"):
    """Deletes our shortcut from that folder if it is there. Returns True if one was removed."""
    if os.name != "nt" or where not in _FOLDERS:
        return False
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           "[Environment]::GetFolderPath(" + _ps(_FOLDERS[where]) + ")"],
                          capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    folder = done.stdout.strip()
    target = Path(folder) / SHORTCUT_NAME if folder else None
    if target and target.is_file():
        os.remove(target)  # exactly our one file, never a pattern
        return True
    return False


def _claude(*args):
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def register_mcp():
    """Returns a short status string."""
    if not shutil.which("claude"):
        return "skipped: the `claude` command was not found. Run this yourself:\n    claude mcp add --scope user leo-sys -- " + " ".join(f'"{a}"' for a in mcp_args())
    _claude("mcp", "remove", "leo-sys", "--scope", "user")  # idempotent: replace an older entry
    done = _claude("mcp", "add", "--scope", "user", "leo-sys", "--", *mcp_args())
    return "registered" if done and done.returncode == 0 else "failed: " + ((done.stderr or done.stdout).strip()[:200] if done else "could not run claude")


def unregister_mcp():
    done = _claude("mcp", "remove", "leo-sys", "--scope", "user")
    return "removed" if done and done.returncode == 0 else "nothing to remove (or the `claude` command was not found)"


def setup(app=True, hooks=True, mcp=True, autostart=True, shortcut=True, say=print):
    from . import client

    say("leo-sys setup")
    if app and os.name == "nt":
        from . import appinstall
        try:
            appinstall.install(say)
        except (RuntimeError, OSError) as e:
            say(f"  app: could not install ({e}); continuing with the scripts in place")
    if not client.ensure_daemon(force=True):
        say("  ! could not start the background service; see daemon.log in the leo-sys data folder")
        return 1
    token = client.admin_token()
    state = client._request("GET", "/api/state")
    if state.get("off"):
        say("  leo-sys is switched OFF (your choice, kept across updates): it holds nothing. Switch it on in the panel.")
    elif state.get("connected"):
        say("  keyboard: connected")
    else:
        say(f"  keyboard: not connected yet ({state.get('error') or 'plug it in by cable'})")
    if state.get("official_app"):
        say("  ! the Leobog driver app is open; close it from its tray icon, it writes to the same screens")
    if autostart:
        from . import system
        system.set_autostart(True)  # rewrites the Run entry, so an older one pointing at leo.py now points at the app
        if token:
            client._request("POST", "/api/settings", {"autostart": True}, token=token)
        say("  start with Windows: on")
    if hooks:
        path = claude_dir() / "settings.json"
        try:
            data = read_settings(path)
        except ValueError as e:
            say(f"  hooks: {e}")
        else:
            write_settings(path, merge_hooks(data))
            say(f"  hooks: {len(HOOK_EVENTS)} events registered in {path} (backup kept as {BACKUP_SUFFIX})")
    if mcp:
        say(f"  MCP server: {register_mcp()}")
    if shortcut and os.name == "nt":
        try:
            say(f"  desktop shortcut: {create_shortcut('desktop')}")
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            say(f"  desktop shortcut: skipped ({e})")
    try:
        client.send({"cmd": "face", "mood": "splash"}, "mcp")
    except ConnectionError:
        pass
    say("Done. Restart Claude Code so it picks up the hooks and the MCP server. Open the panel from the desktop icon.")
    return 0


def uninstall(say=print):
    from . import client

    say("leo-sys uninstall")
    path = claude_dir() / "settings.json"
    try:
        data = read_settings(path)
        n = remove_hooks(data)
        if n:
            write_settings(path, data)
        say(f"  hooks: removed {n} entries")
    except ValueError as e:
        say(f"  hooks: {e}")
    say(f"  MCP server: {unregister_mcp()}")
    say(f"  shortcuts: removed {sum(remove_shortcut(w) for w in _FOLDERS)}")
    if os.name == "nt":
        from . import appinstall
        appinstall.uninstall(say)
    token = client.admin_token()
    try:
        if token:
            client._request("POST", "/api/settings", {"autostart": False}, token=token)
    except OSError:
        pass
    try:
        from . import system
        system.set_autostart(False)
    except Exception:
        pass
    say("  start with Windows: off")
    power.Switch().set_off(False)                      # a later reinstall starts working, not stuck off
    say(f"  background service: {'stopped' if client.stop_daemon() else 'not running'}")
    say(f"Your settings and logs stay in {DATA_DIR}; delete that folder to remove everything.")
    return 0
