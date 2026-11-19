"""Everything drawn on the 63 x 5 matrix: the Claude critter, a 3 x 5 font, the status faces, scrolling text.

A frame is HEIGHT rows of WIDTH (r, g, b) tuples. Faces are functions of time, so animations need no stored
frame lists: the engine asks for `face(mood, t)` about ten times a second. Colours are fully saturated on purpose:
LEDs wash pale colours out.
"""
import math

WIDTH, HEIGHT = 63, 5
OFF = (0, 0, 0)
ORANGE = (255, 96, 24)      # Claude's colour, pushed to full saturation for LEDs
AMBER = (255, 170, 0)
YELLOW = (255, 230, 0)
GREEN = (0, 255, 60)
RED = (255, 0, 0)
BLUE = (0, 120, 255)
WHITE = (255, 255, 255)

MOODS = ["idle", "thinking", "working", "attention", "done", "error", "sleep", "splash"]
LABELS = {"idle": "IDLE", "thinking": "THINKING", "working": "WORKING", "attention": "NEEDS YOU", "done": "DONE",
          "error": "ERROR", "sleep": "SLEEP", "splash": "LEO-SYS"}
MOOD_COLOURS = {"idle": ORANGE, "thinking": AMBER, "working": ORANGE, "attention": YELLOW, "done": GREEN,
                "error": RED, "sleep": (90, 34, 8), "splash": ORANGE}
# what the working face says for each kind of tool (see toolinfo.tool_kind)
KIND_LABELS = {"edit": "EDITING", "write": "WRITING", "read": "READING", "search": "SEARCHING", "web": "WEB",
               "bash": "RUNNING", "test": "TESTING", "git": "GIT", "agent": "AGENTS"}

FONT = {
    "A": ["###", "#.#", "###", "#.#", "#.#"], "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": ["###", "#..", "#..", "#..", "###"], "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "E": ["###", "#..", "##.", "#..", "###"], "F": ["###", "#..", "##.", "#..", "#.."],
    "G": ["###", "#..", "#.#", "#.#", "###"], "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"], "J": ["..#", "..#", "..#", "#.#", "###"],
    "K": ["#.#", "#.#", "##.", "#.#", "#.#"], "L": ["#..", "#..", "#..", "#..", "###"],
    "M": ["#.#", "###", "###", "#.#", "#.#"], "N": ["##.", "#.#", "#.#", "#.#", "#.#"],
    "O": ["###", "#.#", "#.#", "#.#", "###"], "P": ["###", "#.#", "###", "#..", "#.."],
    "Q": ["###", "#.#", "#.#", "###", "..#"], "R": ["##.", "#.#", "##.", "#.#", "#.#"],
    "S": ["###", "#..", "###", "..#", "###"], "T": ["###", ".#.", ".#.", ".#.", ".#."],
    "U": ["#.#", "#.#", "#.#", "#.#", "###"], "V": ["#.#", "#.#", "#.#", "#.#", ".#."],
    "W": ["#.#", "#.#", "###", "###", "#.#"], "X": ["#.#", "#.#", ".#.", "#.#", "#.#"],
    "Y": ["#.#", "#.#", "###", ".#.", ".#."], "Z": ["###", "..#", ".#.", "#..", "###"],
    "0": ["###", "#.#", "#.#", "#.#", "###"], "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"], "3": ["###", "..#", ".##", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"], "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"], "7": ["###", "..#", ".#.", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"], "9": ["###", "#.#", "###", "..#", "###"],
    " ": ["...", "...", "...", "...", "..."], ".": ["...", "...", "...", "...", ".#."],
    "!": [".#.", ".#.", ".#.", "...", ".#."], "?": ["###", "..#", ".##", "...", ".#."],
    ":": ["...", ".#.", "...", ".#.", "..."], "-": ["...", "...", "###", "...", "..."],
    "+": ["...", ".#.", "###", ".#.", "..."], "/": ["..#", "..#", ".#.", "#..", "#.."],
    "_": ["...", "...", "...", "...", "###"], ",": ["...", "...", "...", ".#.", "#.."],
    "%": ["#.#", "..#", ".#.", "#..", "#.#"], "'": [".#.", ".#.", "...", "...", "..."],
    "(": [".#.", "#..", "#..", "#..", ".#."], ")": [".#.", "..#", "..#", "..#", ".#."],
    "=": ["...", "###", "...", "###", "..."], ">": ["#..", ".#.", "..#", ".#.", "#.."],
    "<": ["..#", ".#.", "#..", ".#.", "..#"], "#": ["#.#", "###", "#.#", "###", "#.#"],
    "↓": [".#.", ".#.", ".#.", "###", ".#."], "↑": [".#.", "###", ".#.", ".#.", ".#."],
    "▶": ["#..", "##.", "###", "##.", "#.."], "‖": ["#.#", "#.#", "#.#", "#.#", "#.#"],
    "♪": [".##", ".#.", ".#.", "##.", "##."], "°": ["##.", "##.", "...", "...", "..."],
}
CHARS = "".join(sorted(FONT))

# The critter, 7 x 5. 'o' is body, '.' is off, 'e' is an eye (drawn dark, so the face reads on any background).
CRITTER = {
    "open":   [".ooooo.", "oo.o.oo", "ooooooo", ".ooooo.", ".o.o.o."],
    "blink":  [".ooooo.", "ooooooo", "ooooooo", ".ooooo.", ".o.o.o."],
    "up":     [".o.o.o.", "ooooooo", "ooooooo", ".ooooo.", ".o.o.o."],
    "left":   [".ooooo.", "o.o.ooo", "ooooooo", ".ooooo.", ".o.o.o."],
    "right":  [".ooooo.", "ooo.o.o", "ooooooo", ".ooooo.", ".o.o.o."],
    "wave":   ["o.ooo.o", "oo.o.oo", ".ooooo.", ".ooooo.", ".o.o.o."],   # arms up
    "cross":  ["o.ooo.o", ".o.o.o.", "o.ooo.o", ".ooooo.", ".o.o.o."],
}


NAMED_COLOURS = {"red": (255, 0, 0), "green": (0, 255, 60), "blue": (0, 120, 255), "orange": ORANGE, "yellow": YELLOW,
                 "white": WHITE, "purple": (190, 0, 255), "cyan": (0, 220, 255), "pink": (255, 60, 160), "amber": AMBER,
                 "off": OFF, "black": OFF}


def bar_frame(percent, colour):
    """A progress bar: the percentage at the left, then a bar three dots tall that fills from the left."""
    pct = max(0.0, min(100.0, float(percent)))
    f = [[OFF] * WIDTH for _ in range(HEIGHT)]
    end = 0
    for ch in f"{round(pct)}%":
        for gy, row in enumerate(FONT.get(ch, FONT["?"])):
            for gx, bit in enumerate(row):
                if bit == "#":
                    f[gy][end + gx] = colour
        end += 4
    start = max(end + 2, 16)
    width = WIDTH - start
    filled = round(pct / 100.0 * width)
    for y in (1, 2, 3):
        for x in range(width):
            f[y][start + x] = colour if x < filled else tuple(int(v * 0.12) for v in colour)
    return f


def blank():
    return [[OFF] * WIDTH for _ in range(HEIGHT)]


def dim(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def put(frame, x, y, c):
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        frame[y][x] = c


def text_width(text):
    return max(0, 4 * len(text) - 1)


def draw_text(frame, text, x, colour, y=0):
    """Draw text at column x (may be negative while scrolling). Unknown characters become '?'."""
    for ch in str(text).upper():
        glyph = FONT.get(ch, FONT["?"])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "#":
                    put(frame, x + gx, y + gy, colour)
        x += 4
    return x


def draw_critter(frame, pose, x, colour):
    for y, row in enumerate(CRITTER[pose]):
        for dx, bit in enumerate(row):
            if bit == "o":
                put(frame, x + dx, y, colour)


def _pose(mood, t):
    if mood in ("sleep", "done"):
        return "blink"
    if mood == "thinking":
        return "up"
    if mood == "working":
        return "left" if int(t * 2) % 2 == 0 else "right"
    if mood == "attention":
        return "wave" if int(t * 3) % 2 == 0 else "open"
    if mood == "error":
        return "cross"
    # idle and splash: open eyes, a blink every few seconds
    return "blink" if (t % 4.0) < 0.15 else "open"


def face(mood, t, label=None, sessions=None):
    """One frame of a status face at time t (seconds). `sessions` is a list of colours for the dots at the right."""
    mood = mood if mood in MOODS else "idle"
    f = blank()
    colour = MOOD_COLOURS[mood]
    body = ORANGE if mood not in ("error", "sleep") else colour
    if mood == "attention" and int(t * 3) % 2:
        body = YELLOW
    draw_critter(f, _pose(mood, t), 0, body)
    word = (label or LABELS[mood]).upper()[:12]
    text_colour = colour
    if mood == "attention" and int(t * 2) % 2:
        text_colour = dim(colour, 0.25)
    end = draw_text(f, word, 9, text_colour)
    _decorate(f, mood, t, end + 1)
    if sessions:
        for i, c in enumerate(sessions[:HEIGHT]):
            put(f, WIDTH - 1, i, c)
    return f


def _decorate(f, mood, t, x0):
    """The small animated part after the word."""
    room = WIDTH - 3 - x0                      # keep clear of the session dots
    if room < 3:
        return
    if mood == "thinking":
        for i in range(int(t * 2.5) % 4):      # dots growing, then starting over
            put(f, x0 + 1 + 2 * i, 4, AMBER)
    elif mood == "working":                    # a scanner bouncing along a dim track
        span = min(room - 1, 18)
        for i in range(span):
            put(f, x0 + 1 + i, 2, dim(ORANGE, 0.18))
        pos = int((math.sin(t * 3.2) + 1) / 2 * (span - 3))
        for i in range(3):
            put(f, x0 + 1 + pos + i, 2, ORANGE)
    elif mood == "done":                       # sparkles popping in and out
        for i in range(min(room // 3, 5)):
            phase = (t * 1.7 + i * 0.37) % 1.0
            if phase < 0.5:
                put(f, x0 + 1 + i * 3, (i * 2 + int(t * 2)) % HEIGHT, YELLOW if i % 2 else GREEN)
    elif mood == "sleep":                      # a small z drifting up
        y = 4 - int(t * 1.2) % 5
        put(f, x0 + 2, y, dim(BLUE, 0.6))
    elif mood == "error" and int(t * 6) % 3 == 0:  # a glitch flicker
        for i in range(0, min(room, 12), 2):
            put(f, x0 + 1 + i, (i * 3 + int(t * 9)) % HEIGHT, dim(RED, 0.5))


def text_frame(text, t, colour=ORANGE, speed=12.0):
    """Static if the text fits, otherwise scrolling right to left at `speed` columns a second."""
    f = blank()
    w = text_width(text)
    if w <= WIDTH:
        draw_text(f, text, (WIDTH - w) // 2, colour)
        return f
    cycle = w + WIDTH
    x = WIDTH - int(t * speed) % cycle
    draw_text(f, text, x, colour)
    return f


def scale(frame, brightness):
    """Apply the brightness setting (0-100) to a frame."""
    if brightness >= 100:
        return frame
    k = max(0, brightness) / 100.0
    return [[dim(c, k) for c in row] for row in frame]


_luts = {}


def led_lut(gamma):
    """LEDs are linear, so a colour made for a screen looks pale on them (orange turns yellow). Raising each channel
    to `gamma` (about 2.2) gives the deep colours you see on screen. A lit channel never drops to fully off."""
    g = max(1.0, min(3.0, float(gamma)))
    if g not in _luts:
        _luts[g] = [0 if v == 0 else max(1, round((v / 255) ** g * 255)) for v in range(256)]
    return _luts[g]


def led_colour(c, lut):
    return (lut[c[0]], lut[c[1]], lut[c[2]])


def led_frame(frame, lut):
    return [[led_colour(c, lut) for c in row] for row in frame]


def to_hex(frame):
    return "".join("%02x%02x%02x" % c for row in frame for c in row)
