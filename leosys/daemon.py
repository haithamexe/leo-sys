"""The background process: engine + HTTP server. One instance per machine (the port is the lock)."""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import device, system
from .config import DATA_DIR
from .engine import Engine
from .server import HOST, PORT, make_server


def _seed_demo(engine):
    """Sample sessions so the panel has something to show without a keyboard (LEO_DEMO=1)."""
    for sid, cwd, events in [("demo-web", "C:/work/web-app", [("UserPromptSubmit", {}), ("PreToolUse", {"tool": "Edit", "kind": "edit"})]),
                             ("demo-api", "C:/work/api-server", [("UserPromptSubmit", {}), ("Stop", {})])]:
        for name, extra in events:
            engine.handle({"cmd": "event", "event": name, "session": sid, "cwd": cwd, **extra}, "hook")


def run():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(handlers=[RotatingFileHandler(DATA_DIR / "daemon.log", maxBytes=200_000, backupCount=1,
                                                      encoding="utf-8")],
                        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    demo = bool(os.environ.get("LEO_DEMO"))
    engine = Engine(keyboard=device.DemoKeyboard if demo else device.Keyboard)
    if demo:
        _seed_demo(engine)
    stop = threading.Event()
    try:
        server = make_server(engine, HOST, PORT, on_quit=stop.set)
    except OSError:
        sys.exit(0)  # another service already owns the port
    fd = os.open(str(DATA_DIR / ".token"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(server.token)
    engine.settings.patch({"autostart": system.autostart_enabled()})
    engine.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info("leo-sys started on %s:%s", HOST, PORT)
    try:
        stop.wait()
    finally:
        server.shutdown()
        engine.stop()
        logging.info("leo-sys stopped")
