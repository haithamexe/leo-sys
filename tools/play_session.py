"""Play a pretend Claude Code session through the hook, about 45 seconds, to watch the keyboard react.

    python tools/play_session.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

LEO = Path(__file__).resolve().parent.parent / "leo.py"
SID = "play-session"
STEPS = [
    ("SessionStart", {}, 2, "session starts"),
    ("UserPromptSubmit", {}, 7, "THINKING: amber breathing on the keys"),
    ("PreToolUse", {"tool_name": "Edit", "tool_input": {}}, 8, "EDITING: orange wave across the keys"),
    ("PostToolUse", {"tool_name": "Edit", "tool_input": {}}, 0, ""),
    ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "python -m pytest"}}, 6, "TESTING"),
    ("PostToolUseFailure", {"tool_name": "Bash", "tool_input": {"command": "python -m pytest"}}, 3, "a tool failed: red flash"),
    ("Notification", {"notification_type": "permission_prompt"}, 8, "NEEDS YOU: flashing yellow"),
    ("PreToolUse", {"tool_name": "Read", "tool_input": {}}, 5, "READING"),
    ("Stop", {}, 8, "DONE: green fade on the keys, sparkles on the matrix"),
]


def send(event, extra):
    payload = {"hook_event_name": event, "session_id": SID, "cwd": "C:/work/play", **extra}
    subprocess.run([sys.executable, str(LEO), "hook"], input=json.dumps(payload).encode(), timeout=15)


for event, extra, wait, say in STEPS:
    if say:
        print(say, flush=True)
    send(event, extra)
    time.sleep(wait)
send("SessionEnd", {})
print("session ended: the keyboard goes back to its own lighting")
