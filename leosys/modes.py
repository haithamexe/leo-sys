"""Modes: a few settings switched together, with a way back.

A mode is a set of settings. Switching it on remembers what those settings were (in the settings file, as plain text) and
sets them; switching it off puts them back. It only ever changes settings the panel already shows, nothing else.
"""
import json

from . import keyfx, settings as settings_mod

# name -> label, what it does, and the settings it sets ({"apps": {"matrix": "claude"}} style)
MODES = {
    "claude_screen": {
        "label": "Claude and the screen",
        "desc": "The matrix shows Claude's status, and the keys follow the colours of your screen. Claude's own key effects "
                "are switched off while this is on, so the keys stay on the screen.",
        "set": lambda: {"apps": {"matrix": "claude"},
                        "keys": {"enabled": True, "base": "screen", "moods": {m: False for m in keyfx.KEY_MOODS}}},
    },
}


def _leaves(tree, path=()):
    for k, v in tree.items():
        if isinstance(v, dict):
            yield from _leaves(v, path + (k,))
        else:
            yield path + (k,), v


def _get(tree, path):
    for k in path:
        tree = tree[k]
    return tree


def _nest(pairs):
    out = {}
    for path, value in pairs:
        node = out
        for k in path[:-1]:
            node = node.setdefault(k, {})
        node[path[-1]] = value
    return out


def is_on(snapshot, name):
    return all(_get(snapshot, p) == v for p, v in _leaves(MODES[name]["set"]()))


def turn_on(settings, name):
    """Remember the current values and set the mode's. Does nothing if it is already on."""
    snap = settings.snapshot()
    if is_on(snap, name):
        return
    paths = [p for p, _ in _leaves(MODES[name]["set"]())]
    previous = {".".join(p): _get(snap, p) for p in paths}
    settings.patch({**MODES[name]["set"](), "modes": {"previous": json.dumps({"name": name, "values": previous})}})


def turn_off(settings, name):
    """Put back what was there before (or the factory values, if that was never saved)."""
    snap = settings.snapshot()
    paths = [p for p, _ in _leaves(MODES[name]["set"]())]
    saved = {}
    try:
        data = json.loads(snap["modes"]["previous"] or "{}")
        if data.get("name") == name and isinstance(data.get("values"), dict):
            saved = data["values"]
    except ValueError:
        pass
    factory = settings_mod.defaults()
    restored = _nest([(p, saved.get(".".join(p), _get(factory, p))) for p in paths])
    settings.patch({**restored, "modes": {"previous": ""}})
    if is_on(settings.snapshot(), name):               # what was saved is the mode itself: fall back to the factory values
        settings.patch(_nest([(p, _get(factory, p)) for p in paths]))


def state(snapshot):
    return {name: {"label": m["label"], "desc": m["desc"], "on": is_on(snapshot, name)} for name, m in MODES.items()}
