"""The link to the keyboard: one object the engine's worker thread owns. A pretend one for the demo and tests."""


class Keyboard:
    """The real AMG65 over USB HID. Every method raises on a lost link; the engine reconnects."""

    name = "LEOBOG AMG65"

    def __init__(self):
        self.cmd = self.bulk = None

    def connect(self):
        from .hid import open_channels
        self.cmd, self.bulk = open_channels()

    def matrix(self, pixels):
        from . import matrix
        matrix.send_frame(self.cmd, pixels)

    def keys(self, colours):
        from . import keys
        keys.send(self.cmd, colours)

    def close(self):
        for ch in (self.cmd, self.bulk):
            try:
                if ch:
                    ch.close()
            except Exception:
                pass
        self.cmd = self.bulk = None


class DemoKeyboard:
    """Accepts everything and remembers the last of each, so the panel and the tests have something to look at."""

    name = "pretend keyboard"

    def __init__(self):
        self.frames = self.key_sets = 0
        self.last_matrix = self.last_keys = None

    def connect(self):
        pass

    def matrix(self, pixels):
        self.frames += 1
        self.last_matrix = pixels

    def keys(self, colours):
        self.key_sets += 1
        self.last_keys = colours

    def close(self):
        pass


def official_app_running():
    """Is the Leobog driver app open? It writes to the same channels, so the panel warns about it."""
    import os
    import subprocess
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DeviceDriver.exe", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "DeviceDriver.exe" in out
