"""Key backlight effects for each status. `colours(mood, t, since)` returns {light index: (r, g, b)}, or None to
hand the keys back to the keyboard's own lighting (the engine simply stops sending)."""
import math

from . import faces
from .keys import KEYS

FLASH_SECONDS = {"done": 3.0, "error": 2.0}   # these play once after the change, then let go
KEY_MOODS = ["thinking", "working", "attention", "done", "error"]


def _all(c):
    return {idx: c for _, idx, _, _ in KEYS}


def colours(mood, t, since=0.0):
    """t: seconds now; since: when this mood started. Returns a colour map or None."""
    age = t - since
    if mood == "thinking":                       # slow orange breathing
        k = 0.25 + 0.75 * (math.sin(t * math.pi) + 1) / 2
        return _all(faces.dim(faces.AMBER, k))
    if mood == "working":                        # an orange band sweeping left to right
        front = (t / 1.6 % 1.0) * 1200 - 100
        out = {}
        for _, idx, _, x in KEYS:
            d = abs(x - front)
            out[idx] = faces.dim(faces.ORANGE, max(0.08, 1 - d / 160))
        return out
    if mood == "attention":                      # flashing, hard to miss
        return _all(faces.YELLOW if int(t * 4) % 2 == 0 else faces.dim(faces.ORANGE, 0.15))
    if mood in FLASH_SECONDS:
        if age >= FLASH_SECONDS[mood]:
            return None
        base = faces.GREEN if mood == "done" else faces.RED
        if mood == "error":
            return _all(base if int(age * 6) % 2 == 0 else faces.OFF)
        return _all(faces.dim(base, 1 - age / FLASH_SECONDS[mood] * 0.8))
    return None


def to_hex(cols):
    """{idx: rgb} -> [{"idx", "hex"}] for the panel's keyboard picture."""
    if not cols:
        return None
    return {str(i): "%02x%02x%02x" % c for i, c in cols.items()}
