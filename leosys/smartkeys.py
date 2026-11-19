"""Smart key lights: hold Ctrl, Alt, Shift or the Windows key and only the keys that do something with it glow, coloured
by what they do. A program can have its own map (VS Code, browsers, Explorer, Photoshop are built in; add more in
shortcuts.json in the data folder); anything else uses the generic Windows shortcuts.

The keyboard has no function row, so only the keys it really has can light. Which keys are down is polled from Windows
(winstate.py): modifiers only, never letters, so nothing you type is seen.

A map is {combo: {category: "keys"}}. A combo is modifiers joined by +, in any order ("ctrl", "ctrl+shift", "win").
Key names are the keyboard's own (see keys.py) or plain ones: letters, 0-9, - = [ ] ; ' , . / \\, Tab, Esc, Space,
Enter, Backspace, Del, Home, PgUp, PgDn, Up, Down, Left, Right.
"""
import json
import re
import time
from pathlib import Path

from . import faces
from .keys import BY_NAME, KEYS

MODS = ("ctrl", "alt", "shift", "win")                      # the canonical order of a combo
HELD_KEYS = {"ctrl": ("Ctrl_L", "Ctrl_R"), "alt": ("Alt_L",), "shift": ("Shift_L", "Shift_R"), "win": ("Win_L",)}
DARK = 0.10                 # how much of the base lighting stays while a combo is shown
HINT_BASE, HINT_KEYS = 0.55, 0.75
RELOAD_EVERY = 3.0          # seconds between looks at shortcuts.json for changes

CATEGORIES = {"edit": (255, 96, 24), "file": (0, 120, 255), "nav": (0, 255, 60), "view": (190, 0, 255),
              "tool": (255, 200, 0), "danger": (255, 0, 0)}
LABELS = {"edit": "Edit", "file": "Files", "nav": "Move", "view": "View", "tool": "Tools", "danger": "Careful"}

ALIASES = {"1": "!1", "2": "@2", "3": "#3", "4": "$4", "5": "%5", "6": "^6", "7": "&7", "8": "*8", "9": "(9", "0": ")0",
           "-": "_-", "=": "+=", "[": "{[", "]": "}]", "\\": "|\\", ";": ":;", "'": "\"'", ",": ",<", ".": ".>", "/": "/?",
           "pgup": "page up", "pgdn": "page down", "delete": "del", "bksp": "Backspace", "return": "Enter", "caps": "CapsLock"}

GENERIC = {
    "ctrl": {"edit": "C X V Z Y A", "file": "S N O P", "nav": "F Tab", "view": "= -", "danger": "W"},
    "ctrl+shift": {"edit": "Z", "file": "S N", "nav": "Tab T", "tool": "Esc"},
    "alt": {"nav": "Tab Esc Left Right Up", "tool": "Space Enter Home"},
    "shift": {"nav": "Up Down Left Right Home PgUp PgDn", "edit": "Tab Del Enter"},
    "win": {"tool": "D E R I V S X A P M Space . ;", "nav": "Tab Up Down Left Right Home 1 2 3 4 5 6 7 8 9 0",
            "view": "= -", "danger": "L"},
    "win+shift": {"tool": "S M", "nav": "Left Right Up Down"},
    "ctrl+alt": {"danger": "Del", "nav": "Tab"},
}

APPS = {
    "vscode": {"name": "VS Code", "exes": ["code.exe", "code - insiders.exe", "cursor.exe"], "keys": {
        "ctrl": {"edit": "C X V Z Y A D L H / [ ] Backspace Del", "file": "S N O W", "nav": "P F G T E Tab",
                 "view": "B J , = -", "tool": "K"},
        "ctrl+shift": {"tool": "P", "nav": "F E G O Tab", "view": "X D M U V", "file": "N T S", "edit": "K Z \\ [ ]"},
        "alt": {"edit": "Up Down", "nav": "Left Right", "view": "Z"},
        "alt+shift": {"edit": "Up Down", "tool": "F"}}},
    "browser": {"name": "Browser", "exes": ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
                                            "vivaldi.exe"], "keys": {
        "ctrl": {"file": "T N O S P", "nav": "L F R Tab 1 2 3 4 5 6 7 8 9", "tool": "D H J U K E",
                 "edit": "C X V A Z Y", "view": "= - 0", "danger": "W"},
        "ctrl+shift": {"file": "T N", "tool": "I J C B Del O", "nav": "Tab R", "danger": "W", "edit": "V Z"},
        "alt": {"nav": "Left Right Home D"}}},
    "explorer": {"name": "File Explorer", "exes": ["explorer.exe"], "keys": {
        "ctrl": {"file": "N W", "nav": "E F L Tab", "edit": "C X V Z Y A", "tool": "D", "view": "= -"},
        "ctrl+shift": {"file": "N", "nav": "E", "view": "1 2 3 4 5 6 7 8"},
        "alt": {"nav": "Up Left Right D", "tool": "Enter P"},
        "shift": {"danger": "Del", "nav": "Up Down Left Right Home PgUp PgDn", "edit": "Tab Enter"}}},
    "photoshop": {"name": "Photoshop", "exes": ["photoshop.exe"], "keys": {
        "ctrl": {"edit": "Z X C V A D T J I", "tool": "L M U B E G K", "view": "= - 0 1 R H '", "file": "S N O W P"},
        "ctrl+shift": {"file": "S N", "edit": "Z I E C V J D"},
        "alt": {"edit": "Backspace Del"},
        "shift": {"tool": "M L W C S B E G O J K P T U Y R I N", "edit": "Backspace"}}},
}

EXAMPLE = {
    "_help": "Add programs here. exes are the program's file names (see 'Now' in the panel). Each combo lists keys by "
             "what they do: edit, file, nav, view, tool or danger (or a category you define in 'categories').",
    "categories": {"edit": "#ff6018"},
    "apps": {"notepad": {"name": "Notepad", "exes": ["notepad.exe"], "keys": {
        "ctrl": {"edit": "C X V Z A H", "file": "S N O P", "nav": "F G"},
        "ctrl+shift": {"file": "S N"}}}},
}


def key_name(token):
    """The keyboard's own name for a key written plainly, or None."""
    t = str(token).strip()
    if t in BY_NAME:
        return t
    low = t.lower()
    if low in ALIASES:
        return ALIASES[low]
    if len(t) == 1 and t.upper() in BY_NAME:
        return t.upper()
    for name in BY_NAME:
        if name.lower() == low:
            return name
    return None


def combo_name(mods):
    """The canonical combo for a set of held modifiers: ctrl+alt+shift+win, in that order."""
    return "+".join(m for m in MODS if m in mods)


def _combo(text):
    parts = [p.strip().lower() for p in str(text).split("+") if p.strip()]
    if not parts or any(p not in MODS for p in parts) or len(set(parts)) != len(parts):
        return None
    return "+".join(m for m in MODS if m in parts)


def _colour(value):
    h = str(value or "").lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return None


def parse_keys(spec, categories, problems, where):
    """{combo: {category: "keys"}} -> {combo: {key index: category}}. Anything wrong is skipped and reported."""
    out = {}
    if not isinstance(spec, dict):
        problems.append(f"{where}: keys must be a list of combos like {{\"ctrl\": {{\"edit\": \"C V\"}}}}")
        return out
    for combo_text, cats in spec.items():
        combo = _combo(combo_text)
        if combo is None:
            problems.append(f"{where}: '{combo_text}' is not a combo of ctrl, alt, shift, win (ignored)")
            continue
        if not isinstance(cats, dict):
            problems.append(f"{where}.{combo_text}: expected categories like \"edit\": \"C V\" (ignored)")
            continue
        table = out.setdefault(combo, {})
        for cat, keys in cats.items():
            if cat not in categories:
                problems.append(f"{where}.{combo_text}: unknown category '{cat}' (known: {', '.join(categories)})")
                continue
            tokens = keys.split() if isinstance(keys, str) else keys if isinstance(keys, list) else []
            for token in tokens:
                name = key_name(token)
                if name is None:
                    problems.append(f"{where}.{combo_text}: unknown key '{token}' (ignored)")
                else:
                    table[BY_NAME[name]] = cat
    return out


class Shortcuts:
    """The maps in force: the generic one, the programs, and the category colours."""

    def __init__(self, generic, apps, categories, problems=(), path=None):
        self.generic, self.apps, self.categories = generic, apps, categories
        self.problems, self.path = list(problems)[:20], path
        self.by_exe = {exe.lower(): app_id for app_id, a in apps.items() for exe in a["exes"]}

    def app_for(self, exe):
        """The program record for a focused program's file name, or None."""
        app_id = self.by_exe.get(str(exe or "").lower())
        return dict(self.apps[app_id], id=app_id) if app_id else None

    def lit(self, app, combo, own_only=False):
        """{key index: category} for a combo: the generic shortcuts, with the focused program's on top."""
        table = {} if own_only else dict(self.generic.get(combo, {}))
        if app:
            table.update(app["combos"].get(combo, {}))
        return table

    def colour(self, category):
        return self.categories.get(category, faces.WHITE)


def builtin():
    problems = []
    categories = dict(CATEGORIES)
    generic = parse_keys(GENERIC, categories, problems, "generic")
    apps = {i: {"name": a["name"], "exes": list(a["exes"]), "combos": parse_keys(a["keys"], categories, problems, i)}
            for i, a in APPS.items()}
    assert not problems, problems          # the built-in maps are checked by the tests too
    return generic, apps, categories


def load(path=None):
    """The built-in maps with the user's shortcuts.json laid over them. A broken file leaves the built-ins and says why."""
    generic, apps, categories = builtin()
    problems = []
    path = Path(path) if path else None
    if path and path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(user, dict):
                raise ValueError("the file must hold one JSON object")
        except (OSError, ValueError) as e:
            problems.append(f"shortcuts.json could not be read ({e}); the built-in maps are used")
            user = {}
        for name, value in (user.get("categories") or {}).items() if isinstance(user.get("categories"), dict) else []:
            colour = _colour(value)
            if colour:
                categories[str(name)] = colour
            else:
                problems.append(f"categories.{name}: '{value}' is not a colour like #ff6018 (ignored)")
        if isinstance(user.get("generic"), dict):
            generic.update(parse_keys(user["generic"], categories, problems, "generic"))
        user_apps = user.get("apps") if isinstance(user.get("apps"), dict) else {}
        for app_id, a in user_apps.items():
            exes = [str(e).lower() for e in (a.get("exes") or [])] if isinstance(a, dict) else []
            if not exes:
                problems.append(f"apps.{app_id}: needs \"exes\": [\"program.exe\"] (ignored)")
                continue
            apps[str(app_id)] = {"name": str(a.get("name") or app_id), "exes": exes,
                                 "combos": parse_keys(a.get("keys"), categories, problems, f"apps.{app_id}")}
    return Shortcuts(generic, apps, categories, problems, path)


class Library:
    """The current Shortcuts, reloaded when shortcuts.json changes (looked at every few seconds)."""

    def __init__(self, path, clock=time.monotonic):
        self.path, self.clock = Path(path), clock
        self.checked, self.stamp, self.value = -RELOAD_EVERY, None, None

    def current(self):
        now = self.clock()
        if self.value is None or now - self.checked >= RELOAD_EVERY:
            self.checked = now
            try:
                stamp = (self.path.stat().st_mtime_ns, self.path.stat().st_size)
            except OSError:
                stamp = None
            if self.value is None or stamp != self.stamp:
                self.stamp, self.value = stamp, load(self.path)
        return self.value

    def write_example(self):
        """Create shortcuts.json with an example if there is none. Returns (path, created)."""
        if self.path.exists():
            return self.path, False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(EXAMPLE, indent=2), encoding="utf-8")
        self.checked = -RELOAD_EVERY
        return self.path, True


def overlay(base, mods, app, sc, cfg):
    """The key colours with the smart lights applied, or `base` itself when there is nothing to add.

    Holding modifiers: the base goes nearly dark and the keys that have a shortcut with that combo light in their
    category's colour; the held modifier keys glow white. With no modifier held, and only if asked for, the focused
    program's own Ctrl shortcuts glow softly over a slightly dimmed base (never over the keyboard's own lighting)."""
    if cfg.get("modifiers", True) and mods:
        held = {BY_NAME[n] for m in mods for n in HELD_KEYS[m]}
        lit = {i: c for i, c in sc.lit(app, combo_name(mods)).items() if i not in held}
        if not lit:
            return base
        base = base or {}
        out = {idx: faces.dim(base.get(idx, faces.OFF), DARK) for _, idx, _, _ in KEYS}
        for idx, cat in lit.items():
            out[idx] = sc.colour(cat)
        for idx in held:
            out[idx] = faces.WHITE
        return out
    if cfg.get("program_hint") and app and not mods and base is not None:
        lit = sc.lit(app, "ctrl", own_only=True)
        if lit:
            out = {idx: faces.dim(c, HINT_BASE) for idx, c in base.items()}
            for idx, cat in lit.items():
                out[idx] = faces.dim(sc.colour(cat), HINT_KEYS)
            return out
    return base


def describe(sc, exe, mods, active):
    """What the panel shows: the focused program, the held modifiers, the programs with maps, and any problems."""
    app = sc.app_for(exe)
    return {"exe": exe, "app": app["name"] if app else "", "mods": sorted(mods, key=MODS.index), "active": active,
            "apps": [{"name": a["name"], "exes": a["exes"]} for a in sc.apps.values()], "problems": sc.problems,
            "file": str(sc.path) if sc.path else ""}
