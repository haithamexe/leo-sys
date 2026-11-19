"""Claude Code hook: forward a lifecycle event to the leo-sys service. Never blocks, never fails.

Registered for several events; Claude Code sends the event as JSON on stdin.
"""
import json
import sys
import time

from . import toolinfo


def build(data):
    """Turn a hook payload into a request for the service."""
    ev = str(data.get("hook_event_name") or "")
    if not ev:
        return {"cmd": "event", "event": "Notification", "session": "", "ts": time.time()}
    req = {"cmd": "event", "event": ev, "session": str(data.get("session_id") or ""), "cwd": str(data.get("cwd") or ""),
           "ts": time.time()}
    if ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        tool = data.get("tool_name")
        inp = data.get("tool_input")
        command = str(inp.get("command") or "") if isinstance(inp, dict) else ""
        req["tool"] = tool
        req["kind"] = toolinfo.tool_kind(tool, command)
    elif ev == "Notification":
        req["ntype"] = data.get("notification_type")
    return req


def run():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            raw = ""
        elif hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        else:
            raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if isinstance(data, dict) and data:
            from .client import send
            send(build(data), "hook")
    except Exception:
        pass
    sys.exit(0)
