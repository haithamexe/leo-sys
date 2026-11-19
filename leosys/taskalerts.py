"""What to do with a taskbar flash: which programs count, what to call them and what colour to flash.

Known chat and mail programs get their own name and colour; any other program is shown by its file name in orange. A program
can be ignored, or alerts can be limited to a list of programs. Some programs flash all the time (a Remote Desktop window on this
PC did), so each program can raise an alert only once per cooldown.
"""
import re

ORANGE = (255, 96, 24)
KNOWN = {
    "discord.exe": ("DISCORD", (88, 101, 242)),
    "whatsapp.exe": ("WHATSAPP", (37, 211, 102)), "whatsapp.root.exe": ("WHATSAPP", (37, 211, 102)),
    "telegram.exe": ("TELEGRAM", (0, 136, 204)),
    "slack.exe": ("SLACK", (236, 178, 46)),
    "teams.exe": ("TEAMS", (98, 100, 167)), "ms-teams.exe": ("TEAMS", (98, 100, 167)),
    "outlook.exe": ("OUTLOOK", (0, 120, 215)), "olk.exe": ("OUTLOOK", (0, 120, 215)),
    "thunderbird.exe": ("MAIL", (0, 120, 215)),
    "zoom.exe": ("ZOOM", (45, 140, 255)), "skype.exe": ("SKYPE", (0, 175, 240)),
    "chrome.exe": ("CHROME", (255, 200, 0)), "msedge.exe": ("EDGE", (0, 190, 190)), "firefox.exe": ("FIREFOX", (255, 113, 57)),
    "steam.exe": ("STEAM", (40, 120, 200)),
}


def parse_names(text):
    """Program file names from a box where they are typed with commas, spaces or new lines: lower case, '.exe' optional."""
    out = set()
    for token in re.split(r"[,;\s]+", str(text or "").lower()):
        token = token.strip()
        if token:
            out.add(token if token.endswith(".exe") else token + ".exe")
    return out


def accept(exe, cfg):
    """Should a flash from this program raise an alert?"""
    exe = (exe or "").lower()
    only = parse_names(cfg.get("only"))
    return bool(exe) and exe not in parse_names(cfg.get("ignore")) and (not only or exe in only)


def describe(exe):
    """(name to show, colour) for a program's file name."""
    exe = (exe or "").lower()
    if exe in KNOWN:
        return KNOWN[exe]
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return (stem.upper()[:20] or "ALERT", ORANGE)
