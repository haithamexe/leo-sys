"""Persistent settings (data/settings.json). Unknown keys are dropped, wrong types are ignored."""
import copy
import json
import os
import threading
from pathlib import Path

from . import faces, keyfx
from .config import DATA_DIR, LEGACY_DATA_DIR

TOOLS = ["set_face", "show_text", "draw_pixels", "set_keys", "now_playing", "media_control"]


def defaults():
    return {
        "brightness": 100,          # scales every colour we send (the keyboard's own brightness is left alone)
        "colour_gamma": 2.2,        # colour correction for the LEDs (1 = off); makes orange orange, not pale yellow
        "autostart": False,
        "pinned": False,            # the Pin switch survives a restart
        # What Claude (the MCP tools and the status hooks) is allowed to do.
        "claude": {
            "enabled": True,
            "hooks": True,
            "tools": {t: True for t in TOOLS},
            "moods": {m: True for m in faces.MOODS},
            "override_pin": False,
            "min_interval_s": 1.0,
            "max_text_chars": 40,
            "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
        },
        # How the live status looks.
        "scene": {
            "show_tool": True,          # say EDITING, TESTING... instead of WORKING
            "session_dots": True,       # one dot per open session at the right edge
            "reactions": True,          # brief error flash after a failed tool
            "hold_seconds": 30,         # a face or text you (or Claude's tools) pick stays this long
            "done_seconds": 60,         # how long DONE stays before the matrix goes back to the keyboard
            # When Claude's status takes the matrix and keys from your apps: "always", "alerts" (only when it
            # needs you, or a tool failed) or "never" (your apps always; faces Claude picks with its tools still show).
            # Picking the Claude app (or putting it in the rotation) makes it "always".
            "claude_takes_over": "alerts",
        },
        # What shows when Claude is quiet. Claude's live status (and alerts) always come first.
        "apps": {
            "matrix": "off",            # off | rotation | any key of apps.MATRIX_APPS
            "rotation": [{"app": "clock", "seconds": 20}, {"app": "claude", "seconds": 8}],
            "clock": {"24h": True, "seconds": True, "date": True, "colour": "#ff6018"},
            "message": {"text": "", "colour": "#ff6018"},
            # PC monitor apps. The ping app pings this host every 2 seconds, only while it is shown or in
            # the rotation. Speeds in bits per second (like speed tests), or bytes.
            "monitor": {"ping_host": "1.1.1.1", "net_bits": True},
            "media": {"colour": "#00c8ff"},     # the Now playing text
            # Weather and prayer times. Each has its own switch; a switched-off one is skipped. Weather asks
            # Open-Meteo over the internet (opt-in, coordinates only); prayer times are worked out on this PC.
            "city": {"name": "Baghdad", "lat": 33.32, "lon": 44.37},
            "weather": {"enabled": False, "fahrenheit": False},
            # Typing speed and the key heatmap: COUNTS ONLY (see typingmeter.py), in memory, wiped when this is switched off.
            "typing": {"enabled": False},
            "prayer": {"enabled": False, "method": "mwl", "asr": "standard",
                       "show": {"fajr": True, "dhuhr": True, "asr": True, "maghrib": True, "isha": True},
                       "offsets": {"fajr": 0, "dhuhr": 0, "asr": 0, "maghrib": 0, "isha": 0}},
        },
        "keys": {"enabled": True, "moods": {m: True for m in keyfx.KEY_MOODS},
                 "base": "own",         # own | solid | breathe | rainbow (see apps.KEY_BASES)
                 "colour": "#ff6018",
                 # Signs drawn over the keys: Caps Lock white; the volume on the number row for 2 seconds
                 # after it changes (red when muted); a red key while the microphone is muted; and, if asked,
                 # a pulsing key while a program is using the microphone (orange) or the camera (red).
                 "indicators": {"caps": True, "volume": True, "mic": True, "mic_key": "M",
                                "mic_use": False, "mic_use_key": "Home", "cam_use": False, "cam_key": "Backspace"},
                 # Smart keys: hold Ctrl, Alt, Shift or Win and only the keys that do something with it glow
                 # (see smartkeys.py). program_hint: the focused program's own Ctrl shortcuts glow softly.
                 "smart": {"enabled": True, "modifiers": True, "program_hint": False},
                 # The Screen colours base (screen.py): edges | mirror; saturation and gain make dull screens vivid;
                 # smooth (0 to 0.9) calms flicker; fps is how often the screen is read. balance: percent of each
                 # channel (100 = untouched) to correct a tint of the LEDs. Saturation 1 and gain 1 are the most faithful.
                 "screen": {"mode": "edges", "saturation": 1.5, "gain": 1.5, "smooth": 0.5, "fps": 10,
                            "balance": {"r": 100, "g": 100, "b": 100}}},
        # Modes (modes.py): a few settings switched together. `previous` remembers what they were, to switch back.
        "modes": {"previous": ""},
        # Alerts: when a program flashes its taskbar button (Discord, WhatsApp, a browser tab...), the keys flash in its
        # colour and the matrix shows its name. Off by default. `cooldown_s`: one alert per program per this long.
        # ignore / only: program file names separated by commas (only: empty means every program).
        "alerts": {"taskbar": {"enabled": False, "seconds": 6, "cooldown_s": 60, "ignore": "", "only": ""}},
        # Scripts and programs on this PC can show text or colours with `leo-sys push ...`. Claude's own permissions (the
        # "claude" block) do not apply to it, and anything that can run commands here, Claude Code included, can use it,
        # so it starts switched off. This switch turns every push off.
        "push": {"enabled": False, "seconds": 10},
        # The matrix album is played live (no memory wear). fit: contain | cover | stretch.
        "albums": {"matrix": {"folder": "", "fit": "stretch", "seconds": 8, "shuffle": False}},
    }


def _merge(base, over):
    """Overlay `over` on `base`, keeping base's shape and types."""
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for k, bv in base.items():
        if k not in over:
            continue
        ov = over[k]
        if isinstance(bv, dict):
            out[k] = _merge(bv, ov)
        elif isinstance(bv, bool):
            if isinstance(ov, bool):
                out[k] = ov
        elif isinstance(bv, (int, float)):
            if isinstance(ov, (int, float)) and not isinstance(ov, bool) and abs(ov) <= 1e6:
                out[k] = ov
        elif isinstance(bv, str):
            if isinstance(ov, str):
                out[k] = ov
        elif isinstance(bv, list):
            if isinstance(ov, list):
                out[k] = ov
    return out


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else DATA_DIR / "settings.json"
        self.lock = threading.RLock()
        self.data = defaults()
        sources = [self.path]
        if not path and not os.environ.get("LEO_DATA"):
            sources.append(LEGACY_DATA_DIR / "settings.json")  # first run after the data folder moved
        for source in sources:
            try:
                self.data = _merge(defaults(), json.loads(source.read_text(encoding="utf-8")))
                break
            except (OSError, ValueError):
                continue

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def patch(self, over):
        with self.lock:
            self.data = _merge(self.data, over)
            self.save()
            return copy.deepcopy(self.data)

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, indent=2))
            os.replace(tmp, self.path)
