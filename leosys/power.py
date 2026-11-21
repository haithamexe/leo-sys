"""The master off switch: one marker file in the data folder, readable by every process without the service.

While the file exists leo-sys holds nothing. The service lets go of the keyboard (the link is closed, so the Leobog app
can have it), stops every watcher, and refuses everything from Claude's tools, the hooks and scripts. The hooks and the
MCP server will not start the service again either (client.py). If Windows' start-up or the panel's icon starts it, it
runs idle: only the panel (to switch back on) is there, and the keyboard is never opened. Only the panel, or
`leo-sys on`, takes the file away again.

It is a file, not a setting, so a hook (a fresh process on every event) can check it in microseconds, and so it survives
a restart of the service, of Windows, and an update of the app.
"""
from pathlib import Path

from .config import DATA_DIR

MESSAGE = "leo-sys is switched off (the master switch in its panel, or `leo-sys on`)"
ASIDE_MESSAGE = "leo-sys has stepped aside because the Leobog app is open (it comes back by itself when that closes)"


class Switch:
    def __init__(self, path=None):
        self.path = Path(path) if path else DATA_DIR / "off"

    def is_off(self):
        try:
            return self.path.exists()
        except OSError:
            return False

    def set_off(self, off):
        """Switch off (True) or on (False). Returns whether it is off now."""
        if off:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("off\n", encoding="utf-8")
        else:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        return self.is_off()
