"""The matrix album: a folder of pictures and GIFs, played live on the LED matrix.

Every picture is scaled down to 63 x 5 once, in a background thread, and played from memory. A still shows for
`seconds`; a GIF plays at its own speed and repeats until it has lasted at least `seconds`. The matrix is streamed
live and nothing is stored on the keyboard.

leo-sys never writes to the keyboard's LCD (the user does that with the Leobog app), so there is no LCD album.
"""
import bisect
import hashlib
import math
import os
import random
import re
import threading
from pathlib import Path

from . import wic
from .faces import HEIGHT, WIDTH

MAX_IMAGES = 500
MATRIX_MAX_FRAMES = 5000
LISTING_TTL = 5.0            # how long a folder listing is trusted before looking again


class AlbumError(Exception):
    pass


def _natural(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.lower())]


def scan(folder):
    """(picture paths in name order, signature of names, sizes and dates). Raises AlbumError."""
    text = str(folder or "").strip()
    if not text:
        raise AlbumError("no folder chosen")
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    try:
        entries = [e for e in os.scandir(path) if e.name.lower().endswith(wic.EXTENSIONS) and e.is_file()]
    except OSError:
        raise AlbumError("that folder cannot be opened")
    entries.sort(key=lambda e: _natural(e.name))
    files, parts = [], []
    for e in entries[:MAX_IMAGES]:
        try:
            st = e.stat()
        except OSError:
            continue
        files.append(e.path)
        parts.append(f"{e.name}|{st.st_size}|{int(st.st_mtime)}")
    return files, hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()


class _Listings:
    """Folder listings, remembered for a few seconds so the 10-times-a-second loop does not hit the disk."""

    def __init__(self):
        self.cache = {}

    def look(self, folder, now):
        hit = self.cache.get(folder)
        if hit and now - hit[0] < LISTING_TTL:
            result = hit[1]
        else:
            try:
                result = scan(folder)
            except AlbumError as e:
                result = e
            self.cache[folder] = (now, result)
        if isinstance(result, AlbumError):
            raise result
        return result


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _rgb_bytes(bgra):
    out = bytearray(WIDTH * HEIGHT * 3)
    out[0::3], out[1::3], out[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return bytes(out)


def rows_of(rgb):
    """The 5 x 63 colour grid of one stored matrix frame."""
    return [[(rgb[(y * WIDTH + x) * 3], rgb[(y * WIDTH + x) * 3 + 1], rgb[(y * WIDTH + x) * 3 + 2])
             for x in range(WIDTH)] for y in range(HEIGHT)]


def build_matrix(files, cfg):
    seconds = _clamp(cfg.get("seconds"), 1, 3600, 8)
    items, skipped = [], 0
    for path in files:
        try:
            frames = wic.decode(path, WIDTH, HEIGHT, cfg.get("fit", "stretch"))
        except (wic.ImageError, OSError, ValueError):
            skipped += 1
            continue
        if len(frames) == 1 and frames[0][1] is None:
            items.append([(_rgb_bytes(frames[0][0]), seconds)])
        else:
            anim = [(_rgb_bytes(b), max(0.04, ms / 1000.0)) for b, ms in frames]
            length = sum(d for _, d in anim)
            items.append(anim * max(1, math.ceil(seconds / length)))
    if cfg.get("shuffle"):
        random.shuffle(items)
    flat = [f for item in items for f in item][:MATRIX_MAX_FRAMES]
    starts, t = [], 0.0
    for _, d in flat:
        starts.append(t)
        t += d
    if not flat:
        raise AlbumError("no picture in the folder could be read")
    return {"frames": flat, "starts": starts, "total": t, "images": len(items), "skipped": skipped}


def rows_of_at(data, now):
    """The 5 x 63 grid to show at time `now` for a built matrix album."""
    t = now % data["total"]
    i = max(0, bisect.bisect_right(data["starts"], t) - 1)
    return rows_of(data["frames"][i][0])


class MatrixAlbum:
    def __init__(self, listings):
        self.listings = listings
        self.lock = threading.Lock()
        self.key = self.building = None
        self.data = None
        self.error = ""

    def get(self, cfg, now):
        """{"state": ok | loading | empty | error, ...}; a build starts in the background when the folder or the
        options changed, and the old album keeps playing until the new one is ready."""
        try:
            files, sig = self.listings.look(cfg.get("folder", ""), now)
        except AlbumError as e:
            return {"state": "error", "error": str(e)}
        if not files:
            return {"state": "empty"}
        key = (sig, cfg.get("fit"), _clamp(cfg.get("seconds"), 1, 3600, 8), bool(cfg.get("shuffle")))
        with self.lock:
            if key != self.key and key != self.building:
                self.building = key
                threading.Thread(target=self._build, args=(key, files, dict(cfg)), daemon=True).start()
            data, err = self.data, self.error
        if data is None:
            return {"state": "error", "error": err} if err else {"state": "loading"}
        return dict(data, state="ok")

    def _build(self, key, files, cfg):
        try:
            data, err = build_matrix(files, cfg), ""
        except Exception as e:      # a broken picture must not take the service down
            data, err = None, str(e) or type(e).__name__
        with self.lock:
            self.key, self.building = key, None
            self.data, self.error = data, err


class Albums:
    def __init__(self):
        self.listings = _Listings()
        self.matrix = MatrixAlbum(self.listings)

    def peek(self, folder, now):
        """How many pictures a folder holds (for the panel), without building anything."""
        try:
            files, _ = self.listings.look(folder, now)
            return {"images": len(files), "error": ""}
        except AlbumError as e:
            return {"images": None, "error": str(e)}
