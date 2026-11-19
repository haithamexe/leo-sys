"""Screen colours for the key lights: a tiny picture of the screen, read with Windows' own GDI (ctypes, nothing to install).

Privacy, in plain words: the screen is shrunk by Windows to a 32 x 18 grid of colours, about 10 times a second, and only
that grid is kept in memory (the latest one, nothing else). No picture is saved, nothing is sent anywhere, and the screen
is not read at all unless the key base "Screen colours" is picked. Protected video may read as black.

How the colours are made accurate: the first shrink samples the screen densely (every 7th pixel or so, with layered windows
included); the grid is then averaged over each key's own patch of the screen and smoothed over time in LINEAR light (the way
light really adds up), not in the screen's encoded values, which muddies mixed colours and darkens flashes; a per-channel
white balance can correct an LED tint.

The grid becomes key colours in one of two ways (see key_colours): "edges", where each key takes the colour at the nearest
edge of the screen (like a lamp strip behind a monitor), or "mirror", where the keyboard is a tiny picture of the screen.
Only the main monitor is read.
"""
import colorsys
import ctypes
import threading
import time
from ctypes import wintypes

from .keys import KEYS

GRID_W, GRID_H = 32, 18
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000     # also copy layered (see-through) windows, which a plain copy leaves out
COLORONCOLOR, HALFTONE = 3, 4
IDLE_AFTER = 5.0            # stop reading the screen this long after nothing asked for it
EDGE_BAND = 0.07            # how far in from the screen's edge the "edges" mode samples (0 to 1)
MODES = ("edges", "mirror")


class _BMI(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("w", wintypes.LONG), ("h", wintypes.LONG), ("planes", wintypes.WORD),
                ("bpp", wintypes.WORD), ("comp", wintypes.DWORD), ("img", wintypes.DWORD), ("xp", wintypes.LONG),
                ("yp", wintypes.LONG), ("used", wintypes.DWORD), ("imp", wintypes.DWORD)]


_DPI = []


def _real_pixels(u):
    """Count real screen pixels, not the scaled ones a high-DPI screen reports to programs that do not say they cope."""
    if _DPI:
        return
    _DPI.append(True)
    try:
        u.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


class Grabber:
    """Holds the Windows drawing objects for shrinking the screen, so each reading costs about 14 ms."""

    def __init__(self):
        H, P = ctypes.c_void_p, ctypes.POINTER
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u, self.g = u, g
        u.GetDC.restype, u.GetDC.argtypes = H, [H]
        u.ReleaseDC.argtypes = [H, H]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = H, [H]
        g.CreateCompatibleBitmap.restype, g.CreateCompatibleBitmap.argtypes = H, [H, ctypes.c_int, ctypes.c_int]
        g.SelectObject.restype, g.SelectObject.argtypes = H, [H, H]
        g.SetStretchBltMode.argtypes = [H, ctypes.c_int]
        g.SetBrushOrgEx.argtypes = [H, ctypes.c_int, ctypes.c_int, H]
        g.StretchBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        g.GetDIBits.argtypes = [H, H, wintypes.UINT, wintypes.UINT, H, P(_BMI), wintypes.UINT]
        g.DeleteDC.argtypes = [H]
        g.DeleteObject.argtypes = [H]
        _real_pixels(u)
        self.size = (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
        self.screen = u.GetDC(None)
        self.mid = (GRID_W * 8, GRID_H * 8)       # a fast shrink first (every ~7th pixel), then a proper averaging one
        self.dc1, self.bmp1, self.old1 = self._surface(*self.mid, COLORONCOLOR)
        self.dc2, self.bmp2, self.old2 = self._surface(GRID_W, GRID_H, HALFTONE)
        self.stride = (GRID_W * 3 + 3) // 4 * 4
        self.buf = (ctypes.c_ubyte * (self.stride * GRID_H))()
        self.bmi = _BMI(ctypes.sizeof(_BMI), GRID_W, -GRID_H, 1, 24, 0)

    def _surface(self, w, h, mode):
        g = self.g
        dc = g.CreateCompatibleDC(self.screen)
        bmp = g.CreateCompatibleBitmap(self.screen, w, h)
        old = g.SelectObject(dc, bmp)
        g.SetStretchBltMode(dc, mode)
        g.SetBrushOrgEx(dc, 0, 0, None)
        return dc, bmp, old

    def read(self):
        """The screen as 32 x 18 x 3 bytes of RGB. Raises OSError if Windows cannot give it (a locked screen, say)."""
        g, u = self.g, self.u
        if (u.GetSystemMetrics(0), u.GetSystemMetrics(1)) != self.size:
            raise OSError("the screen size changed")
        sw, sh = self.size
        if not g.StretchBlt(self.dc1, 0, 0, self.mid[0], self.mid[1], self.screen, 0, 0, sw, sh, SRCCOPY | CAPTUREBLT):
            raise OSError("Windows would not copy the screen (it may be locked)")
        g.StretchBlt(self.dc2, 0, 0, GRID_W, GRID_H, self.dc1, 0, 0, self.mid[0], self.mid[1], SRCCOPY)
        if not g.GetDIBits(self.dc2, self.bmp2, 0, GRID_H, self.buf, ctypes.byref(self.bmi), 0):
            raise OSError("could not read the shrunk screen")
        raw = bytes(self.buf)
        out = bytearray(GRID_W * GRID_H * 3)
        for y in range(GRID_H):
            row = raw[y * self.stride:y * self.stride + GRID_W * 3]
            base = y * GRID_W * 3
            out[base:base + GRID_W * 3:3] = row[2::3]
            out[base + 1:base + GRID_W * 3:3] = row[1::3]
            out[base + 2:base + GRID_W * 3:3] = row[0::3]
        return bytes(out)

    def close(self):
        g = self.g
        for dc, bmp, old in ((self.dc1, self.bmp1, self.old1), (self.dc2, self.bmp2, self.old2)):
            g.SelectObject(dc, old)
            g.DeleteObject(bmp)
            g.DeleteDC(dc)
        self.u.ReleaseDC(None, self.screen)


# ---- from the grid to key colours -----------------------------------------------------------------------------
# Screens store colours "encoded" (sRGB); light adds up linearly. Mixing in the linear form is what the eye would see.
_LINEAR = [(i / 255.0) / 12.92 if i / 255.0 <= 0.04045 else ((i / 255.0 + 0.055) / 1.055) ** 2.4 for i in range(256)]


def to_srgb(x):
    """A linear light level (0 to 1) as an encoded value, 0 to 255 (not rounded)."""
    x = max(0.0, min(1.0, x))
    return 255.0 * (x * 12.92 if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055)


def sample(grid, u, v, rx=1.2, ry=1.8):
    """The colour at (u, v), both 0 to 1 across the screen: the grid's cells around that point (about the area of a key),
    nearer ones counting more, mixed in linear light."""
    cx, cy = u * (GRID_W - 1), v * (GRID_H - 1)
    total, acc = 0.0, [0.0, 0.0, 0.0]
    for y in range(max(0, int(cy - ry - 0.5)), min(GRID_H - 1, int(cy + ry + 0.5)) + 1):
        wy = 1.0 - abs(y - cy) / (ry + 0.5)
        if wy <= 0:
            continue
        for x in range(max(0, int(cx - rx - 0.5)), min(GRID_W - 1, int(cx + rx + 0.5)) + 1):
            w = wy * (1.0 - abs(x - cx) / (rx + 0.5))
            if w <= 0:
                continue
            k = (y * GRID_W + x) * 3
            total += w
            acc[0] += w * _LINEAR[grid[k]]
            acc[1] += w * _LINEAR[grid[k + 1]]
            acc[2] += w * _LINEAR[grid[k + 2]]
    if total <= 0:                                  # a point far off the grid: the nearest cell
        x, y = max(0, min(GRID_W - 1, round(cx))), max(0, min(GRID_H - 1, round(cy)))
        k = (y * GRID_W + x) * 3
        return [float(grid[k]), float(grid[k + 1]), float(grid[k + 2])]
    return [to_srgb(a / total) for a in acc]


def edge_point(u, v, band=EDGE_BAND):
    """Push a point on the screen to the nearest edge (a thin band just inside it)."""
    d = {"top": v, "bottom": 1 - v, "left": u, "right": 1 - u}     # a tie goes to the first: top, bottom, left, right
    side = min(d, key=d.get)
    if side == "top":
        return u, band
    if side == "bottom":
        return u, 1 - band
    if side == "left":
        return band, v
    return 1 - band, v


def boost(rgb, saturation, gain=1.0):
    """More (or less) colourful, and brighter: screens are mostly dark greys and LEDs wash those out."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    r, g, b = colorsys.hsv_to_rgb(h, max(0.0, min(1.0, s * saturation)), max(0.0, min(1.0, v * gain)))
    return (round(r * 255), round(g * 255), round(b * 255))


def balanced(rgb, balance):
    """White balance: each channel scaled by its percentage (100 leaves it alone), to correct a tint of the LEDs."""
    if not balance:
        return rgb
    return tuple(max(0, min(255, round(c * max(0.0, min(200.0, float(balance[i]))) / 100.0))) for i, c in enumerate(rgb))


def key_colours(grid, mode="edges", saturation=1.5, gain=1.5, balance=None):
    """{light index: (r, g, b)} for every key from a 32 x 18 grid of RGB bytes."""
    rows = max(r for _, _, r, _ in KEYS) or 1
    out = {}
    for _, idx, row, x in KEYS:
        u, v = x / 1000.0, row / rows
        if mode == "edges":
            u, v = edge_point(u, v)
        out[idx] = balanced(boost(tuple(round(c) for c in sample(grid, u, v)), saturation, gain), balance)
    return out


def blend(old, new, smooth):
    """Mix a new grid into the old one, in linear light: smooth 0 is the new grid, 0.9 changes slowly."""
    if old is None or smooth <= 0:
        return new
    keep = max(0.0, min(0.95, smooth))
    return bytes(round(to_srgb(_LINEAR[o] * keep + _LINEAR[n] * (1 - keep))) for o, n in zip(old, new))


# ---- the watcher ----------------------------------------------------------------------------------------------
class ScreenWatch:
    """A thread that reads the screen while something wants it, and keeps only the latest grid."""

    def __init__(self, grabber=Grabber, clock=time.time, threads=True):
        self.grabber_type, self.clock, self.threads = grabber, clock, threads
        self.lock = threading.Lock()
        self.wanted_at = -1e9
        self.fps, self.smooth = 10.0, 0.5
        self.grid, self.error = None, ""
        self.started = False
        self.grabber = None

    def want(self, fps=10.0, smooth=0.5):
        with self.lock:
            self.wanted_at = self.clock()
            self.fps = max(2.0, min(15.0, float(fps)))
            self.smooth = max(0.0, min(0.9, float(smooth)))
            start = self.threads and not self.started
            self.started = self.started or start
        if start:
            threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return {"grid": self.grid, "error": self.error}

    def _step(self):
        if self.grabber is None:
            self.grabber = self.grabber_type()
        grid = self.grabber.read()
        with self.lock:
            self.grid, self.error = blend(self.grid, grid, self.smooth), ""

    def _drop(self):
        if self.grabber is not None:
            try:
                self.grabber.close()
            except Exception:
                pass
            self.grabber = None

    def _run(self):
        while True:
            t0 = self.clock()
            with self.lock:
                active = t0 - self.wanted_at <= IDLE_AFTER
                interval = 1.0 / self.fps
            if active:
                try:
                    self._step()
                except Exception as e:          # a locked screen or a changed size: try again, never crash
                    with self.lock:
                        self.error = str(e) or type(e).__name__
                    self._drop()
                    interval = 1.0
            else:
                with self.lock:
                    self.grid = None            # nothing is kept once nothing asks for it
                self._drop()
                interval = 0.5
            time.sleep(max(0.02, interval - (self.clock() - t0)))


class FakeScreenWatch(ScreenWatch):
    """A moving gradient for the demo and the tests. Never touches Windows."""

    def __init__(self, clock=time.time):
        super().__init__(clock=clock, threads=False)
        self.wanted = 0

    def want(self, fps=10.0, smooth=0.5):
        self.wanted += 1

    def snapshot(self):
        t = self.clock()
        grid = bytearray()
        for y in range(GRID_H):
            for x in range(GRID_W):
                h = (x / GRID_W + t * 0.05) % 1.0
                r, g, b = colorsys.hsv_to_rgb(h, 0.8, 0.4 + 0.6 * y / GRID_H)
                grid += bytes((int(r * 255), int(g * 255), int(b * 255)))
        return {"grid": bytes(grid), "error": ""}
