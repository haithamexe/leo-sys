"""Local HTTP server: serves the panel and a small JSON API. Bound to 127.0.0.1 only.

Because any web page you visit can send requests to localhost, every request must carry the right Host header, a
custom X-Leo header (which browsers will not send cross-site without a CORS preflight we never answer), and, for
anything that changes settings, the per-run token that only the served panel page contains.
"""
import json
import os
import pkgutil
import secrets
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import apps, faces, keyfx, places, prayer, smartkeys, system
from .config import HOST, PORT  # noqa: F401
from .keys import KEYS

WEB = Path(__file__).resolve().parent / "web"
HTTP_SOURCES = {"mcp", "hook", "gui", "push"}

MOOD_DESC = {
    "idle": "Calm, blinking now and then: nothing happening",
    "thinking": "Eyes up, dots growing: Claude is reasoning",
    "working": "Eyes darting, a scanner moving: running tools. Says EDITING, TESTING... when it knows",
    "attention": "Arms up, flashing: Claude needs you",
    "done": "Eyes closed, sparkles: finished",
    "error": "Crossed out, flickering: a tool failed",
    "sleep": "Dim, a small z drifting: nothing open",
    "splash": "The leo-sys sign",
}
TOOL_DESC = {
    "set_face": "Show a status face on the matrix",
    "show_text": "Scroll a short message across the matrix",
    "draw_pixels": "Draw its own 63 x 5 picture or animation",
    "set_keys": "Colour the key lights",
    "now_playing": "See what song or video is playing, and the volume",
    "media_control": "Play, pause, skip, and change or mute the volume",
}
KEY_DESC = {"thinking": "Slow orange breathing", "working": "An orange wave left to right",
            "attention": "Flashing yellow", "done": "A green fade, once", "error": "A red blink, once"}


def meta():
    return {
        "moods": {m: {"desc": MOOD_DESC.get(m, ""), "label": faces.LABELS[m],
                      "frames": [faces.to_hex(faces.face(m, t / 4)) for t in range(16)]} for m in faces.MOODS},
        "tools": TOOL_DESC, "key_moods": {m: KEY_DESC[m] for m in keyfx.KEY_MOODS},
        "keys": [{"name": n, "idx": i, "row": r, "x": x} for n, i, r, x in KEYS],
        "width": faces.WIDTH, "height": faces.HEIGHT,
        "apps": {k: {"label": v["label"], "desc": v["desc"]} for k, v in apps.MATRIX_APPS.items()},
        "app_samples": apps.samples(),
        "key_bases": {k: {"label": v["label"], "desc": v["desc"]} for k, v in apps.KEY_BASES.items()},
        "cities": {n: {"lat": lat, "lon": lon} for n, (lat, lon) in places.CITIES.items()},
        "prayer_methods": {k: v["name"] for k, v in prayer.METHODS.items()},
        "smart_categories": {k: {"label": smartkeys.LABELS[k], "colour": "#%02x%02x%02x" % c}
                             for k, c in smartkeys.CATEGORIES.items()},
    }


def pick_folder():
    """Open Windows' folder chooser (tkinter, in its own process so it cannot hold up the service) and return the
    path, or "" if the user cancelled."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe")
    code = ("import tkinter, tkinter.filedialog as f; "
            "r = tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True); "
            "print(f.askdirectory(title='Choose a folder of pictures', parent=r) or '')")
    try:
        out = subprocess.run([str(py if py.exists() else exe), "-I", "-X", "utf8", "-c", code], capture_output=True,
                             encoding="utf-8", timeout=600, creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    if out.returncode:
        return {"ok": False, "error": "the folder chooser could not open; type the path instead"}
    return {"ok": True, "path": out.stdout.strip().replace("/", os.sep)}


class _Server(ThreadingHTTPServer):
    """Exclusive bind on Windows, so the port really is the one-service lock."""

    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(engine, host=HOST, port=PORT, on_quit=None):
    token = secrets.token_urlsafe(24)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "leo-sys"
        timeout = 10

        def log_message(self, *a):
            pass

        def _send(self, code, obj, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype or "json" in ctype else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; img-src data:; connect-src 'self'; base-uri 'none'; "
                             "form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _guard(self):
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(403, {"error": "bad host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in allowed_origins:
                self._send(403, {"error": "bad origin"})
                return False
            return True

        def _admin(self):
            ok = secrets.compare_digest(self.headers.get("X-Leo-Token", ""), token)
            if not ok:
                self._send(403, {"error": "admin token required"})
            return ok

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if n < 0 or n > 2_000_000:
                return None
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None

        def do_GET(self):
            if not self._guard():
                return
            path = self.path.split("?")[0]
            if path == "/":
                html = pkgutil.get_data("leosys", "web/index.html").decode("utf-8").replace("__TOKEN__", token)
                return self._send(200, html.encode("utf-8"), "text/html")
            if not self.headers.get("X-Leo") and path.startswith("/api/"):
                return self._send(403, {"error": "missing X-Leo header"})
            if path == "/api/ping":
                return self._send(200, {"ok": True})
            if path == "/api/state":
                return self._send(200, engine.state())
            if path == "/api/meta":
                return self._send(200, meta())
            if path == "/api/claude":   # read-only permission summary, for Claude's own status tool
                st = engine.state()
                return self._send(200, {"permissions": engine.settings.snapshot()["claude"], "pinned": st["pinned"],
                                        "connected": st["connected"], "showing": st["showing"]["label"]})
            if path == "/api/settings":
                if not self._admin():
                    return
                s = engine.settings.snapshot()
                s["autostart"] = system.autostart_enabled()
                return self._send(200, s)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._guard():
                return
            if not self.headers.get("X-Leo"):
                return self._send(403, {"error": "missing X-Leo header"})
            path = self.path.split("?")[0]
            body = self._body()
            if not isinstance(body, dict):
                return self._send(400, {"error": "bad json"})
            if path == "/api/cmd":
                source = body.get("source", "mcp")
                if source not in HTTP_SOURCES:
                    return self._send(400, {"error": "bad source"})
                if source == "gui" and not self._admin():
                    return
                return self._send(200, engine.handle(body.get("req") or {}, source))
            if not self._admin():
                return
            if path == "/api/settings":
                after = engine.settings.patch(body)
                if "autostart" in body and after["autostart"] != system.autostart_enabled():
                    system.set_autostart(after["autostart"])
                with engine.lock:
                    engine._log("gui", "settings saved")
                return self._send(200, after)
            if path == "/api/shortcut":
                from . import setup_cmd
                try:
                    return self._send(200, {"ok": True, "path": setup_cmd.create_shortcut(body.get("where", "desktop"))})
                except (RuntimeError, ValueError, OSError) as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if path == "/api/pick-folder":
                return self._send(200, pick_folder())
            if path == "/api/smart-example":
                made, created = engine.shortcuts.write_example()
                return self._send(200, {"ok": True, "path": str(made), "created": created})
            if path == "/api/reconnect":
                engine.request_reconnect()
                return self._send(200, {"ok": True})
            if path == "/api/quit":
                self._send(200, {"ok": True})
                if on_quit:
                    on_quit()
                return
            self._send(404, {"error": "not found"})

    srv = _Server((host, port), Handler)
    srv.daemon_threads = True
    srv.token = token
    return srv
