"""MCP server (stdio, hand-rolled JSON-RPC, no SDK) that lets Claude use the LEOBOG AMG65's matrix and key lights.

Every tool goes through the leo-sys service, which enforces the permissions you set in the panel. A denied call is
reported back as plain text so Claude can adapt instead of retrying.
"""
import json
import sys

from . import __version__, faces, power
from .client import SwitchedOff, get, send
from .keys import KEYS

MOOD_HELP = {
    "idle": "calm, blinking: nothing happening", "thinking": "eyes up, dots growing: reasoning or planning",
    "working": "eyes darting, a scanner: running tools", "attention": "arms up, flashing: I need the user",
    "done": "eyes closed, sparkles: finished", "error": "crossed out: something failed",
    "sleep": "dim, a drifting z: nothing going on", "splash": "the leo-sys sign",
}
COLOR = {"type": "string", "description": "hex colour like #FF6018 (use fully saturated colours: LEDs wash pale ones out)"}

TOOLS = [
    {"name": "set_face",
     "description": "Show one of the status faces on the keyboard's 63x5 LED matrix (it holds for about 30 seconds, "
                    "then the live status comes back). Moods: " + "; ".join(f"{k} = {v}" for k, v in MOOD_HELP.items()),
     "inputSchema": {"type": "object", "required": ["mood"], "properties": {"mood": {"type": "string", "enum": faces.MOODS}}}},
    {"name": "show_text",
     "description": "Show a short message on the matrix (A-Z 0-9 and . ! ? : - + / _ , % ' ( ) = < > #). Up to 15 "
                    "characters fit; longer messages scroll.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "color": COLOR}}},
    {"name": "draw_pixels",
     "description": "Draw a custom picture on the 63x5 matrix. Each frame is exactly 5 strings of exactly 63 characters; "
                    "each character is a key in `palette` (character -> hex colour). Unmapped characters are off. "
                    "Several frames animate and loop.",
     "inputSchema": {"type": "object", "required": ["frames", "palette"], "properties": {
         "frames": {"type": "array", "minItems": 1, "maxItems": 12, "items": {
             "type": "array", "minItems": 5, "maxItems": 5, "items": {"type": "string"}}},
         "palette": {"type": "object", "additionalProperties": {"type": "string"}},
         "delay_ms": {"type": "integer", "description": "per-frame delay, default 300"}}}},
    {"name": "set_keys",
     "description": "Colour the keyboard's key lights for about 30 seconds. Either `color` for every key, or `keys` "
                    "mapping key names to colours (others go dark), or `off: true` to hand them back. Key names: "
                    + ", ".join(n for n, _, _, _ in KEYS),
     "inputSchema": {"type": "object", "properties": {"color": COLOR, "keys": {"type": "object", "additionalProperties": {"type": "string"}},
                                                      "off": {"type": "boolean"}}}},
    {"name": "now_playing",
     "description": "What is playing on this PC right now, from Windows' media sessions (Spotify, YouTube or other "
                    "sites in any browser, VLC and most players): title, artist, album, which app, playing or paused, "
                    "position and length in seconds, plus the speaker volume (0-100) and mute.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "media_control",
     "description": "Control what is playing and the speaker volume. Actions: play, pause, toggle, next, previous, stop "
                    "(sent to the current player); volume_up, volume_down (by `value` points, default 10); set_volume "
                    "(`value` 0-100); mute, unmute. Only act when the user asks.",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous", "stop", "volume_up",
                                               "volume_down", "set_volume", "mute", "unmute"]},
         "value": {"type": "integer", "minimum": 0, "maximum": 100}}}},
    {"name": "leo_status",
     "description": "What the user currently allows me to do on the keyboard, whether it is connected, and what it "
                    "shows. Call this once if a tool was denied.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def build_request(name, a):
    if name == "set_face":
        return {"cmd": "face", "mood": a["mood"]}
    if name == "show_text":
        r = {"cmd": "text", "text": a["text"]}
        if a.get("color"):
            r["colour"] = a["color"]
        return r
    if name == "draw_pixels":
        pal = a["palette"]
        out = []
        for i, fr in enumerate(a["frames"]):
            if len(fr) != faces.HEIGHT or any(len(row) != faces.WIDTH for row in fr):
                raise ValueError(f"frame {i} must be {faces.HEIGHT} rows of exactly {faces.WIDTH} characters")
            out.append({"rows": [[pal.get(ch, "#000000") for ch in row] for row in fr], "delay": a.get("delay_ms", 300)})
        return {"cmd": "pixels", "frames": out}
    if name == "now_playing":
        return {"cmd": "nowplaying"}
    if name == "media_control":
        r = {"cmd": "media", "action": a["action"]}
        if a.get("value") is not None:
            r["value"] = int(a["value"])
        return r
    if name == "set_keys":
        if a.get("off"):
            return {"cmd": "keys", "off": True}
        if a.get("color"):
            return {"cmd": "keys", "colour": a["color"]}
        if a.get("keys"):
            return {"cmd": "keys", "keys": a["keys"]}
        raise ValueError("give color, keys or off")
    raise ValueError(f"unknown tool {name}")


OFF_TEXT = ("denied by the user: leo-sys is switched off (the master switch). It does not touch the keyboard at all "
            "while off. Do not retry; tell the user if they asked for something on the keyboard.")


def call_tool(name, a):
    try:
        return _call_tool(name, a)
    except SwitchedOff:
        return OFF_TEXT                      # the service is not even running: nothing was started for this call


def _call_tool(name, a):
    if name == "leo_status":
        info = get("/api/claude")
        if info.get("switched_off"):
            return json.dumps({"switched_off": True, "note": "the user switched leo-sys off; nothing can be shown"})
        p = info["permissions"]
        allowed = [t for t, on in p["tools"].items() if on] if p["enabled"] else []
        return json.dumps({"connected": info["connected"], "showing": info["showing"], "pinned_by_user": info["pinned"],
                           "claude_control_enabled": p["enabled"], "tools_allowed": allowed,
                           "faces_disabled": [m for m, on in p["moods"].items() if not on],
                           "auto_status_hooks": p["hooks"], "min_seconds_between_changes": p["min_interval_s"],
                           "quiet_hours": p["quiet_hours"]})
    r = send(build_request(name, a), "mcp")
    if r.get("denied") == power.MESSAGE:
        return OFF_TEXT
    if r.get("denied"):
        return f"denied by the user's settings: {r['denied']}. Do not retry; call leo_status to see what is allowed."
    if name == "now_playing" and r.get("ok"):
        return json.dumps({"playing": r.get("now"), "volume": r.get("volume"), "muted": r.get("muted")}
                          if r.get("now") else {"playing": None, "note": "nothing is playing",
                                                "volume": r.get("volume"), "muted": r.get("muted")})
    if name == "media_control":
        if not r.get("ok"):
            return "not done: " + str(r.get("error") or "the player did not accept it (it may not support that)")
        return json.dumps({k: v for k, v in r.items() if k not in ("ok", "connected")})
    if not r.get("ok"):
        raise ValueError(r.get("error", "service error"))
    return "ok" + ("" if r.get("connected") else " (keyboard not connected yet; it will show when it is)")


def _reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if not isinstance(m, dict):
            continue
        method, id_ = m.get("method"), m.get("id")
        if id_ is None:
            continue
        if method == "initialize":
            _reply(id_, {"protocolVersion": (m.get("params") or {}).get("protocolVersion", "2024-11-05"),
                         "capabilities": {"tools": {}}, "serverInfo": {"name": "leo-sys", "version": __version__}})
        elif method == "tools/list":
            _reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            p = m.get("params") or {}
            try:
                _reply(id_, {"content": [{"type": "text", "text": call_tool(p.get("name"), p.get("arguments") or {})}]})
            except Exception as e:
                _reply(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        elif method == "ping":
            _reply(id_, {})
        else:
            _reply(id_, error={"code": -32601, "message": f"method not found: {method}"})
