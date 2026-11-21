"""The heart of leo-sys: tracks Claude's sessions from the hooks, decides what the keyboard shows, and runs the one
worker thread that talks to it.

The keyboard goes back to its own effects when frames stop, so "showing nothing" is easy: we just stop sending.
That is how the master switch, quiet hours and an idle Claude hand the keyboard back to you.
"""
import collections
import datetime
import json
import math
import os
import threading
import time

from . import albums, apps, launch, device, faces, keyfx, media, modes, pcstats, policy, power, prayer, privacyuse, screen, smartkeys, taskalerts, taskflash, typingmeter, weather, winstate
from .keys import BY_NAME
from .config import DATA_DIR
from .settings import Settings

TICK = 0.1            # seconds between frames
KEEPALIVE = 1.0       # resend an unchanged frame this often, or the keyboard falls back to its own effect
STALE_SESSION = 3 * 3600
URGENCY = ["attention", "working", "thinking", "done", "idle"]   # most urgent first
VOLUME_POPUP = 2.0    # seconds the volume shows on the number row after it changes
NUMBER_ROW = ["!1", "@2", "#3", "$4", "%5", "^6", "&7", "*8", "(9", ")0"]
OFF_OK = {"power", "status", "pin", "mode", "typingreset"}   # while switched off: only the switch itself and plain settings


class Engine:
    def __init__(self, settings=None, keyboard=device.Keyboard, clock=time.time, threads=True,
                 monitor=None, media_watch=None, probe=None, shortcuts=None, screen_watch=None, weather_watch=None, privacy_watch=None, flash_watch=None, typing_meter=None,
                 switch=None):
        self.settings = settings or Settings()
        self.switch = switch or power.Switch(self.settings.path.parent / "off")    # the master off switch, beside the settings
        real = threads and os.name == "nt"
        self.monitor = monitor or (pcstats.Monitor(clock=clock) if real else pcstats.FakeMonitor(clock=clock))
        self.media = media_watch or (media.MediaWatch(clock=clock) if real else media.FakeMediaWatch(clock=clock))
        self.keyboard_factory = keyboard
        self.clock = clock
        self.use_threads = threads
        self.lock = threading.RLock()
        self.quit = threading.Event()
        self.started = clock()
        self.pinned = bool(self.settings.snapshot().get("pinned", False))
        self.connected = False
        self.last_error = ""
        self.official_app = False
        self.log = collections.deque(maxlen=300)
        self.sessions = {}
        self.manual = None          # a face, text or drawing picked by you or Claude's tools: {"kind", "until", ...}
        self.manual_keys = None     # key colours picked by you or Claude's tools: {"colours", "until"}
        self.flash = None           # brief reaction drawn over the live status: {"mood", "until"}
        self.last_change = 0.0      # last time Claude's tools changed something (for the rate limit)
        self.mood, self.mood_since = None, clock()
        self.shown = {"matrix": None, "keys": None, "label": "Starting", "kind": "none"}
        self.albums = albums.Albums()
        self.screen = screen_watch or (screen.ScreenWatch(clock=clock) if real else screen.FakeScreenWatch(clock=clock))
        self.screen_seen = {"on": False, "error": ""}
        self.typing = typing_meter or (typingmeter.TypingMeter(clock=clock) if real else typingmeter.FakeTypingMeter(clock=clock))
        self.flashes = flash_watch or (taskflash.FlashWatch(clock=clock) if real else taskflash.FakeFlashWatch(clock=clock))
        self.alert = None            # {"name", "colour", "exe", "start", "until"} while a taskbar alert shows
        self.alert_last = {}         # program -> when it last raised an alert (for the cooldown)
        self.alert_retry = 0.0       # do not retry a failed listener start more often than every 30 s
        self.privacy = privacy_watch or (privacyuse.UseWatch(clock=clock) if real else privacyuse.FakeUseWatch(clock=clock))
        self.weather = weather_watch or (weather.WeatherWatch(clock=clock) if real else weather.FakeWeatherWatch(clock=clock))
        self.probe = probe or winstate.input_state     # (modifiers held, focused program): polled, nothing hooked
        self.shortcuts = shortcuts or smartkeys.Library(DATA_DIR / "shortcuts.json")
        self.smart_seen = {"mods": frozenset(), "exe": "", "active": False}
        self.reconnect_flag = False

    # ---- logging ----------------------------------------------------------------------
    def _log(self, source, action, ok=True, detail=""):
        self.log.appendleft({"t": self.clock(), "source": source, "action": str(action)[:80], "ok": ok,
                             "detail": str(detail)[:200]})

    # ---- entry point ------------------------------------------------------------------
    def handle(self, req, source="mcp"):
        if not isinstance(req, dict) or "cmd" not in req:
            return {"ok": False, "error": "bad request"}
        if self.switch.is_off() and req.get("cmd") not in OFF_OK:
            # Switched off: nothing from Claude's tools, the hooks or scripts, and nothing that would draw. Not logged: the
            # hooks fire all the time, and a long list of refusals would bury everything else.
            return {"ok": False, "denied": power.MESSAGE if source not in policy.PRIVILEGED
                    else "leo-sys is switched off: switch it on first", "connected": False}
        if source == "push":                # a script on this PC: its own switch, not Claude's permissions
            with self.lock:
                return self._handle_push(req)
        with self.lock:
            cmd = req.get("cmd")
            if cmd == "event":
                return self._handle_event(req, source)
            if cmd in policy.MEDIA_TOOL_FOR:
                cfg = self.settings.snapshot()["claude"]
                ok, reason, req = policy.decide(cfg, req, source, self.pinned)
                if not ok:
                    self._log(source, policy.describe(req), False, reason)
                    return {"ok": False, "denied": reason, "connected": self.connected}
        if cmd in policy.MEDIA_TOOL_FOR:
            return self._handle_media(req, source)       # outside the lock: Windows may take a moment to answer
        with self.lock:
            if cmd in ("pin", "resume", "status", "alerttest", "typingreset", "mode", "power") and source not in policy.PRIVILEGED:
                return {"ok": False, "denied": f"'{cmd}' is only for the control panel", "connected": self.connected}
            cfg = self.settings.snapshot()["claude"]
            ok, reason, req = policy.decide(cfg, req, source, self.pinned, datetime.datetime.now(),
                                            self.last_change, self.clock())
            if not ok:
                self._log(source, policy.describe(req), False, reason)
                return {"ok": False, "denied": reason, "connected": self.connected}
            try:
                self._apply(req, source)
            except (ValueError, KeyError, TypeError, OverflowError, OSError) as e:
                self._log(source, policy.describe(req), False, f"invalid: {e}")
                return {"ok": False, "error": str(e)}
            if source not in policy.PRIVILEGED:
                self.last_change = self.clock()
            return {"ok": True, "connected": self.connected}

    def _handle_push(self, req):
        """text, keys (a colour for every key), bar (a progress bar) or clear, from `leo-sys push` or the local API."""
        cfg = self.settings.snapshot()["push"]
        cmd = req.get("cmd")
        if cmd not in ("text", "keys", "bar", "clear"):
            return {"ok": False, "error": f"unknown push command {cmd!r}"}
        if not cfg["enabled"]:
            self._log("push", f"push · {cmd}", False, "switched off in the panel")
            return {"ok": False, "denied": "pushes from scripts are switched off in the panel", "connected": self.connected}
        now = self.clock()
        try:
            seconds = max(1.0, min(300.0, float(req.get("seconds") or cfg["seconds"])))
        except (TypeError, ValueError):
            return {"ok": False, "error": "seconds must be a number"}
        until = now + seconds
        colour = _rgb(req.get("colour"), faces.ORANGE)
        if cmd == "clear":
            self.manual = self.manual_keys = None
            self._log("push", "push · clear")
            return {"ok": True, "connected": self.connected}
        if cmd == "text":
            text = str(req.get("text", "")).strip()[:200]
            if not text:
                return {"ok": False, "error": "empty text"}
            self.manual = {"kind": "text", "text": text.upper(), "colour": colour, "until": until, "source": "push", "start": now}
            self._log("push", f"push · text {text[:24]}")
        elif cmd == "bar":
            try:
                frame = faces.bar_frame(float(req.get("percent")), colour)
            except (TypeError, ValueError):
                return {"ok": False, "error": "percent must be a number from 0 to 100"}
            self.manual = {"kind": "pixels", "frames": [(frame, 1.0)], "until": until, "source": "push", "start": now}
            self._log("push", f"push · bar {req.get('percent')}%")
        else:                                                           # keys: every key in one colour
            self.manual_keys = {"colours": keyfx._all(colour), "until": until, "source": "push"}
            self._log("push", "push · keys")
        if cmd != "keys" and req.get("keys"):                           # text and bar can colour the keys too
            self.manual_keys = {"colours": keyfx._all(_rgb(req["keys"], colour)), "until": until, "source": "push"}
        return {"ok": True, "connected": self.connected}

    def _handle_media(self, req, source):
        try:
            if req["cmd"] == "nowplaying":
                return {"ok": True, **self.media.now_playing()}
            r = self.media.control(str(req.get("action")), req.get("value"))
        except (ValueError, TypeError, KeyError, OSError, TimeoutError) as e:
            with self.lock:
                self._log(source, policy.describe(req), False, str(e))
            return {"ok": False, "error": str(e)}
        with self.lock:
            self._log(source, policy.describe(req), bool(r.get("ok")), "" if r.get("ok") else r.get("error", "the player did not accept it"))
        return dict(r, ok=bool(r.get("ok")))

    def _hold(self):
        return self.clock() + float(self.settings.snapshot()["scene"]["hold_seconds"])

    def _apply(self, req, source):
        cmd = req["cmd"]
        now = self.clock()
        if cmd == "face":
            mood = req["mood"]
            if mood not in faces.MOODS:
                raise ValueError(f"unknown mood {mood!r}")
            self.manual = {"kind": "face", "mood": mood, "until": self._hold(), "source": source, "start": now}
            self._log(source, f"face · {mood}")
        elif cmd == "text":
            text = str(req["text"]).strip()
            if not text:
                raise ValueError("empty text")
            colour = _rgb(req.get("colour"), faces.ORANGE)
            self.manual = {"kind": "text", "text": text.upper(), "colour": colour, "until": self._hold(),
                           "source": source, "start": now}
            self._log(source, f"text · {text[:24]}")
        elif cmd == "pixels":
            frames = req["frames"]
            if not 1 <= len(frames) <= 12:
                raise ValueError("1 to 12 frames")
            clean = []
            for f in frames:
                rows = f["rows"]
                if len(rows) != faces.HEIGHT or any(len(r) != faces.WIDTH for r in rows):
                    raise ValueError(f"each frame needs {faces.HEIGHT} rows of {faces.WIDTH} pixels")
                clean.append(([[_rgb(p, faces.OFF) for p in r] for r in rows],
                              max(0.05, min(5.0, int(f.get("delay", 300)) / 1000))))
            self.manual = {"kind": "pixels", "frames": clean, "until": self._hold(), "source": source, "start": now}
            self._log(source, f"drawing · {len(clean)} frame(s)")
        elif cmd == "keys":
            if req.get("off"):
                self.manual_keys = None
                self._log(source, "keys handed back")
                return
            if "colour" in req:
                c = _rgb(req["colour"], faces.ORANGE)
                cols = keyfx._all(c)
            else:
                cols = {}
                from .keys import BY_NAME
                for name, value in (req.get("keys") or {}).items():
                    if name not in BY_NAME:
                        raise ValueError(f"unknown key {name!r}")
                    cols[BY_NAME[name]] = _rgb(value, faces.OFF)
            self.manual_keys = {"colours": cols, "until": self._hold(), "source": source}
            self._log(source, policy.describe(req))
        elif cmd == "pin":
            self.pinned = bool(req.get("value"))
            self.settings.patch({"pinned": self.pinned})
            self._log(source, "keyboard pinned" if self.pinned else "keyboard unpinned")
        elif cmd == "resume":
            self.manual = self.manual_keys = self.flash = None
            self._log(source, "live status resumed")
        elif cmd == "mode":
            name = req.get("name")
            if name not in modes.MODES:
                raise ValueError(f"unknown mode {name!r}")
            (modes.turn_on if req.get("on") else modes.turn_off)(self.settings, name)
            self._log(source, f"mode · {modes.MODES[name]['label']} {'on' if req.get('on') else 'off'}")
        elif cmd == "power":
            off = not req.get("on")
            if self.switch.set_off(off) != off:
                raise ValueError("could not change the master switch")
            if off:
                self._rest()                  # at once; the worker then closes its link to the keyboard
            self._log(source, "leo-sys switched off: the keyboard is let go" if off else "leo-sys switched on")
        elif cmd == "typingreset":
            self.typing.reset()
            self._log(source, "typing counts cleared")
        elif cmd == "alerttest":
            self._raise_alert("discord.exe", now, self.settings.snapshot()["alerts"]["taskbar"], force=True)
        elif cmd == "status":
            pass
        else:
            raise ValueError(f"unknown command {cmd!r}")

    # ---- Claude Code sessions --------------------------------------------------------------
    def _handle_event(self, req, source):
        if source not in ("hook", "gui", "system"):
            return {"ok": False, "denied": "only the status hooks may send events", "connected": self.connected}
        try:
            self._record_event(req, self.clock())
        except (ValueError, TypeError, OverflowError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "connected": self.connected}

    def _record_event(self, ev, now):
        """Update session bookkeeping from one hook event. Always runs, whatever the permissions say."""
        name = str(ev.get("event", ""))
        sid = str(ev.get("session") or "default")[:64]
        if name == "SessionEnd":
            self.sessions.pop(sid, None)
            return
        s = self.sessions.get(sid)
        if s is None:
            if len(self.sessions) >= 50:
                del self.sessions[min(self.sessions, key=lambda k: self.sessions[k]["last_event"])]
            s = self.sessions[sid] = {"id": sid, "project": "", "state": "idle", "kind": None, "tool": None,
                                      "since": now, "last_event": now, "last_ts": 0.0, "agents": 0}
        cwd = str(ev.get("cwd") or "")
        if cwd:
            s["project"] = os.path.basename(cwd.rstrip("/\\"))[:30]
        ts = ev.get("ts")
        ts = float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) and abs(ts) < 1e12 else now
        ts = min(ts, now)
        fresh = ts >= s["last_ts"]       # hooks run asynchronously: a late arrival must not undo newer state
        s["last_ts"] = max(s["last_ts"], ts)
        s["last_event"] = now

        def set_state(st):
            if fresh and s["state"] != st:
                s["state"], s["since"] = st, now

        if name == "SessionStart":
            set_state("idle")
        elif name == "UserPromptSubmit":
            set_state("thinking")
            if fresh:
                s["tool"] = s["kind"] = None
        elif name == "PreToolUse":
            set_state("working")
            if fresh:
                s["tool"] = str(ev.get("tool") or "")[:40] or None
                s["kind"] = ev.get("kind") if ev.get("kind") in faces.KIND_LABELS else None
        elif name in ("PostToolUse", "PostToolUseFailure"):
            if s["state"] == "attention":
                set_state("working")
            if name == "PostToolUseFailure" and self.settings.snapshot()["scene"]["reactions"]:
                self.flash = {"mood": "error", "until": now + 1.5}
        elif name == "Notification":
            if ev.get("ntype") in (None, "", "permission_prompt", "idle_prompt", "elicitation_dialog",
                                   "agent_needs_input"):
                set_state("attention")
        elif name == "SubagentStart":
            s["agents"] += 1
        elif name == "SubagentStop":
            s["agents"] = max(0, s["agents"] - 1)
        elif name == "Stop":
            set_state("done")
            if fresh:
                s["tool"] = s["kind"] = None
                s["agents"] = 0

    def _session_mood(self, s, now, done_seconds):
        if s["state"] == "done" and now - s["since"] > done_seconds:
            return "idle"
        return s["state"]

    def _live(self, now, snap):
        """(mood, label, session dot colours) for the live status, from all open sessions."""
        for sid in [k for k, s in self.sessions.items() if now - s["last_event"] > STALE_SESSION]:
            del self.sessions[sid]
        if not self.sessions:
            return None, None, []
        done_s = snap["scene"]["done_seconds"]
        moods = {sid: self._session_mood(s, now, done_s) for sid, s in self.sessions.items()}
        top_sid = min(moods, key=lambda k: (URGENCY.index(moods[k]) if moods[k] in URGENCY else 9,
                                            -self.sessions[k]["since"]))
        mood = moods[top_sid]
        label = None
        s = self.sessions[top_sid]
        if mood == "working" and snap["scene"]["show_tool"]:
            label = faces.KIND_LABELS.get(s["kind"]) if s["kind"] else None
        dots = []
        if snap["scene"]["session_dots"] and len(self.sessions) > 1:
            order = sorted(self.sessions, key=lambda k: self.sessions[k]["since"])
            dots = [faces.MOOD_COLOURS.get(moods[k], faces.ORANGE) for k in order]
        return mood, label, dots

    # ---- what to show right now ---------------------------------------------------------------
    def compose(self, now):
        """Decide the matrix frame and key colours for this moment. None means hand that part back."""
        if self.switch.is_off():
            self._rest()
            return None, None
        with self.lock:
            snap = self.settings.snapshot()
            bright = snap["brightness"]
            claude_ok = policy.decide(snap["claude"], {"cmd": "event"}, "hook", self.pinned,
                                      datetime.datetime.now(), self.last_change, now)[0]
            if self.manual and now >= self.manual["until"]:
                self.manual = None
            if self.manual_keys and now >= self.manual_keys["until"]:
                self.manual_keys = None
            if self.flash and now >= self.flash["until"]:
                self.flash = None
            mood, label, dots = self._live(now, snap) if claude_ok else (None, None, [])
            claude_now = {"mood": mood or ("idle" if self.sessions and claude_ok else "sleep"), "label": label,
                          "dots": dots, "t": now - self.mood_since}
            if mood in ("idle", "sleep"):
                mood = None              # a quiet Claude leaves the keyboard to your apps (or its own effects)
            if mood != self.mood:
                self.mood, self.mood_since = mood, now

            need = apps.needs(snap["apps"])     # asked for every tick, so graphs keep filling under Claude
            if need:
                self.monitor.want(need, pcstats.clean_host(snap["apps"]["monitor"]["ping_host"]))
            media_need = apps.needs(snap["apps"], "media")
            if snap["keys"]["enabled"] and (snap["keys"]["indicators"]["mic_use"] or snap["keys"]["indicators"]["cam_use"]):
                self.privacy.want()                          # the privacy record is read only while an indicator needs it
            album_need = bool(apps.needs(snap["apps"], "album"))
            self.typing.set_enabled(snap["apps"]["typing"]["enabled"])      # off: the meter stops and forgets everything
            typing_need = snap["apps"]["typing"]["enabled"] and "typing" in apps.needs(snap["apps"], "typing")
            weather_need = "weather" in apps.needs(snap["apps"], "city")
            if weather_need:                                 # asked every tick while in use; it fetches at most every 30 min
                self.weather.want(snap["apps"]["city"]["lat"], snap["apps"]["city"]["lon"])
            ind = snap["keys"]["indicators"]
            if snap["keys"]["enabled"] and (ind["volume"] or ind["mic"]):
                media_need.add("volume")
            if media_need:
                self.media.want(*media_need)
            # always | alerts | never: when Claude beats your apps. The Claude app, picked or in the rotation, is always.
            takes = "always" if apps.claude_picked(snap["apps"]) else snap["scene"]["claude_takes_over"]
            alert = self._current_alert(now, snap["alerts"]["taskbar"])
            matrix, kind, shown_label = None, "released", "Keyboard's own effect"
            m = self.manual
            if m:
                t = now - m["start"]
                if m["kind"] == "face":
                    matrix, shown_label = faces.face(m["mood"], t), f"face · {m['mood']}"
                elif m["kind"] == "text":
                    matrix, shown_label = faces.text_frame(m["text"], t, m["colour"]), f"text · {m['text'][:24]}"
                else:
                    total = sum(d for _, d in m["frames"])
                    pos, acc = t % total, 0.0
                    for fr, d in m["frames"]:
                        acc += d
                        if pos < acc:
                            matrix = fr
                            break
                    shown_label = "drawing"
                kind = m["source"]
            elif alert is not None:
                matrix = faces.text_frame(alert["name"] + " !", now - alert["start"], alert["colour"])
                kind, shown_label = "alert", f"alert · {alert['name'].lower()}"
            elif self.flash and claude_ok and takes != "never" and snap["claude"]["moods"].get("error", True):
                matrix, kind, shown_label = faces.face("error", now - self.flash["until"]), "hook", "Claude · a tool failed"
            elif mood and _claude_takes(takes, mood) and snap["claude"]["moods"].get(mood, True):
                matrix = faces.face(mood, now - self.mood_since, label, dots)
                kind, shown_label = "hook", f"Claude · {label or faces.LABELS[mood].lower()}"
            else:
                stats = dict(self.monitor.snapshot() if need else apps.NO_STATS,
                             media=self.media.snapshot() if media_need else {}, claude=claude_now,
                             album=self.albums.matrix.get(snap["albums"]["matrix"], now) if album_need else None,
                             weather=self.weather.snapshot() if weather_need else None,
                             typing=self.typing.snapshot() if typing_need else None)
                frame, app_label = apps.matrix_frame(now, snap["apps"], stats)
                if frame is not None:
                    matrix, kind, shown_label = frame, "app", app_label

            keys, from_base = None, True
            if self.manual_keys:
                keys, from_base = self.manual_keys["colours"], False
            elif alert is not None and snap["keys"]["enabled"]:
                on = int((now - alert["start"]) / 0.35) % 2 == 0           # the keys flash in the program's colour
                keys, from_base = (keyfx._all(alert["colour"]) if on else {}), False
            elif snap["keys"]["enabled"] and claude_ok:
                km = "error" if self.flash else mood
                if km and _claude_takes(takes, km) and snap["keys"]["moods"].get(km, False):
                    keys = keyfx.colours(km, now, self.flash["until"] - 1.5 if self.flash else self.mood_since)
                    from_base = False
            if keys is None and not self.manual_keys:
                shot = None
                if snap["keys"]["enabled"] and snap["keys"]["base"] == "heatmap":
                    shot = {"typing": self.typing.snapshot()}
                if snap["keys"]["enabled"] and snap["keys"]["base"] == "screen":
                    sc = snap["keys"]["screen"]
                    self.screen.want(sc["fps"], sc["smooth"])      # the screen is read only while this base is in use
                    shot = {"screen": self.screen.snapshot()}
                screen_on = bool(shot and "screen" in shot)
                self.screen_seen = {"on": screen_on, "error": shot["screen"]["error"] if screen_on else ""}
                keys = apps.key_colours(now, snap["keys"], shot)
            if from_base and snap["keys"]["enabled"] and snap["keys"]["smart"]["enabled"]:
                keys = self._smart(keys, snap["keys"]["smart"])     # not over Claude's own effects or a picked colour
            volume_popup = snap["keys"]["enabled"] and ind["volume"] and self._volume_popup(now)
            if keys is None and volume_popup and not self.manual_keys:
                keys = {}                         # on the keyboard's own lighting, the volume shows on dark keys
            if keys is not None:
                keys = self._indicators(dict(keys), ind if snap["keys"]["enabled"] else {"caps": False}, now)
            if matrix is not None:
                matrix = faces.scale(matrix, bright)
            if keys is not None and bright < 100:
                keys = {i: faces.dim(c, bright / 100) for i, c in keys.items()}

            self.shown = {"matrix": matrix, "keys": keys, "label": shown_label, "kind": kind}
            return matrix, keys

    def _rest(self):
        """Switched off: forget everything live and stop everything that listens. The worker closes the keyboard link."""
        with self.lock:
            self.manual = self.manual_keys = self.flash = self.alert = None
            self.sessions.clear()
            self.mood, self.mood_since = None, self.clock()
            self.connected, self.last_error = False, ""
            self.shown = {"matrix": None, "keys": None, "label": "Switched off", "kind": "off"}
            self.screen_seen = {"on": False, "error": ""}
        self.typing.set_enabled(False)               # stops counting (and forgets, as always when it is off)
        if self.flashes.running():
            self.flashes.stop()
            self.flashes.clear()
        # The screen, PC, media, weather and privacy watchers read only while asked, and nothing asks now: they go idle on
        # their own within a few seconds.

    def _raise_alert(self, exe, now, cfg, force=False):
        """Show an alert for a program unless it is ignored or raised one a moment ago."""
        if not force and (not taskalerts.accept(exe, cfg) or now - self.alert_last.get(exe, -1e9) < float(cfg["cooldown_s"])):
            return False
        name, colour = taskalerts.describe(exe)
        self.alert_last[exe] = now
        self.alert = {"name": name, "colour": colour, "exe": exe, "start": now, "until": now + max(1.0, float(cfg["seconds"]))}
        self._log("system", f"alert · {name}")
        return True

    def _current_alert(self, now, cfg):
        """Start or stop the listener to match the switch, take in new flashes, and return the alert showing, if any."""
        if cfg["enabled"]:
            if not self.flashes.running() and now >= self.alert_retry:
                self.alert_retry = now + 30                  # one try every 30 s, whatever happens
                self.flashes.start(wait=0)                   # never wait here: the engine's lock is held
            for event in self.flashes.pop_new():
                self._raise_alert(event["exe"], now, cfg)
        elif self.flashes.running():
            self.flashes.stop()
            self.flashes.clear()                             # and forget which programs flashed
            self.alert = None
        if self.alert and now >= self.alert["until"]:
            self.alert = None
        return self.alert

    def _smart(self, keys, cfg):
        """Hold Ctrl, Alt, Shift or Win and the keys with a shortcut for it glow (smartkeys.py)."""
        mods, exe = self.probe()
        sc = self.shortcuts.current()
        out = smartkeys.overlay(keys, mods, sc.app_for(exe), sc, cfg)
        self.smart_seen = {"mods": frozenset(mods), "exe": exe, "active": out is not keys}
        return out

    def _volume_popup(self, now):
        m = self.media.snapshot()
        return m["volume"] is not None and now - m["volume_changed"] < VOLUME_POPUP

    def _indicators(self, keys, cfg, now=None):
        """Small signs drawn over whatever the keys show. Only possible while leo-sys drives the keys: on the
        keyboard's own lighting there is nothing to draw on (the volume is the exception: it shows briefly anyway)."""
        if cfg.get("caps") and winstate.caps_on():
            keys[BY_NAME["CapsLock"]] = faces.WHITE
        if cfg.get("mic") or cfg.get("volume"):
            m = self.media.snapshot()
            if cfg.get("mic") and m["mic_muted"] and cfg.get("mic_key") in BY_NAME:
                keys[BY_NAME[cfg["mic_key"]]] = faces.RED
            if cfg.get("volume") and now is not None and self._volume_popup(now):
                lit = round(m["volume"] / 10)
                for i, name in enumerate(NUMBER_ROW):
                    keys[BY_NAME[name]] = (faces.RED if m["muted"] else faces.WHITE) if i < lit or m["muted"] else faces.OFF
        if cfg.get("mic_use") or cfg.get("cam_use"):
            use = self.privacy.snapshot()
            pulse = 1.0 if now is None else 0.55 + 0.45 * (math.sin(now * 4) + 1) / 2
            if cfg.get("mic_use") and use["mic"] and cfg.get("mic_use_key") in BY_NAME:
                keys[BY_NAME[cfg["mic_use_key"]]] = faces.dim((255, 96, 24), pulse)
            if cfg.get("cam_use") and use["cam"] and cfg.get("cam_key") in BY_NAME:
                keys[BY_NAME[cfg["cam_key"]]] = faces.dim(faces.RED, pulse)
        return keys

    # ---- the worker thread ---------------------------------------------------------------------
    def start(self):
        if self.use_threads:
            threading.Thread(target=self._worker, daemon=True, name="keyboard").start()

    def stop(self):
        self.quit.set()

    def request_reconnect(self):
        self.reconnect_flag = True

    def _worker(self):
        kb, last_m, last_k, sent_m, sent_k, last_app_check = None, None, None, 0.0, 0.0, 0.0
        while not self.quit.is_set():
            t0 = self.clock()
            if self.reconnect_flag and kb is not None:
                kb.close()
                kb = None
            self.reconnect_flag = False
            if self.keyboard_factory is device.Keyboard and t0 - last_app_check > 10:
                last_app_check = t0
                self.official_app = device.official_app_running()
            if self.switch.is_off():             # the master switch: let go of the keyboard entirely, so the Leobog app can have it
                if kb is not None:
                    kb.close()
                    kb = None
                last_m = last_k = None
                self._rest()
                self.quit.wait(0.5)
                continue
            if kb is None:
                try:
                    kb = self.keyboard_factory()
                    kb.connect()
                except Exception as e:
                    kb = None
                    with self.lock:
                        if self.connected:
                            self._log("system", "keyboard disconnected", False, str(e))
                        self.connected, self.last_error = False, str(e) or type(e).__name__
                    self.quit.wait(3)
                    continue
                with self.lock:
                    self.connected, self.last_error = True, ""
                    self._log("system", "keyboard connected")
                last_m = last_k = None
            try:
                matrix, keys = self.compose(t0)
                if matrix is not None and (matrix != last_m or t0 - sent_m >= KEEPALIVE):
                    kb.matrix(faces.led_frame(matrix, faces.led_lut(self.settings.snapshot()["colour_gamma"])))
                    last_m, sent_m = matrix, t0
                elif matrix is None:
                    last_m = None
                if keys is not None and (keys != last_k or t0 - sent_k >= KEEPALIVE):
                    lut = faces.led_lut(self.settings.snapshot()["colour_gamma"])
                    kb.keys({i: faces.led_colour(c, lut) for i, c in keys.items()})
                    last_k, sent_k = keys, t0
                elif keys is None:
                    last_k = None
            except Exception as e:
                with self.lock:
                    self.connected, self.last_error = False, str(e) or type(e).__name__
                    self._log("system", "keyboard connection lost", False, str(e))
                kb.close()
                kb = None
                continue
            self.quit.wait(max(0.0, TICK - (self.clock() - t0)))
        if kb:
            kb.close()

    # ---- what the panel reads -------------------------------------------------------------------
    def _albums_state(self, now, s):
        """What the panel shows under the album folder: how many pictures it holds."""
        folder = s["albums"]["matrix"]["folder"]
        return {"matrix": self.albums.peek(folder, now) if str(folder).strip() else {"images": None, "error": ""}}

    def _city_state(self, now, s):
        """What the panel shows under the weather and prayer switches: today's times, the weather now."""
        a, out = s["apps"], {"weather": None, "prayer": None}
        if a["prayer"]["enabled"]:
            p, c = a["prayer"], a["city"]
            shown = tuple(n for n in prayer.PRAYERS if p["show"].get(n, True)) or prayer.PRAYERS
            try:
                name, when, today = prayer.next_prayer(datetime.datetime.fromtimestamp(now), c["lat"], c["lon"], p["method"],
                                                       p["asr"], p["offsets"], enabled=shown)
                out["prayer"] = {"times": {k: v.strftime("%H:%M") if v else None for k, v in today.items()}, "next": name,
                                 "next_time": when.strftime("%H:%M") if when else None}
            except (ValueError, ArithmeticError, TypeError, OSError):
                out["prayer"] = {"times": {}, "next": None, "next_time": None}
        if a["weather"]["enabled"]:
            snap = self.weather.snapshot()
            d = snap["data"] or {}
            out["weather"] = {"temp": d.get("temp"), "high": d.get("high"), "low": d.get("low"), "age": snap["age"],
                              "error": snap["error"]}
        return out

    def _pc_state(self, now):
        """The latest PC readings, only for what an app is reading right now."""
        active = [m for m in pcstats.METRICS if m in self.monitor.active(now)]
        values = self.monitor.snapshot()["values"] if active else {}
        return {"active": active, "values": {m: values.get(m) for m in active}}

    def state(self):
        with self.lock:
            now = self.clock()
            s = self.settings.snapshot()
            sessions = []
            for x in sorted(self.sessions.values(), key=lambda v: v["since"]):
                sessions.append({"id": x["id"], "project": x["project"], "state": self._session_mood(
                    x, now, s["scene"]["done_seconds"]), "tool": x["tool"], "agents": x["agents"],
                    "for": now - x["since"], "idle_for": now - x["last_event"]})
            m = self.shown["matrix"]
            return {
                "off": self.switch.is_off(),
                "connected": self.connected, "error": self.last_error, "official_app": self.official_app,
                "device": getattr(self.keyboard_factory, "name", "keyboard"),
                "pinned": self.pinned, "brightness": s["brightness"],
                "showing": {"label": self.shown["label"], "kind": self.shown["kind"],
                            "matrix": faces.to_hex(m) if m else None, "keys": keyfx.to_hex(self.shown["keys"])},
                "manual_for": round(self.manual["until"] - now) if self.manual else None,
                "albums": self._albums_state(now, s),
                "screen": dict(self.screen_seen),
                "city": self._city_state(now, s),
                "modes": modes.state(s),
                "typing": {k: v for k, v in self.typing.snapshot().items() if k not in ("counts", "buckets")},   # no per-key counts
                "privacy": self.privacy.snapshot(),
                "alerts": {"running": self.flashes.running(), "error": self.flashes.error,
                           "showing": self.alert["name"] if self.alert else None,
                           "seen": [{"exe": e["exe"], "name": taskalerts.describe(e["exe"])[0], "ago": now - e["t"],
                                     "count": e["count"]} for e in self.flashes.seen()]},
                "push": {"command": launch.quote(launch.tool_command()) + " push"},
                "smart": smartkeys.describe(self.shortcuts.current(), self.smart_seen["exe"], self.smart_seen["mods"],
                                            self.smart_seen["active"]),
                "sessions": sessions, "log": list(self.log)[:60], "uptime": now - self.started,
                "pc": self._pc_state(now),
            }


def _claude_takes(takes, mood):
    """Does Claude's status take the matrix and keys from your apps? "alerts": only when it needs you (or a tool
    failed), so a busy Claude leaves the clock, music and the rest alone."""
    return takes == "always" or (takes == "alerts" and mood in ("attention", "error"))


def _rgb(c, default):
    """A colour from [r, g, b], '#rrggbb' or a name such as 'green'."""
    if isinstance(c, str):
        if c.strip().lower() in faces.NAMED_COLOURS:
            return faces.NAMED_COLOURS[c.strip().lower()]
        h = c.lstrip("#")
        if len(h) == 6:
            try:
                return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
        return default
    if isinstance(c, (list, tuple)) and len(c) == 3:
        try:
            return tuple(max(0, min(255, int(v))) for v in c)
        except (TypeError, ValueError):
            return default
    return default
