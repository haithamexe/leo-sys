"""leo-sys tests. No keyboard, no network: python -m unittest discover -s tests"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LEO_DATA", tempfile.mkdtemp(prefix="leo-test-"))

from leosys import apps, cityapps, faces, keyfx, keys, matrix, places, policy, power, prayer, screen, settings  # noqa: E402
from leosys import privacyuse, smartkeys, taskalerts, taskflash, typingmeter, weather, winstate  # noqa: E402
from leosys import hook, mcp_server, setup_cmd  # noqa: E402
from leosys.device import DemoKeyboard  # noqa: E402
from leosys.engine import Engine  # noqa: E402

winstate.caps_on = lambda: False     # the real Caps Lock light must not decide whether a test passes


import struct  # noqa: E402
import time  # noqa: E402
import zlib  # noqa: E402


def write_png(path, w, h, pixel):
    """A real PNG, made with the standard library: pixel(x, y) -> (r, g, b)."""
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                          + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def write_gif(path, w, h, frames, delay_cs):
    """A real animated GIF: frames are lists of palette indexes (0 red, 1 blue, 2 green, 3 black), one per pixel."""
    out = bytearray(b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF1, 0, 0))
    out += bytes([255, 0, 0, 0, 0, 255, 0, 255, 0, 0, 0, 0])
    for px in frames:
        out += b"\x21\xf9\x04\x00" + struct.pack("<H", delay_cs) + b"\x00\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x02"
        bits, nbits, data = 0, 0, bytearray()
        for code in [c for i in px for c in (4, i)] + [4, 5]:       # clear before every pixel keeps codes 3 bits wide
            bits |= code << nbits
            nbits += 3
            while nbits >= 8:
                data.append(bits & 0xFF)
                bits >>= 8
                nbits -= 8
        if nbits:
            data.append(bits & 0xFF)
        for i in range(0, len(data), 255):
            out += bytes([len(data[i:i + 255])]) + bytes(data[i:i + 255])
        out += b"\x00"
    out += b"\x3b"
    Path(path).write_bytes(bytes(out))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def engine(**over):
    s = settings.Settings(Path(tempfile.mkdtemp()) / "s.json")
    if over:
        s.patch(over)
    clock = Clock()
    e = Engine(settings=s, keyboard=DemoKeyboard, clock=clock, threads=False,
               probe=lambda: (frozenset(), ""),                       # no keys held, whatever you are doing now
               shortcuts=smartkeys.Library(Path(tempfile.mkdtemp()) / "shortcuts.json"))
    return e, clock


class Wire(unittest.TestCase):
    def test_led_map_is_the_drivers_five_panels(self):
        idx = [matrix.led_index(x, y) for y in range(5) for x in range(63)]
        self.assertEqual(sorted(idx), list(range(315)))
        self.assertEqual(matrix.led_index(0, 0), 0)
        self.assertEqual(matrix.led_index(14, 0), 70)     # second panel starts at 70
        self.assertEqual(matrix.led_index(0, 1), 14)      # each panel is 14 wide
        self.assertEqual(matrix.led_index(56, 0), 280)    # the last panel is 7 wide
        self.assertEqual(matrix.led_index(62, 4), 314)

    def test_matrix_frame_layout(self):
        px = matrix.blank()
        px[0][0] = (255, 0, 0)
        buf = matrix.encode(px)
        self.assertEqual(len(buf), 960)
        self.assertEqual(buf[:3], b"\xff\x00\x00")
        self.assertEqual(buf[946:948], b"\xaa\x55")

    def test_keys_payload(self):
        buf = keys.encode({0: (1, 2, 3)})
        self.assertEqual(len(buf), 512)
        self.assertEqual(buf[:4], b"\x00\x01\x02\x03")    # Esc is light index 0
        self.assertEqual(len(keys.KEYS), 69)
        self.assertEqual(len({i for _, i, _, _ in keys.KEYS}), 69)

    def test_every_face_fits_and_animates(self):
        for m in faces.MOODS:
            f = faces.face(m, 0.4)
            self.assertEqual((len(f), len(f[0])), (5, 63), m)
            self.assertTrue(any(c != faces.OFF for row in f for c in row), m)

    def test_font_covers_the_labels(self):
        for word in list(faces.LABELS.values()) + list(faces.KIND_LABELS.values()):
            for ch in word:
                self.assertIn(ch, faces.FONT, word)

    def test_long_text_scrolls(self):
        a = faces.text_frame("A LONG MESSAGE THAT WILL NOT FIT", 0.0)
        b = faces.text_frame("A LONG MESSAGE THAT WILL NOT FIT", 1.0)
        self.assertNotEqual(a, b)

    def test_key_effects(self):
        self.assertIsNone(keyfx.colours("idle", 5.0, 0.0))
        self.assertEqual(len(keyfx.colours("working", 5.0, 0.0)), 69)
        self.assertIsNotNone(keyfx.colours("done", 1.0, 0.0))
        self.assertIsNone(keyfx.colours("done", 10.0, 0.0))     # done plays once, then lets go


class Policy(unittest.TestCase):
    def cfg(self, **over):
        c = settings.defaults()["claude"]
        c.update(over)
        return c

    def test_gui_is_never_limited(self):
        self.assertTrue(policy.decide(self.cfg(enabled=False), {"cmd": "face", "mood": "idle"}, "gui")[0])

    def test_master_switch_and_pin(self):
        self.assertFalse(policy.decide(self.cfg(enabled=False), {"cmd": "face", "mood": "idle"}, "mcp")[0])
        self.assertFalse(policy.decide(self.cfg(), {"cmd": "face", "mood": "idle"}, "mcp", pinned=True)[0])

    def test_quiet_hours_wrap_midnight(self):
        c = self.cfg(quiet_hours={"enabled": True, "start": "23:00", "end": "07:00"})
        night = datetime.datetime(2026, 1, 1, 2, 0)
        noon = datetime.datetime(2026, 1, 1, 12, 0)
        self.assertFalse(policy.decide(c, {"cmd": "event"}, "hook", now=night)[0])
        self.assertTrue(policy.decide(c, {"cmd": "event"}, "hook", now=noon)[0])

    def test_hooks_cannot_draw(self):
        self.assertFalse(policy.decide(self.cfg(), {"cmd": "keys", "colour": "#ff0000"}, "hook")[0])

    def test_tool_switch_and_rate_limit(self):
        c = self.cfg()
        c["tools"]["set_keys"] = False
        self.assertFalse(policy.decide(c, {"cmd": "keys"}, "mcp")[0])
        ok, why, _ = policy.decide(self.cfg(), {"cmd": "face", "mood": "done"}, "mcp", last_change=10.0, now_ts=10.2)
        self.assertFalse(ok)
        self.assertIn("too fast", why)


class Settings(unittest.TestCase):
    def test_bad_types_are_ignored(self):
        s = settings.Settings(Path(tempfile.mkdtemp()) / "s.json")
        out = s.patch({"brightness": "loud", "claude": {"enabled": "yes"}, "nonsense": 1, "albums": {"matrix": {"seconds": 1e9}}})
        self.assertEqual(out["brightness"], 100)
        self.assertTrue(out["claude"]["enabled"])
        self.assertNotIn("nonsense", out)
        self.assertEqual(out["albums"]["matrix"]["seconds"], 8)


class Hooks(unittest.TestCase):
    def test_hook_payload(self):
        r = hook.build({"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": "C:/w/app", "tool_name": "Bash",
                        "tool_input": {"command": "python -m pytest -q"}})
        self.assertEqual((r["event"], r["kind"], r["session"]), ("PreToolUse", "test", "s1"))

    def test_setup_leaves_other_hooks_alone(self):
        theirs = {"hooks": [{"type": "command", "command": '"python" "C:/x/divo-sys/divo.py" hook'}]}
        data = {"hooks": {"Stop": [theirs]}, "model": "x"}
        setup_cmd.merge_hooks(data, command='"python" "C:/x/leo-sys/leo.py" hook')
        self.assertIn(theirs, data["hooks"]["Stop"])
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        self.assertEqual(setup_cmd.remove_hooks(data), len(setup_cmd.HOOK_EVENTS))
        self.assertEqual(data["hooks"]["Stop"], [theirs])
        self.assertEqual(data["model"], "x")


class EngineTests(unittest.TestCase):
    def ev(self, e, name, sid="s1", **extra):
        return e.handle({"cmd": "event", "event": name, "session": sid, "cwd": "C:/w/app", **extra}, "hook")

    def test_nothing_open_hands_the_keyboard_back(self):
        e, clock = engine()
        m, k = e.compose(clock())
        self.assertIsNone(m)
        self.assertIsNone(k)

    def test_working_shows_the_tool_and_lights_keys(self):
        e, clock = engine(scene={"claude_takes_over": "always"})
        self.ev(e, "UserPromptSubmit")
        self.ev(e, "PreToolUse", tool="Edit", kind="edit")
        m, k = e.compose(clock())
        self.assertIsNotNone(m)
        self.assertEqual(len(k), 69)
        self.assertIn("editing", e.shown["label"].lower())

    def test_attention_beats_working_across_sessions(self):
        e, clock = engine()
        self.ev(e, "PreToolUse", "a", tool="Read", kind="read")
        self.ev(e, "Notification", "b", ntype="permission_prompt")
        e.compose(clock())
        self.assertIn("needs you", e.shown["label"].lower())

    def test_done_goes_away_after_its_time(self):
        e, clock = engine(scene={"done_seconds": 60, "claude_takes_over": "always"})
        self.ev(e, "Stop")
        self.assertIsNotNone(e.compose(clock())[0])
        clock.t += 61
        self.assertIsNone(e.compose(clock())[0])

    def test_master_switch_releases_everything(self):
        e, clock = engine(claude={"enabled": False})
        self.ev(e, "PreToolUse", tool="Edit", kind="edit")
        m, k = e.compose(clock())
        self.assertEqual((m, k), (None, None))

    def test_manual_face_holds_then_returns(self):
        e, clock = engine(scene={"hold_seconds": 30})
        self.assertTrue(e.handle({"cmd": "face", "mood": "done"}, "gui")["ok"])
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "gui")
        clock.t += 31
        self.assertIsNone(e.compose(clock())[0])

    def test_claude_cannot_pin(self):
        e, _ = engine()
        self.assertTrue(e.handle({"cmd": "pin", "value": True}, "mcp").get("denied"))

    def test_brightness_scales_colours(self):
        e, clock = engine(brightness=50)
        e.handle({"cmd": "keys", "colour": "#ff0000"}, "gui")
        k = e.compose(clock())[1]
        self.assertEqual(k[0], (127, 0, 0))


class AppsTests(unittest.TestCase):
    def test_clock_draws_the_time(self):
        from leosys import apps
        cfg = settings.defaults()["apps"]
        t = datetime.datetime(2026, 11, 19, 9, 5, 7).timestamp()
        f = apps.clock(t, cfg)
        ref = faces.blank()
        faces.draw_text(ref, "09:05:07", 1, (255, 96, 24))
        self.assertEqual(f[0][:32], ref[0][:32])

    def test_rotation_takes_turns(self):
        from leosys import apps
        items = [{"app": "clock", "seconds": 10}, {"app": "face", "seconds": 5}, {"app": "nope", "seconds": 5}]
        self.assertEqual(apps.rotation_item(3, items), "clock")
        self.assertEqual(apps.rotation_item(12, items), "claude")   # the old Critter app's name still works
        self.assertEqual(apps.rotation_item(16, items), "clock")   # unknown apps are skipped

    def test_empty_message_hands_back(self):
        from leosys import apps
        cfg = settings.defaults()["apps"]
        cfg["matrix"] = "message"
        self.assertEqual(apps.matrix_frame(0, cfg), (None, None))


class Layers(unittest.TestCase):
    def test_app_shows_when_claude_is_quiet_and_claude_wins_when_busy(self):
        e, clock = engine(apps={"matrix": "clock"}, scene={"claude_takes_over": "always"})
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "app")
        e.handle({"cmd": "event", "event": "PreToolUse", "session": "s", "tool": "Edit", "kind": "edit"}, "hook")
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "hook")

    def test_by_default_a_busy_claude_leaves_your_app_and_only_alerts_take_over(self):
        e, clock = engine(apps={"matrix": "clock"}, keys={"base": "solid", "colour": "#00ff00"})
        for name, extra in (("UserPromptSubmit", {}), ("PreToolUse", {"tool": "Edit", "kind": "edit"})):
            e.handle({"cmd": "event", "event": name, "session": "s1", "cwd": "C:/w", **extra}, "hook")
        k = e.compose(clock())[1]
        self.assertEqual(e.shown["kind"], "app")
        self.assertEqual(k[0], (0, 255, 0))                     # the working wave does not take the keys either
        e.handle({"cmd": "event", "event": "Notification", "session": "s1", "cwd": "C:/w"}, "hook")
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "hook")               # needs you: Claude takes over
        e.settings.patch({"scene": {"claude_takes_over": "never"}})
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "app")

    def test_the_claude_app_gives_claude_full_priority(self):
        e, clock = engine(apps={"matrix": "rotation", "rotation": [{"app": "clock", "seconds": 5},
                                                                   {"app": "claude", "seconds": 5}]})
        e.compose(clock())
        for name, extra in (("UserPromptSubmit", {}), ("PreToolUse", {"tool": "Edit", "kind": "edit"})):
            e.handle({"cmd": "event", "event": name, "session": "s1", "cwd": "C:/w", **extra}, "hook")
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "hook")               # busy: Claude beats the clock in the rotation
        e.settings.patch({"apps": {"matrix": "claude"}})
        e.handle({"cmd": "event", "event": "Stop", "session": "s1", "cwd": "C:/w"}, "hook")
        clock.t += 120                                          # done has passed: quiet, so the app shows the critter
        f = e.compose(clock())[0]
        self.assertEqual(e.shown["kind"], "app")
        self.assertIsNotNone(f)

    def test_apps_run_even_when_claude_is_switched_off(self):
        e, clock = engine(apps={"matrix": "face"}, claude={"enabled": False})
        self.assertIsNotNone(e.compose(clock())[0])

    def test_key_base_and_caps_indicator(self):
        from leosys import winstate
        e, clock = engine(keys={"base": "solid", "colour": "#00ff00"})
        real = winstate.caps_on
        try:
            winstate.caps_on = lambda: True
            k = e.compose(clock())[1]
        finally:
            winstate.caps_on = real
        self.assertEqual(k[0], (0, 255, 0))
        self.assertEqual(k[keys.BY_NAME["CapsLock"]], faces.WHITE)

    def test_own_lighting_means_nothing_sent(self):
        e, clock = engine()
        self.assertIsNone(e.compose(clock())[1])


class Monitor(unittest.TestCase):
    """The PC monitor apps, on made-up readings (no Windows counters are touched)."""

    def stats(self):
        from leosys.pcstats import FakeMonitor
        return FakeMonitor(clock=lambda: 5000.0).snapshot()

    def test_every_monitor_app_draws_a_full_frame_with_and_without_readings(self):
        from leosys import apps
        for name, app in apps.MATRIX_APPS.items():
            if "needs" not in app:
                continue
            for st in (self.stats(), None, {"values": {}, "history": {}}):
                f = app["frame"](5000.0, {"monitor": {"net_bits": True}}, st)
                self.assertEqual((len(f), len(f[0])), (5, 63), name)

    def test_speed_text_fits_four_characters(self):
        from leosys import apps
        cases = {0: "0K", 1250: "10K", 150_000: "1.2M", 1_500_000: "12M", 15_000_000: "120M", 187_500_000: "1.5G"}
        for b, want in cases.items():
            self.assertEqual(apps.rate_text(b), want)
        self.assertEqual(apps.rate_text(150_000, bits=False), "150K")
        for b in (1, 999, 124_999, 1_249_999, 9_999_999_999):
            self.assertLessEqual(len(apps.rate_text(b)), 4)

    def test_rotation_reads_everything_it_will_show(self):
        from leosys import apps
        cfg = {"matrix": "rotation", "rotation": [{"app": "pc", "seconds": 5}, {"app": "ping", "seconds": 5},
                                                  {"app": "clock", "seconds": 5}]}
        self.assertEqual(apps.needs(cfg), {"cpu", "ram", "gpu", "disk", "ping"})
        self.assertEqual(apps.needs({"matrix": "clock", "rotation": []}), set())

    def test_lost_ping_is_red(self):
        from leosys import apps
        f = apps.ping(0, {}, {"values": {"ping": None}, "history": {"ping": [20.0, None]}})
        self.assertEqual(f[0][62], faces.RED)     # the column of the lost ping, at the right edge of the graph
        self.assertEqual(f[0][45], faces.RED)

    def test_ping_host_is_checked(self):
        from leosys.pcstats import clean_host
        self.assertEqual(clean_host("8.8.8.8"), "8.8.8.8")
        self.assertEqual(clean_host("dns.google"), "dns.google")
        for bad in ("", "a b", "x;rm", "http://x", "ه.com", "a" * 300):
            self.assertEqual(clean_host(bad), "1.1.1.1")

    def test_engine_reads_only_while_a_monitor_app_is_picked(self):
        e, clock = engine(apps={"matrix": "pc"})
        frame = e.compose(clock())[0]
        self.assertIsNotNone(frame)
        self.assertEqual(e.state()["pc"]["active"], ["cpu", "ram", "gpu", "disk"])
        e.settings.patch({"apps": {"matrix": "clock"}})
        clock.t += 10
        e.compose(clock())
        self.assertEqual(e.state()["pc"]["active"], [])


class Media(unittest.TestCase):
    """Now playing, the level meter, the volume and mic signs, and Claude's media tools, on a made-up player."""

    def test_font_text_keeps_latin_and_drops_what_the_matrix_cannot_draw(self):
        from leosys import apps
        self.assertEqual(apps.font_text("Café del Mar"), "CAFE DEL MAR")
        self.assertEqual(apps.font_text("سعيد"), "")          # Arabic: not drawable on 3 x 5
        self.assertEqual(apps.media_text({"title": "سعيد", "artist": "Abyusif", "app": "Chrome"}), "ABYUSIF")
        self.assertEqual(apps.media_text({"title": "سعيد", "artist": "", "app": "Chrome"}), "CHROME")

    def test_media_apps_draw_full_frames(self):
        from leosys import apps
        from leosys.media import FakeMediaWatch
        st = {"media": FakeMediaWatch(clock=lambda: 50.0).snapshot()}
        for name in ("now", "level"):
            for stats in (st, None, {"media": {}}):
                f = apps.MATRIX_APPS[name]["frame"](50.0, {"media": {"colour": "#00c8ff"}}, stats)
                self.assertEqual((len(f), len(f[0])), (5, 63), name)

    def test_media_tools_skip_pin_and_quiet_hours_but_obey_the_switches(self):
        cfg = settings.defaults()["claude"]
        cfg["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59"}
        noon = datetime.datetime(2026, 1, 1, 12, 0)
        self.assertTrue(policy.decide(cfg, {"cmd": "media", "action": "next"}, "mcp", pinned=True, now=noon)[0])
        self.assertFalse(policy.decide(cfg, {"cmd": "media", "action": "next"}, "hook")[0])
        cfg["tools"]["media_control"] = False
        self.assertFalse(policy.decide(cfg, {"cmd": "media", "action": "next"}, "mcp")[0])
        self.assertTrue(policy.decide(cfg, {"cmd": "nowplaying"}, "mcp")[0])
        cfg["enabled"] = False
        self.assertFalse(policy.decide(cfg, {"cmd": "nowplaying"}, "mcp")[0])

    def test_engine_answers_now_playing_and_runs_controls(self):
        e, clock = engine()
        r = e.handle({"cmd": "nowplaying"}, "mcp")
        self.assertEqual(r["now"]["title"], "Sample song")
        r = e.handle({"cmd": "media", "action": "set_volume", "value": 25}, "mcp")
        self.assertTrue(r["ok"])
        self.assertEqual(e.media.state["volume"], 25)
        self.assertFalse(e.handle({"cmd": "media", "action": "explode"}, "mcp")["ok"])
        self.assertEqual(e.log[0]["action"], "media · explode")

    def test_volume_shows_on_the_number_row_then_goes(self):
        e, clock = engine()                        # keys on the keyboard's own lighting
        e.media.state.update(volume=40, volume_changed=clock())
        k = e.compose(clock())[1]
        self.assertEqual(k[keys.BY_NAME["$4"]], faces.WHITE)
        self.assertEqual(k[keys.BY_NAME["%5"]], faces.OFF)
        e.media.state["muted"] = True
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME[")0"]], faces.RED)
        clock.t += 3
        self.assertIsNone(e.compose(clock())[1])   # back to the keyboard's own lighting

    def test_muted_microphone_lights_its_key(self):
        e, clock = engine(keys={"base": "solid", "colour": "#00ff00"})
        e.media.state["mic_muted"] = True
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["M"]], faces.RED)
        e.settings.patch({"keys": {"indicators": {"mic": False}}})
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["M"]], (0, 255, 0))

    def test_mcp_media_request(self):
        self.assertEqual(mcp_server.build_request("media_control", {"action": "set_volume", "value": 30}),
                         {"cmd": "media", "action": "set_volume", "value": 30})
        self.assertEqual(mcp_server.build_request("now_playing", {}), {"cmd": "nowplaying"})


class Colour(unittest.TestCase):
    def test_led_correction_keeps_orange_deep_and_never_turns_a_lit_channel_off(self):
        lut = faces.led_lut(2.2)
        self.assertEqual(faces.led_colour(faces.ORANGE, lut), (255, 30, 1))
        self.assertEqual((lut[0], lut[1], lut[255]), (0, 1, 255))
        self.assertEqual(faces.led_lut(1.0)[96], 96)            # 1 = off
        self.assertEqual(faces.led_lut(9)[96], faces.led_lut(3)[96])   # out of range is clamped


@unittest.skipUnless(os.name == "nt", "pictures are decoded by Windows")
class AlbumTests(unittest.TestCase):
    def folder(self, **files):
        """A temp folder holding: name -> ("png", colour-by-x function) or ("gif", frames, delay_cs) or raw bytes."""
        d = Path(tempfile.mkdtemp(prefix="leo-album-"))
        for name, spec in files.items():
            if isinstance(spec, bytes):
                (d / name).write_bytes(spec)
            elif spec[0] == "png":
                write_png(d / name, 4, 2, spec[1])
            else:
                write_gif(d / name, 4, 2, spec[1], spec[2])
        return d

    RED_BLUE = ("png", lambda x, y: (255, 0, 0) if x < 2 else (0, 0, 255))

    def test_scan_sorts_names_naturally_and_notices_changes(self):
        from leosys import albums
        d = self.folder(**{"10.png": self.RED_BLUE, "2.png": self.RED_BLUE, "1.png": self.RED_BLUE, "note.txt": b"x"})
        files, sig = albums.scan(d)
        self.assertEqual([Path(f).name for f in files], ["1.png", "2.png", "10.png"])
        write_png(d / "2.png", 8, 4, lambda x, y: (0, 255, 0))
        self.assertNotEqual(albums.scan(d)[1], sig)
        with self.assertRaises(albums.AlbumError):
            albums.scan(d / "nope")
        with self.assertRaises(albums.AlbumError):
            albums.scan("")

    def test_picture_fits(self):
        from leosys import wic
        d = self.folder(**{"a.png": self.RED_BLUE})
        stretch = wic.decode(d / "a.png", 63, 5, "stretch")[0][0]
        self.assertEqual(tuple(stretch[:3]), (0, 0, 255))                 # BGRA: red
        self.assertEqual(tuple(stretch[-4:-1]), (255, 0, 0))              # blue at the far right
        contain = wic.decode(d / "a.png", 240, 135, "contain")[0][0]
        self.assertEqual(tuple(contain[:4]), (0, 0, 0, 0))                # black bars above and below
        cover = wic.decode(d / "a.png", 240, 135, "cover")[0][0]
        self.assertEqual(tuple(cover[:3]), (0, 0, 255))                   # filled, no bars

    def test_gif_frames_and_delays(self):
        from leosys import wic
        d = self.folder(**{"g.gif": ("gif", [[0] * 8, [1] * 8], 20)})
        frames = wic.decode(d / "g.gif", 63, 5, "stretch")
        self.assertEqual([ms for _, ms in frames], [200, 200])
        self.assertEqual(tuple(frames[0][0][:3]), (0, 0, 255))            # red, then blue
        self.assertEqual(tuple(frames[1][0][:3]), (255, 0, 0))

    def test_matrix_album_plays_stills_and_loops_gifs(self):
        from leosys import albums
        d = self.folder(**{"1.png": self.RED_BLUE, "2.gif": ("gif", [[0] * 8, [1] * 8], 50)})
        files, _ = albums.scan(d)
        data = albums.build_matrix(files, {"fit": "stretch", "seconds": 3})
        self.assertEqual(data["images"], 2)
        self.assertAlmostEqual(data["total"], 3 + 3 * 1.0, places=2)        # a 3 s still, then the 1 s GIF three times
        grid = albums.rows_of_at(data, 0.5)
        self.assertEqual((len(grid), len(grid[0])), (5, 63))
        self.assertEqual(grid[0][0], (255, 0, 0))
        self.assertEqual(grid[0][62], (0, 0, 255))
        self.assertEqual(albums.rows_of_at(data, 3.2)[0][0], (255, 0, 0))   # the GIF's first frame
        self.assertEqual(albums.rows_of_at(data, 3.7)[0][0], (0, 0, 255))   # its second



class NeverTheLcd(unittest.TestCase):
    """His rule (2026-11-19): leo-sys only touches the keys and the matrix. Nothing may be able to write the LCD."""

    def test_no_lcd_code_exists(self):
        import pkgutil
        import leosys
        names = {m.name for m in pkgutil.iter_modules(leosys.__path__)}
        self.assertFalse({"lcd", "lcdart", "timesync"} & names)
        from leosys.device import DemoKeyboard, Keyboard
        for cls in (Keyboard, DemoKeyboard):
            self.assertFalse(hasattr(cls, "lcd"))
        for f in Path(leosys.__file__).parent.glob("*.py"):
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("x04\\x72", text, f.name)      # op 0x72 is the LCD upload
            self.assertNotIn("0x04, 0x72", text, f.name)
            self.assertNotIn("x04\\x28", text, f.name)      # op 0x28 sets the clock the LCD watch shows

    def test_the_engine_refuses_lcd_commands_and_has_no_lcd_settings(self):
        e, clock = engine()
        self.assertFalse(e.handle({"cmd": "lcd", "mood": "idle"}, "gui")["ok"])
        self.assertFalse(e.handle({"cmd": "lcd", "mood": "album"}, "mcp").get("ok"))
        self.assertNotIn("lcd", settings.defaults())
        self.assertNotIn("lcd", settings.defaults()["albums"])
        self.assertNotIn("time_sync", settings.defaults())          # the keyboard clock is the LCD watch's: not touched
        self.assertFalse(e.handle({"cmd": "synctime"}, "gui")["ok"])


class Modules(unittest.TestCase):
    def test_every_module_imports(self):
        """The tests above do not import the server or the daemon; a typo there only shows when the service starts."""
        import importlib
        import pkgutil
        import leosys
        for m in pkgutil.iter_modules(leosys.__path__):
            importlib.import_module(f"leosys.{m.name}")


class SmartKeys(unittest.TestCase):
    ALL = {idx: (0, 255, 0) for _, idx, _, _ in keys.KEYS}
    CFG = {"enabled": True, "modifiers": True, "program_hint": False}

    def sc(self):
        return smartkeys.load(None)

    def idx(self, name):
        return keys.BY_NAME[name]

    def test_key_names_and_combos(self):
        self.assertEqual([smartkeys.key_name(t) for t in ("c", "1", "pgup", "Del", "[", "'", "Esc", "nope")],
                         ["C", "!1", "page up", "del", "{[", "\"'", "Esc", None])
        self.assertEqual(smartkeys.combo_name({"shift", "ctrl"}), "ctrl+shift")
        self.assertEqual(smartkeys.combo_name({"win", "alt", "ctrl", "shift"}), "ctrl+alt+shift+win")

    def test_the_built_in_maps_are_valid(self):
        generic, apps, categories = smartkeys.builtin()                    # raises if a key or combo is wrong
        self.assertTrue({"ctrl", "alt", "shift", "win"} <= set(generic))
        self.assertEqual(set(apps), {"vscode", "browser", "explorer", "photoshop"})
        for a in apps.values():
            self.assertTrue(a["exes"] and a["combos"]["ctrl"])
        self.assertEqual(set(categories), set(smartkeys.LABELS))

    def test_holding_ctrl_lights_only_the_keys_with_a_shortcut(self):
        out = smartkeys.overlay(self.ALL, frozenset({"ctrl"}), None, self.sc(), self.CFG)
        self.assertEqual(out[self.idx("C")], smartkeys.CATEGORIES["edit"])
        self.assertEqual(out[self.idx("S")], smartkeys.CATEGORIES["file"])
        self.assertEqual(out[self.idx("W")], smartkeys.CATEGORIES["danger"])
        self.assertEqual(out[self.idx("Ctrl_L")], faces.WHITE)              # the held key itself
        self.assertEqual(out[self.idx("Q")], faces.dim((0, 255, 0), smartkeys.DARK))   # no shortcut: nearly dark
        self.assertEqual(len(out), len(keys.KEYS))

    def test_a_programs_own_map_adds_to_the_generic_one(self):
        sc = self.sc()
        browser = sc.app_for("Chrome.EXE")                                   # file names are matched without case
        self.assertEqual(browser["name"], "Browser")
        out = smartkeys.overlay(self.ALL, frozenset({"ctrl"}), browser, sc, self.CFG)
        self.assertEqual(out[self.idx("T")], smartkeys.CATEGORIES["file"])   # new tab: only the browser has it
        self.assertEqual(out[self.idx("C")], smartkeys.CATEGORIES["edit"])   # copy: generic still there
        plain = smartkeys.overlay(self.ALL, frozenset({"ctrl"}), None, sc, self.CFG)
        self.assertEqual(plain[self.idx("T")], faces.dim((0, 255, 0), smartkeys.DARK))
        self.assertIsNone(sc.app_for("unknown.exe"))

    def test_the_windows_key_has_its_own_set(self):
        out = smartkeys.overlay(self.ALL, frozenset({"win"}), None, self.sc(), self.CFG)
        self.assertEqual(out[self.idx("L")], smartkeys.CATEGORIES["danger"])  # lock
        self.assertEqual(out[self.idx("D")], smartkeys.CATEGORIES["tool"])    # desktop
        self.assertEqual(out[self.idx("Win_L")], faces.WHITE)

    def test_nothing_to_show_leaves_the_base_alone(self):
        sc = self.sc()
        self.assertIs(smartkeys.overlay(self.ALL, frozenset({"ctrl", "alt", "shift"}), None, sc, self.CFG), self.ALL)
        self.assertIs(smartkeys.overlay(self.ALL, frozenset(), None, sc, self.CFG), self.ALL)
        off = dict(self.CFG, modifiers=False)
        self.assertIs(smartkeys.overlay(self.ALL, frozenset({"ctrl"}), None, sc, off), self.ALL)

    def test_on_the_keyboards_own_lighting_a_held_combo_still_shows_on_dark_keys(self):
        out = smartkeys.overlay(None, frozenset({"ctrl"}), None, self.sc(), self.CFG)
        self.assertEqual(out[self.idx("C")], smartkeys.CATEGORIES["edit"])
        self.assertEqual(out[self.idx("Q")], faces.OFF)
        self.assertIsNone(smartkeys.overlay(None, frozenset(), None, self.sc(), self.CFG))

    def test_the_program_hint_is_soft_and_never_over_the_keyboards_own_lighting(self):
        sc = self.sc()
        vscode = sc.app_for("code.exe")
        hint = dict(self.CFG, program_hint=True)
        out = smartkeys.overlay(self.ALL, frozenset(), vscode, sc, hint)
        self.assertEqual(out[self.idx("P")], faces.dim(smartkeys.CATEGORIES["nav"], smartkeys.HINT_KEYS))
        self.assertEqual(out[self.idx("Q")], faces.dim((0, 255, 0), smartkeys.HINT_BASE))
        self.assertIsNone(smartkeys.overlay(None, frozenset(), vscode, sc, hint))
        self.assertIs(smartkeys.overlay(self.ALL, frozenset(), None, sc, hint), self.ALL)   # no map: nothing

    def test_your_own_json_adds_programs_and_reports_mistakes(self):
        d = Path(tempfile.mkdtemp())
        (d / "shortcuts.json").write_text(json.dumps({
            "categories": {"edit": "#112233", "oops": "red"},
            "apps": {"obs": {"name": "OBS", "exes": ["OBS64.exe"], "keys": {
                "ctrl": {"tool": "R S Banana", "nowhere": "A"}, "hyper": {"tool": "A"}}},
                "broken": {"name": "No exes"}}}), encoding="utf-8")
        sc = smartkeys.load(d / "shortcuts.json")
        obs = sc.app_for("obs64.exe")
        self.assertEqual((obs["name"], obs["combos"]["ctrl"][self.idx("R")]), ("OBS", "tool"))
        self.assertEqual(sc.categories["edit"], (0x11, 0x22, 0x33))
        text = " ".join(sc.problems)
        for word in ("Banana", "nowhere", "hyper", "oops", "broken"):
            self.assertIn(word, text)
        self.assertEqual(sc.app_for("code.exe")["name"], "VS Code")        # the built-ins are still there

    def test_a_broken_file_falls_back_to_the_built_ins_and_says_so(self):
        d = Path(tempfile.mkdtemp())
        (d / "shortcuts.json").write_text("{not json", encoding="utf-8")
        sc = smartkeys.load(d / "shortcuts.json")
        self.assertEqual(sc.app_for("chrome.exe")["name"], "Browser")
        self.assertIn("could not be read", sc.problems[0])

    def test_the_example_file_is_valid_and_never_overwrites(self):
        lib = smartkeys.Library(Path(tempfile.mkdtemp()) / "shortcuts.json", clock=lambda: 0.0)
        path, created = lib.write_example()
        self.assertTrue(created and path.is_file())
        self.assertEqual(lib.current().problems, [])
        self.assertEqual(lib.current().app_for("notepad.exe")["name"], "Notepad")
        path.write_text('{"apps": {}}', encoding="utf-8")
        self.assertEqual(lib.write_example(), (path, False))

    def test_the_library_notices_a_changed_file(self):
        now = [0.0]
        d = Path(tempfile.mkdtemp())
        lib = smartkeys.Library(d / "shortcuts.json", clock=lambda: now[0])
        self.assertIsNone(lib.current().app_for("obs64.exe"))
        (d / "shortcuts.json").write_text(json.dumps({"apps": {"obs": {"exes": ["obs64.exe"], "keys": {}}}}), encoding="utf-8")
        self.assertIsNone(lib.current().app_for("obs64.exe"))              # not looked at again yet
        now[0] = 10.0
        self.assertEqual(lib.current().app_for("obs64.exe")["id"], "obs")

    def test_windows_readings_have_the_right_shape(self):
        mods, exe = winstate.input_state()
        self.assertIsInstance(mods, frozenset)
        self.assertLessEqual(mods, {"ctrl", "alt", "shift", "win"})
        self.assertIsInstance(exe, str)


class SmartKeysInTheEngine(unittest.TestCase):
    def held(self, mods, exe="", **over):
        keys_cfg = dict({"base": "solid", "colour": "#00ff00"}, **over.pop("keys", {}))
        e, clock = engine(keys=keys_cfg, **over)
        e.probe = lambda: (frozenset(mods), exe)
        return e, clock

    def test_holding_ctrl_in_a_browser_lights_its_shortcuts_then_releasing_restores_the_base(self):
        e, clock = self.held({"ctrl"}, "chrome.exe")
        k = e.compose(clock())[1]
        self.assertEqual(k[keys.BY_NAME["T"]], smartkeys.CATEGORIES["file"])
        s = e.state()["smart"]
        self.assertEqual((s["app"], s["mods"], s["active"]), ("Browser", ["ctrl"], True))
        e.probe = lambda: (frozenset(), "chrome.exe")
        k = e.compose(clock())[1]
        self.assertEqual(k[keys.BY_NAME["T"]], (0, 255, 0))                # back to the solid base
        self.assertFalse(e.state()["smart"]["active"])

    def test_on_the_keyboards_own_lighting_it_hands_the_keys_back_after_the_key_is_released(self):
        e, clock = engine()
        e.probe = lambda: (frozenset({"win"}), "")
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["D"]], smartkeys.CATEGORIES["tool"])
        e.probe = lambda: (frozenset(), "")
        self.assertIsNone(e.compose(clock())[1])

    def test_it_can_be_switched_off(self):
        e, clock = self.held({"ctrl"}, "chrome.exe", keys={"smart": {"enabled": False}})
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["T"]], (0, 255, 0))
        e, clock = self.held({"ctrl"}, "chrome.exe", keys={"smart": {"modifiers": False}})
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["T"]], (0, 255, 0))

    def test_claudes_effects_and_picked_colours_come_first(self):
        e, clock = self.held({"ctrl"}, "chrome.exe", scene={"claude_takes_over": "always"})
        for name, extra in (("UserPromptSubmit", {}), ("PreToolUse", {"tool": "Edit", "kind": "edit"})):
            e.handle({"cmd": "event", "event": name, "session": "s1", "cwd": "C:/w", **extra}, "hook")
        e.compose(clock())
        self.assertFalse(e.state()["smart"]["active"])                     # Claude's working wave is not overdrawn
        e2, clock2 = self.held({"ctrl"}, "chrome.exe")
        e2.handle({"cmd": "keys", "colour": "#ff0000"}, "gui")
        e2.compose(clock2())
        self.assertFalse(e2.state()["smart"]["active"])

    def test_brightness_still_applies(self):
        e, clock = self.held({"ctrl"}, "chrome.exe", brightness=50)
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["T"]], faces.dim(smartkeys.CATEGORIES["file"], 0.5))


class ScreenColours(unittest.TestCase):
    @staticmethod
    def grid(fn):
        """A 32 x 18 grid of RGB bytes: fn(x, y) -> (r, g, b)."""
        return bytes(c for y in range(screen.GRID_H) for x in range(screen.GRID_W) for c in fn(x, y))

    @staticmethod
    def row_keys(row):
        return sorted((x, idx) for _, idx, r, x in keys.KEYS if r == row)

    def test_edges_mode_gives_each_key_the_colour_at_its_nearest_edge(self):
        left_right = self.grid(lambda x, y: (255, 0, 0) if x < 16 else (0, 0, 255))
        out = screen.key_colours(left_right, "edges", saturation=1.0, gain=1.0)
        middle = self.row_keys(2)
        self.assertEqual(out[middle[0][1]], (255, 0, 0))             # the leftmost key of a middle row: the left edge
        self.assertEqual(out[middle[-1][1]], (0, 0, 255))            # the rightmost: the right edge
        top_bottom = self.grid(lambda x, y: (0, 255, 0) if y < 9 else (255, 255, 0))
        out = screen.key_colours(top_bottom, "edges", saturation=1.0, gain=1.0)
        self.assertEqual({out[i] for _, i in self.row_keys(0)}, {(0, 255, 0)})      # the top row: the top edge
        self.assertEqual({out[i] for _, i in self.row_keys(4)}, {(255, 255, 0)})    # the bottom row: the bottom edge

    def test_mirror_mode_is_a_tiny_picture_of_the_screen(self):
        grid = self.grid(lambda x, y: (255, 0, 0) if x < 16 else (0, 0, 255))
        out = screen.key_colours(grid, "mirror", saturation=1.0, gain=1.0)
        row = self.row_keys(2)
        self.assertEqual(out[row[0][1]], (255, 0, 0))
        self.assertEqual(out[row[-1][1]], (0, 0, 255))
        self.assertEqual(len(out), len(keys.KEYS))
        top = self.grid(lambda x, y: (0, 255, 0) if y < 9 else (255, 255, 0))
        mirror = screen.key_colours(top, "mirror", saturation=1.0, gain=1.0)
        self.assertEqual(mirror[self.row_keys(0)[3][1]], (0, 255, 0))
        self.assertEqual(mirror[self.row_keys(4)[3][1]], (255, 255, 0))

    def test_boost_makes_dull_screens_vivid_and_stays_in_range(self):
        import colorsys
        self.assertEqual(screen.boost((200, 100, 50), 1.0, 1.0), (200, 100, 50))
        dull = (200, 150, 150)
        s_before = colorsys.rgb_to_hsv(*(c / 255 for c in dull))[1]
        s_after = colorsys.rgb_to_hsv(*(c / 255 for c in screen.boost(dull, 2.0)))[1]
        self.assertGreater(s_after, s_before)
        self.assertEqual(max(screen.boost((200, 100, 50), 1.0, 3.0)), 255)             # brightness is capped
        self.assertEqual(screen.boost((10, 10, 10), 99, 99), (255, 255, 255))           # a grey has no hue to boost
        self.assertTrue(all(0 <= c <= 255 for c in screen.boost((255, 0, 0), 3.0, 3.0)))

    def test_smoothing_mixes_the_new_grid_into_the_old_one(self):
        old, new = bytes([0, 100, 200]), bytes([100, 100, 0])
        self.assertEqual(screen.blend(None, new, 0.5), new)
        self.assertEqual(screen.blend(old, new, 0), new)
        mixed = list(screen.blend(old, new, 0.5))
        self.assertEqual(mixed[1], 100)                                                # an unchanged channel stays put
        self.assertTrue(50 < mixed[0] < 100 and 100 < mixed[2] < 200)                  # linear light: brighter than the plain middle
        self.assertEqual(list(screen.blend(old, new, 5)), list(screen.blend(old, new, 0.95)))   # the amount is capped at 0.95
        self.assertEqual(list(screen.blend(old, old, 0.5)), list(old))                 # nothing changing changes nothing

    def test_colours_are_mixed_as_light_not_as_encoded_numbers(self):
        # half red, half green: light adds up, so the mix is brighter than the middle of the two encoded values (128, 128, 0)
        grid = self.grid(lambda x, y: (255, 0, 0) if x % 2 else (0, 255, 0))
        r, g, b = screen.sample(grid, 0.5, 0.5, rx=3, ry=3)
        self.assertGreater(r, 150)
        self.assertGreater(g, 150)
        self.assertAlmostEqual(b, 0, delta=1)
        self.assertAlmostEqual(screen.to_srgb(screen._LINEAR[200]), 200, delta=0.01)    # encoding and decoding agree
        flat = self.grid(lambda x, y: (40, 120, 220))
        self.assertEqual([round(v) for v in screen.sample(flat, 0.3, 0.6)], [40, 120, 220])   # a flat colour stays itself

    def test_a_key_averages_its_patch_of_the_screen_not_one_cell(self):
        grid = self.grid(lambda x, y: (255, 255, 255) if (x, y) == (16, 9) else (0, 0, 0))   # one bright cell in a dark screen
        centre = screen.sample(grid, 16 / 31, 9 / 17)
        beside = screen.sample(grid, 20 / 31, 9 / 17)
        self.assertTrue(0 < centre[0] < 255)                                           # spread over the key, not a single hot cell
        self.assertEqual(beside, [0.0, 0.0, 0.0])

    def test_white_balance_scales_each_channel_and_is_off_at_100(self):
        self.assertEqual(screen.balanced((100, 150, 200), (100, 100, 100)), (100, 150, 200))
        self.assertEqual(screen.balanced((100, 150, 200), (100, 50, 200)), (100, 75, 255))   # capped at 255
        grid = self.grid(lambda x, y: (200, 200, 200))
        out = screen.key_colours(grid, "mirror", saturation=1.0, gain=1.0, balance=(100, 100, 50))
        self.assertEqual(set(out.values()), {(200, 200, 100)})

    def test_the_screen_is_read_as_a_small_grid(self):
        try:
            grabber = screen.Grabber()
            data = grabber.read()
            grabber.close()
        except (OSError, AttributeError):
            self.skipTest("no screen to read here")
        self.assertEqual(len(data), screen.GRID_W * screen.GRID_H * 3)

    def test_the_watcher_reads_only_while_wanted_and_keeps_nothing_after(self):
        reads, closed = [], []

        class FakeGrab:
            def read(self):
                reads.append(1)
                return bytes([10]) * (screen.GRID_W * screen.GRID_H * 3)

            def close(self):
                closed.append(1)
        with mock.patch.object(screen, "IDLE_AFTER", 0.4):
            watch = screen.ScreenWatch(grabber=FakeGrab)
            self.assertIsNone(watch.snapshot()["grid"])
            for _ in range(5):                                       # the engine asks every tick while it is in use
                watch.want(fps=15, smooth=0.5)
                time.sleep(0.1)
            self.assertIsNotNone(watch.snapshot()["grid"])
            time.sleep(1.2)                                          # nothing asked for it any more
            n = len(reads)
            self.assertIsNone(watch.snapshot()["grid"])
            self.assertTrue(closed)
            time.sleep(0.4)
            self.assertEqual(len(reads), n)

    def test_a_failed_reading_is_reported_not_raised(self):
        class Broken:
            def read(self):
                raise OSError("the screen is locked")

            def close(self):
                pass
        with mock.patch.object(screen, "IDLE_AFTER", 5):
            watch = screen.ScreenWatch(grabber=Broken)
            watch.want()
            time.sleep(0.4)
            self.assertIn("locked", watch.snapshot()["error"])

    def test_the_screen_key_base_and_the_engine(self):
        self.assertIn("screen", apps.KEY_BASES)
        e, clock = engine(keys={"base": "screen"})
        k = e.compose(clock())[1]
        self.assertEqual(len(k), len(keys.KEYS))
        self.assertGreater(e.screen.wanted, 0)
        self.assertTrue(e.state()["screen"]["on"])
        e2, clock2 = engine(keys={"base": "solid"})
        e2.compose(clock2())
        self.assertEqual(e2.screen.wanted, 0)                        # the screen is not read for any other base
        self.assertFalse(e2.state()["screen"]["on"])
        e3, clock3 = engine(keys={"base": "screen"}, scene={"claude_takes_over": "always"})
        for name, extra in (("UserPromptSubmit", {}), ("PreToolUse", {"tool": "Edit", "kind": "edit"})):
            e3.handle({"cmd": "event", "event": name, "session": "s1", "cwd": "C:/w", **extra}, "hook")
        e3.compose(clock3())
        self.assertEqual(e3.screen.wanted, 0)                        # Claude's effect is showing: no reading either

    def test_no_reading_yet_means_dark_keys_not_a_crash(self):
        self.assertEqual(apps.screen_colours(0, {"screen": {}}, {"screen": {"grid": None, "error": ""}}), {})
        self.assertEqual(apps.screen_colours(0, {}, None), {})


BAGHDAD = (33.32, 44.37)


class PrayerTimes(unittest.TestCase):
    DAY = datetime.date(2026, 11, 19)

    def at(self, method="mwl", asr="standard", offsets=None, day=None, place=BAGHDAD, tz=3):
        return prayer.times(day or self.DAY, place[0], place[1], tz, method, asr, offsets)

    def test_muslim_world_league_matches_a_public_service_for_baghdad(self):
        """Reference: api.aladhan.com, method 3, 19 Nov 2026. Their Asr runs about 2 minutes later than the precise
        solar formula (14:38.3), so Asr is allowed 3; the rest matched to the minute."""
        ref = {"fajr": (5, 11), "sunrise": (6, 37), "dhuhr": (11, 48), "asr": (14, 40), "maghrib": (16, 58), "isha": (18, 20)}
        got = self.at()
        for name, (h, m) in ref.items():
            want = datetime.datetime.combine(self.DAY, datetime.time(h, m))
            slack = 3 if name == "asr" else 1
            self.assertLessEqual(abs((got[name] - want).total_seconds()) / 60, slack, name)

    def test_the_day_runs_in_order(self):
        t = self.at()
        order = [t[k] for k in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")]
        self.assertEqual(order, sorted(order))

    def test_methods_differ_where_they_should(self):
        mwl, jafari, umm = self.at("mwl"), self.at("jafari"), self.at("ummalqura")
        self.assertGreater(jafari["maghrib"], mwl["maghrib"])            # Jafari waits for the sun to be 4 degrees down
        self.assertGreater(jafari["fajr"], mwl["fajr"])                   # and its Fajr angle (16) is shallower than 18
        self.assertEqual(jafari["dhuhr"], mwl["dhuhr"])
        self.assertEqual(umm["isha"] - umm["maghrib"], datetime.timedelta(minutes=90))
        self.assertEqual(set(prayer.METHODS), {"mwl", "jafari", "ummalqura", "egypt", "karachi", "isna", "tehran"})

    def test_hanafi_asr_is_later_and_offsets_move_a_time(self):
        self.assertGreater(self.at(asr="hanafi")["asr"], self.at()["asr"])
        base = self.at()
        moved = self.at(offsets={"fajr": 5, "isha": -3})
        self.assertEqual(moved["fajr"] - base["fajr"], datetime.timedelta(minutes=5))
        self.assertEqual(moved["isha"] - base["isha"], datetime.timedelta(minutes=-3))
        self.assertEqual(moved["asr"], base["asr"])

    def test_places_where_the_sun_does_not_get_that_low_have_no_fajr(self):
        t = prayer.times(datetime.date(2026, 6, 21), 69.65, 18.96, 2, "mwl")      # Tromso, midsummer
        self.assertIsNone(t["fajr"])
        self.assertIsNone(t["isha"])
        self.assertIsNotNone(t["dhuhr"])

    def test_the_next_prayer_rolls_over_to_tomorrows_fajr_after_isha(self):
        shown = prayer.PRAYERS
        name, when, _ = prayer.next_prayer(datetime.datetime(2026, 11, 19, 14, 0), *BAGHDAD, tz_hours=3, enabled=shown)
        self.assertEqual((name, when.strftime("%H:%M")), ("asr", "14:38"))
        name, when, _ = prayer.next_prayer(datetime.datetime(2026, 11, 19, 22, 0), *BAGHDAD, tz_hours=3, enabled=shown)
        self.assertEqual(name, "fajr")
        self.assertEqual(when.date(), datetime.date(2026, 11, 20))
        name, _, _ = prayer.next_prayer(datetime.datetime(2026, 11, 19, 14, 0), *BAGHDAD, tz_hours=3, enabled=("fajr", "isha"))
        self.assertEqual(name, "isha")                                    # prayers switched off are skipped

    def test_the_pcs_time_zone_is_used(self):
        self.assertIsInstance(prayer.local_offset_hours(), float)
        self.assertEqual(prayer.local_offset_hours(datetime.datetime(2026, 11, 19, 12, 0).astimezone(datetime.timezone.utc)), 0.0)


OPEN_METEO = {"current": {"time": "2026-11-19T14:00", "temperature_2m": 26.7, "weather_code": 0, "is_day": 1},
              "hourly": {"time": [f"2026-11-{19 + h // 24}T{h % 24:02d}:00" for h in range(48)],
                         "temperature_2m": [18.0 + (h % 24) * 0.5 for h in range(48)]},
              "daily": {"temperature_2m_max": [26.9, 27.3], "temperature_2m_min": [17.8, 16.1]}}


class Weather(unittest.TestCase):
    def test_the_address_sends_only_rounded_coordinates(self):
        url = weather.build_url(33.31524, 44.36612)
        self.assertIn("latitude=33.32", url)
        self.assertIn("longitude=44.37", url)
        self.assertTrue(url.startswith("https://api.open-meteo.com/"))
        self.assertNotIn("33.3152", url)

    def test_the_answer_is_reduced_to_what_the_matrix_shows(self):
        d = weather.parse(OPEN_METEO)
        self.assertEqual((d["temp"], d["code"], d["day"], d["high"], d["low"]), (26.7, 0, True, 26.9, 17.8))
        self.assertEqual(len(d["hours"]), weather.HOURS)
        self.assertEqual(d["hours"][0], 18.0 + 14 * 0.5)                   # starts at the current hour
        with self.assertRaises(ValueError):
            weather.parse({"current": {}})

    def test_it_asks_only_when_wanted_and_refreshes_every_half_hour(self):
        now, asked = [1000.0], []
        w = weather.WeatherWatch(fetcher=lambda lat, lon: asked.append((lat, lon)) or weather.parse(OPEN_METEO),
                                 clock=lambda: now[0], threads=False)
        self.assertFalse(w.due())                                           # nothing wants it yet
        w.want(33.32, 44.37)
        self.assertTrue(w.due())
        w._step()
        self.assertEqual(len(asked), 1)
        self.assertFalse(w.due())                                           # fresh
        now[0] += weather.REFRESH_S - 1
        w.want(33.32, 44.37)
        self.assertFalse(w.due())
        now[0] += 2
        w.want(33.32, 44.37)
        self.assertTrue(w.due())
        now[0] += weather.IDLE_AFTER + 1                                    # and nobody has used it for a while
        self.assertFalse(w.due())
        self.assertEqual(w.snapshot()["data"]["temp"], 26.7)                # the last good forecast is kept

    def test_a_failure_keeps_the_old_forecast_and_retries_later(self):
        now, fail = [0.0], [False]

        def fetcher(lat, lon):
            if fail[0]:
                raise OSError("no internet")
            return weather.parse(OPEN_METEO)
        w = weather.WeatherWatch(fetcher=fetcher, clock=lambda: now[0], threads=False)
        w.want(33.32, 44.37)
        w._step()
        fail[0] = True
        now[0] += weather.REFRESH_S + 1
        w.want(33.32, 44.37)
        self.assertTrue(w.due())
        w._step()
        snap = w.snapshot()
        self.assertIn("no internet", snap["error"])
        self.assertIsNotNone(snap["data"])
        self.assertFalse(w.due())                                           # not again straight away
        now[0] += weather.RETRY_S + 1
        w.want(33.32, 44.37)
        self.assertTrue(w.due())

    def test_another_city_drops_the_old_forecast(self):
        w = weather.WeatherWatch(fetcher=lambda a, b: weather.parse(OPEN_METEO), clock=lambda: 0.0, threads=False)
        w.want(33.32, 44.37)
        w._step()
        self.assertIsNotNone(w.snapshot()["data"])
        w.want(30.51, 47.78)
        self.assertIsNone(w.snapshot()["data"])

    def test_an_old_forecast_is_flagged_stale(self):
        now = [0.0]
        w = weather.WeatherWatch(fetcher=lambda a, b: weather.parse(OPEN_METEO), clock=lambda: now[0], threads=False)
        w.want(33.32, 44.37)
        w._step()
        now[0] = weather.STALE_S + 1
        self.assertTrue(w.snapshot()["stale"])


class TypingCounts(unittest.TestCase):
    A, B, C, SHIFT, SPACE = 0x41, 0x42, 0x43, 0xA0, 0x20

    def meter(self):
        self.down = set()
        self.now = [1000.0]
        m = typingmeter.TypingMeter(down=lambda vks: self.down & set(vks), clock=lambda: self.now[0], threads=False)
        m.set_enabled(True)
        return m

    def tap(self, m, vk, advance=0.0):
        self.down = {vk}
        m.poll(self.now[0])
        self.down = set()
        m.poll(self.now[0])
        self.now[0] += advance

    def idx(self, name):
        return keys.BY_NAME[name]

    def test_a_press_counts_once_even_when_held(self):
        m = self.meter()
        self.down = {self.A}
        for _ in range(5):
            m.poll(self.now[0])                                          # held down through five looks
        self.assertEqual(m.snapshot()["counts"], {self.idx("A"): 1})
        self.down = set()
        m.poll(self.now[0])
        self.down = {self.A}
        m.poll(self.now[0])
        self.assertEqual(m.snapshot()["counts"], {self.idx("A"): 2})

    def test_speed_counts_characters_not_modifiers_and_best_needs_a_fair_run(self):
        m = self.meter()
        self.tap(m, self.SHIFT)
        self.assertEqual((m.snapshot()["total"], m.snapshot()["wpm"]), (1, 0.0))        # counted as a press, not a character
        for i in range(150):                                              # 150 characters spread over 29 seconds
            self.tap(m, self.A if i % 2 else self.SPACE, advance=29.0 / 150)
        snap = m.snapshot(self.now[0])
        self.assertAlmostEqual(snap["wpm"], 62.0, delta=3)                # 150 characters = 30 words over about 29 seconds
        self.assertGreater(snap["best"], 50)
        self.now[0] += 120                                                # two quiet minutes later: speed falls to zero
        later = m.snapshot(self.now[0])
        self.assertEqual(later["wpm"], 0.0)
        self.assertEqual(later["best"], snap["best"])                    # the session's best stays

    def test_a_short_burst_does_not_set_a_silly_best(self):
        m = self.meter()
        for _ in range(4):
            self.tap(m, self.A, advance=0.05)
        self.assertEqual(m.snapshot()["best"], 0.0)

    def test_the_graph_has_ten_second_stretches_and_moves_on_with_time(self):
        m = self.meter()
        self.now[0] = 1000.0
        for _ in range(6):
            self.tap(m, self.A)
        self.now[0] = 1015.0                                              # a later stretch
        for _ in range(3):
            self.tap(m, self.B)
        buckets = m.snapshot(self.now[0])["buckets"]
        self.assertEqual(len(buckets), typingmeter.BUCKETS)
        self.assertEqual(buckets[-1], 3)
        self.assertEqual(buckets[-2], 6)
        self.now[0] = 1015.0 + 10 * 60
        self.assertEqual(sum(m.snapshot(self.now[0])["buckets"]), 0)       # six quiet minutes later it has scrolled away

    def test_switching_off_or_resetting_wipes_everything_and_off_counts_nothing(self):
        m = self.meter()
        for _ in range(20):
            self.tap(m, self.A, advance=0.1)
        m.reset()
        snap = m.snapshot(self.now[0])
        self.assertEqual((snap["counts"], snap["total"], snap["best"], sum(snap["buckets"])), ({}, 0, 0.0, 0))
        for _ in range(20):
            self.tap(m, self.B, advance=0.1)
        m.set_enabled(False)
        snap = m.snapshot()
        self.assertEqual((snap["enabled"], snap["counts"], snap["total"], snap["wpm"]), (False, {}, 0, 0.0))
        self.tap(m, self.C)
        self.assertEqual(m.snapshot()["total"], 0)                         # off means off: nothing is counted

    def test_only_counts_exist_nothing_in_order_or_with_a_time(self):
        m = self.meter()
        for vk in (self.A, self.B, self.A):
            self.tap(m, vk, advance=1.0)
        snap = m.snapshot(self.now[0])
        self.assertEqual(set(snap), {"enabled", "wpm", "best", "total", "counts", "buckets"})
        self.assertTrue(all(isinstance(v, int) for v in snap["counts"].values()))
        self.assertTrue(all(isinstance(v, int) for v in snap["buckets"]))
        self.assertFalse([name for name in vars(m) if name in ("history", "log", "text", "sequence", "events", "seconds")])
        self.assertEqual(len(m.buckets), typingmeter.BUCKETS)               # the only time information: ten-second tallies

    def test_every_key_the_keyboard_has_is_known(self):
        names = {n for n, _, _, _ in keys.KEYS}
        self.assertEqual(names - set(typingmeter.VK), {"Fn"})            # the Fn key does not reach Windows
        self.assertFalse(typingmeter.TEXT_IDX & {self.idx(n) for n in ("Shift_L", "Enter", "Backspace", "Left")})
        self.assertIn(self.idx("Space"), typingmeter.TEXT_IDX)

    @unittest.skipUnless(os.name == "nt", "key state is a Windows call")
    def test_the_real_key_state_call_returns_a_set_of_the_asked_keys_only(self):
        got = typingmeter._async_down([0x41, 0x42])                         # nothing is looked at: only the type is checked
        self.assertIsInstance(got, set)
        self.assertLessEqual(got, {0x41, 0x42})

    def test_the_heatmap_runs_from_blue_to_red_and_needs_counting_switched_on(self):
        stats = {"typing": {"enabled": True, "counts": {self.idx("A"): 100, self.idx("B"): 25}}}
        out = typingmeter.heatmap(0, {}, stats)
        self.assertEqual(out[self.idx("A")], (255, 0, 0))                    # the most pressed key
        self.assertEqual(out[self.idx("C")], faces.dim((0, 70, 200), 0.12))  # never pressed: a faint blue
        self.assertNotIn(out[self.idx("B")], (out[self.idx("A")], out[self.idx("C")]))
        self.assertEqual(len(out), len(keys.KEYS))
        faint = {i: faces.dim((0, 70, 200), 0.12) for _, i, _, _ in keys.KEYS}
        self.assertEqual(typingmeter.heatmap(0, {}, {"typing": {"enabled": False, "counts": {1: 5}}}), faint)   # counting off
        self.assertEqual(typingmeter.heatmap(0, {}, None), faint)

    def test_the_speed_screen_in_every_state(self):
        on = {"typing": {"enabled": True}}
        off = {"typing": {"enabled": False}}
        self.assertEqual(typingmeter.typing_frame(0, off, None), cityapps.note("TYPING OFF"))
        for stats in (None, {}, {"typing": {"wpm": 0, "buckets": []}}, {"typing": {"wpm": 62.4, "buckets": [0, 30, 55, 40] * 12}},
                      {"typing": {"wpm": 140, "buckets": [999] * 36}}):
            f = typingmeter.typing_frame(0, on, stats)
            self.assertEqual((len(f), len(f[0])), (5, 63))
        lit = typingmeter.typing_frame(0, on, {"typing": {"wpm": 62, "buckets": [50] * 36}})
        self.assertIn(typingmeter.speed_colour(62), [c for row in lit for c in row])
        self.assertEqual(typingmeter.speed_colour(0), (0, 120, 255))
        self.assertEqual(typingmeter.speed_colour(500), (255, 0, 0))

    def test_in_the_engine_it_is_off_until_switched_on_and_off_wipes_it(self):
        e, clock = engine()
        e.compose(clock())
        self.assertFalse(e.typing.enabled)
        e.settings.patch({"apps": {"typing": {"enabled": True}}})
        e.typing.down = lambda vks: {0x41}
        e.compose(clock())
        self.assertTrue(e.typing.enabled)
        e.typing.poll(clock())
        self.assertEqual(e.typing.snapshot()["total"], 1)
        state = e.state()["typing"]
        self.assertEqual(set(state), {"enabled", "wpm", "best", "total"})    # the panel never gets per-key counts
        self.assertFalse(e.handle({"cmd": "typingreset"}, "mcp")["ok"])      # only the panel may reset
        self.assertTrue(e.handle({"cmd": "typingreset"}, "gui")["ok"])
        self.assertEqual(e.typing.snapshot()["total"], 0)
        e.typing.poll(clock())
        e.settings.patch({"apps": {"typing": {"enabled": False}}})
        e.compose(clock())
        self.assertEqual((e.typing.enabled, e.typing.snapshot()["total"]), (False, 0))

    def test_the_heatmap_key_light_and_the_speed_app_follow_the_switch(self):
        e, clock = engine(keys={"base": "heatmap"}, apps={"typing": {"enabled": True}})
        e.compose(clock())
        e.typing.down = lambda vks: {0x41}
        e.typing.poll(clock())
        k = e.compose(clock())[1]
        self.assertEqual(k[keys.BY_NAME["A"]], (255, 0, 0))
        off, clock2 = engine(keys={"base": "heatmap"})
        self.assertEqual(set(off.compose(clock2())[1].values()), {faces.dim((0, 70, 200), 0.12)})   # counting is off: a faint blue
        e.settings.patch({"keys": {"base": "solid"}, "apps": {"matrix": "typing"}})
        e.compose(clock())
        self.assertEqual(e.shown["label"], "Typing speed")
        rot = settings.defaults()["apps"]
        rot.update(matrix="rotation", rotation=[{"app": "clock", "seconds": 5}, {"app": "typing", "seconds": 5}])
        self.assertEqual({apps.rotation_item(t, rot["rotation"], rot) for t in (0, 5)}, {"clock"})   # switched off: skipped


class ModesTests(unittest.TestCase):
    def e(self, **over):
        return engine(**over)

    def test_claude_and_screen_sets_the_matrix_and_keys_and_switching_off_puts_everything_back(self):
        e, clock = engine(apps={"matrix": "clock"}, keys={"base": "rainbow"})
        self.assertFalse(e.state()["modes"]["claude_screen"]["on"])
        self.assertTrue(e.handle({"cmd": "mode", "name": "claude_screen", "on": True}, "gui")["ok"])
        s = e.settings.snapshot()
        self.assertEqual((s["apps"]["matrix"], s["keys"]["base"]), ("claude", "screen"))
        self.assertFalse(any(s["keys"]["moods"].values()))                     # Claude's key effects would hide the screen
        self.assertTrue(e.state()["modes"]["claude_screen"]["on"])
        self.assertTrue(apps.claude_picked(s["apps"]))                         # so Claude's status is always on the matrix
        k = e.compose(clock())[1]
        self.assertEqual(len(k), len(keys.KEYS))                               # the keys follow the screen
        self.assertTrue(e.handle({"cmd": "mode", "name": "claude_screen", "on": False}, "gui")["ok"])
        s = e.settings.snapshot()
        self.assertEqual((s["apps"]["matrix"], s["keys"]["base"]), ("clock", "rainbow"))     # what it was before
        self.assertTrue(all(s["keys"]["moods"].values()))
        self.assertEqual(s["modes"]["previous"], "")
        self.assertFalse(e.state()["modes"]["claude_screen"]["on"])

    def test_switching_on_twice_keeps_the_first_memory_and_off_without_memory_uses_the_factory_values(self):
        e, clock = engine(apps={"matrix": "clock"}, keys={"base": "rainbow"})
        for _ in range(2):
            e.handle({"cmd": "mode", "name": "claude_screen", "on": True}, "gui")
        e.handle({"cmd": "mode", "name": "claude_screen", "on": False}, "gui")
        self.assertEqual(e.settings.snapshot()["keys"]["base"], "rainbow")
        e2, _ = engine(apps={"matrix": "claude"}, keys={"base": "screen", "moods": {m: False for m in keyfx.KEY_MOODS}})
        self.assertTrue(e2.state()["modes"]["claude_screen"]["on"])           # already like this, nothing remembered
        e2.handle({"cmd": "mode", "name": "claude_screen", "on": False}, "gui")
        s = e2.settings.snapshot()
        self.assertEqual((s["apps"]["matrix"], s["keys"]["base"]), ("off", "own"))
        self.assertFalse(e2.state()["modes"]["claude_screen"]["on"])

    def test_only_the_panel_may_switch_modes_and_unknown_modes_are_refused(self):
        e, clock = engine()
        self.assertFalse(e.handle({"cmd": "mode", "name": "claude_screen", "on": True}, "mcp")["ok"])
        self.assertFalse(e.state()["modes"]["claude_screen"]["on"])
        self.assertFalse(e.handle({"cmd": "mode", "name": "nope", "on": True}, "gui")["ok"])


class MasterOffSwitch(unittest.TestCase):
    """The off switch: leo-sys holds nothing, and nothing wakes it, until it is switched on again."""

    def test_off_refuses_claude_hooks_scripts_and_drawing_and_lets_go_of_everything(self):
        e, clock = engine(apps={"matrix": "clock"}, push={"enabled": True})
        e.handle({"cmd": "event", "event": "UserPromptSubmit", "session": "s1", "cwd": "C:/x"}, "hook")
        e.handle({"cmd": "face", "mood": "done"}, "gui")
        self.assertTrue(e.sessions and e.manual)
        self.assertFalse(e.state()["off"])
        self.assertTrue(e.handle({"cmd": "power", "on": False}, "gui")["ok"])
        s = e.state()
        self.assertTrue(s["off"])
        self.assertFalse(s["connected"])
        self.assertEqual((s["showing"]["label"], s["sessions"], s["showing"]["matrix"]), ("Switched off", [], None))
        self.assertIsNone(e.manual)
        self.assertEqual(e.compose(clock()), (None, None))                     # nothing to draw
        for req, source in [({"cmd": "face", "mood": "done"}, "mcp"), ({"cmd": "event", "event": "Stop", "session": "s1"}, "hook"),
                            ({"cmd": "text", "text": "hi"}, "push"), ({"cmd": "keys", "colour": "red"}, "mcp"),
                            ({"cmd": "nowplaying"}, "mcp"), ({"cmd": "power", "on": True}, "mcp"), ({"cmd": "power", "on": True}, "hook")]:
            r = e.handle(req, source)
            self.assertFalse(r["ok"], (req, source))
            self.assertTrue(r["denied"], (req, source))
        self.assertEqual(e.sessions, {})                                       # a refused hook event was not even recorded
        self.assertEqual(e.handle({"cmd": "face", "mood": "done"}, "gui")["denied"], "leo-sys is switched off: switch it on first")
        self.assertIsNone(e.manual)
        self.assertTrue(e.handle({"cmd": "pin", "value": True}, "gui")["ok"])   # plain settings still work from the panel

    def test_off_refusals_say_why_and_are_not_logged_but_the_switch_itself_is(self):
        e, _ = engine()
        e.handle({"cmd": "power", "on": False}, "gui")
        before = len(e.log)
        r = e.handle({"cmd": "face", "mood": "done"}, "mcp")
        self.assertEqual(r["denied"], power.MESSAGE)
        for _ in range(20):
            e.handle({"cmd": "event", "event": "PreToolUse", "session": "s"}, "hook")
        self.assertEqual(len(e.log), before)                                   # the hooks fire all the time: no flood
        self.assertIn("switched off", e.log[0]["action"])

    def test_switching_on_again_gives_everything_back(self):
        e, clock = engine(apps={"matrix": "clock"})
        e.handle({"cmd": "power", "on": False}, "gui")
        self.assertTrue(e.handle({"cmd": "power", "on": True}, "gui")["ok"])
        self.assertFalse(e.state()["off"])
        self.assertTrue(e.handle({"cmd": "face", "mood": "done"}, "mcp")["ok"])
        self.assertIsNotNone(e.compose(clock())[0])

    def test_only_the_panel_may_switch_it(self):
        e, _ = engine()
        for source in ("mcp", "hook", "push"):
            self.assertFalse(e.handle({"cmd": "power", "on": False}, source)["ok"], source)
        self.assertFalse(e.state()["off"])

    def test_the_switch_is_a_file_so_it_survives_a_restart_and_a_second_engine_sees_it(self):
        e, _ = engine()
        e.handle({"cmd": "power", "on": False}, "gui")
        self.assertTrue(e.switch.path.is_file())
        again = Engine(settings=e.settings, keyboard=DemoKeyboard, clock=Clock(), threads=False)
        self.assertTrue(again.state()["off"])
        e.handle({"cmd": "power", "on": True}, "gui")
        self.assertFalse(e.switch.path.exists())
        self.assertFalse(again.state()["off"])

    def test_the_worker_closes_its_link_to_the_keyboard_when_switched_off_and_opens_it_again_when_on(self):
        import threading
        links = []

        class Link(DemoKeyboard):
            def __init__(self):
                super().__init__()
                self.closed = False
                links.append(self)

            def close(self):
                self.closed = True

        def wait_for(cond, what):
            end = time.time() + 5
            while time.time() < end and not cond():
                time.sleep(0.02)
            self.assertTrue(cond(), what)

        e, _ = engine(apps={"matrix": "clock"})
        e.keyboard_factory = Link
        threading.Thread(target=e._worker, daemon=True).start()
        try:
            wait_for(lambda: e.connected and links and links[0].frames, "connected and drawing")
            e.handle({"cmd": "power", "on": False}, "gui")
            wait_for(lambda: links[0].closed, "the link is closed (so the Leobog app can have the keyboard)")
            self.assertFalse(e.state()["connected"])
            frames = links[0].frames
            time.sleep(0.4)
            self.assertEqual(links[0].frames, frames)                          # and nothing more is sent
            self.assertEqual(len(links), 1)                                    # nor reopened while off
            e.handle({"cmd": "power", "on": True}, "gui")
            wait_for(lambda: len(links) == 2 and e.connected, "reconnected after switching on")
        finally:
            e.quit.set()

    def test_the_hooks_and_the_mcp_server_never_start_the_service_while_off(self):
        from leosys import client, power as power_mod
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(power_mod, "DATA_DIR", tmp), mock.patch.object(client, "start_daemon") as start, \
                mock.patch.object(client, "_request", side_effect=OSError("nothing is listening")):
            client.ensure_daemon(wait=0.1)                                     # nothing listens and it is switched on: it starts
            start.assert_called_once()
            power_mod.Switch().set_off(True)
            start.reset_mock()
            self.assertFalse(client.ensure_daemon(wait=0.1))
            start.assert_not_called()
            with self.assertRaises(client.SwitchedOff):
                client.send({"cmd": "event", "event": "Stop"}, "hook")
            with self.assertRaises(client.SwitchedOff):
                client.get("/api/claude")
            start.assert_not_called()
            self.assertEqual(mcp_server.call_tool("set_face", {"mood": "done"}), mcp_server.OFF_TEXT)
            self.assertEqual(mcp_server.call_tool("leo_status", {}), mcp_server.OFF_TEXT)
            start.reset_mock()
            client.ensure_daemon(wait=0.1, force=True)                         # you asked for the panel: it starts (idle)
            start.assert_called_once()

    def test_a_running_but_switched_off_service_answers_the_mcp_server_with_a_plain_refusal(self):
        reply = {"ok": False, "denied": power.MESSAGE, "connected": False}
        with mock.patch.object(mcp_server, "send", return_value=reply):
            self.assertEqual(mcp_server.call_tool("show_text", {"text": "hi"}), mcp_server.OFF_TEXT)
        with mock.patch.object(mcp_server, "get", return_value={"switched_off": True, "permissions": {}}):
            self.assertIn('"switched_off": true', mcp_server.call_tool("leo_status", {}))

    def test_the_command_line_switch_writes_and_removes_the_file(self):
        import contextlib
        import io
        from leosys import cli, power as power_mod
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(power_mod, "DATA_DIR", tmp):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(["off"]), 0)
                self.assertTrue((tmp / "off").is_file())
                self.assertIn("switched off", out.getvalue())
                self.assertEqual(cli.main(["on"]), 0)
            self.assertFalse((tmp / "off").exists())

    def test_the_command_line_says_so_instead_of_a_traceback_when_off(self):
        import contextlib
        import io
        from leosys import cli, client, power as power_mod
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(power_mod, "DATA_DIR", tmp), mock.patch.object(client, "_request", side_effect=OSError), \
                mock.patch.object(client, "start_daemon") as start:
            power_mod.Switch().set_off(True)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(["face", "done"]), 1)
            self.assertIn("switched off", out.getvalue())
            start.assert_not_called()


class StepAsideForTheLeobogApp(unittest.TestCase):
    """While the Leobog app is open leo-sys holds nothing, and it comes back by itself when that app closes."""

    def make(self, **over):
        e, clock = engine(**over)
        app = {"open": True}
        e.app_probe = lambda: app["open"]
        return e, clock, app

    def test_it_steps_aside_while_the_app_is_open_and_refuses_everything_that_would_draw(self):
        e, clock, app = self.make(apps={"matrix": "clock"})
        self.assertFalse(e.state()["stepped_aside"])
        e.handle({"cmd": "event", "event": "UserPromptSubmit", "session": "s", "cwd": "C:/x"}, "hook")
        e.check_app()
        s = e.state()
        self.assertEqual((s["stepped_aside"], s["off"], s["connected"], s["official_app"]), (True, False, False, True))
        self.assertEqual((s["showing"]["label"], s["showing"]["kind"], s["sessions"]), ("Stepped aside for the Leobog app", "aside", []))
        self.assertEqual(e.compose(clock()), (None, None))
        for req, source in [({"cmd": "face", "mood": "done"}, "mcp"), ({"cmd": "event", "event": "Stop", "session": "s"}, "hook"),
                            ({"cmd": "text", "text": "hi"}, "push"), ({"cmd": "face", "mood": "done"}, "gui")]:
            r = e.handle(req, source)
            self.assertEqual((r["ok"], r["denied"]), (False, power.ASIDE_MESSAGE), (req, source))
        self.assertEqual(e.sessions, {})
        self.assertTrue(e.handle({"cmd": "pin", "value": True}, "gui")["ok"])            # settings still work

    def test_it_comes_back_a_few_seconds_after_the_app_closes(self):
        e, clock, app = self.make(apps={"matrix": "clock"})
        e.check_app()
        app["open"] = False
        clock.t += 1
        e.check_app()
        self.assertTrue(e.state()["stepped_aside"])                                       # not yet: the app only just went
        clock.t += 3
        self.assertFalse(e.state()["stepped_aside"])
        self.assertTrue(e.handle({"cmd": "face", "mood": "done"}, "mcp")["ok"])
        self.assertIsNotNone(e.compose(clock())[0])
        self.assertFalse(e.state()["official_app"])

    def test_the_switch_in_settings_turns_it_off_and_the_panel_still_warns(self):
        e, clock, app = self.make(leobog={"step_aside": False})
        e.check_app()
        s = e.state()
        self.assertEqual((s["stepped_aside"], s["official_app"]), (False, True))
        self.assertTrue(e.handle({"cmd": "face", "mood": "done"}, "mcp")["ok"])
        e.settings.patch({"leobog": {"step_aside": True}})                                 # switching it on acts at once
        self.assertTrue(e.state()["stepped_aside"])
        e.settings.patch({"leobog": {"step_aside": False}})
        self.assertFalse(e.state()["stepped_aside"])

    def test_it_is_on_by_default_and_the_master_switch_wins(self):
        self.assertTrue(settings.defaults()["leobog"]["step_aside"])
        e, clock, app = self.make()
        e.check_app()
        e.handle({"cmd": "power", "on": False}, "gui")
        s = e.state()
        self.assertEqual((s["off"], s["stepped_aside"]), (True, False))
        self.assertEqual(e.handle({"cmd": "face", "mood": "done"}, "mcp")["denied"], power.MESSAGE)
        self.assertTrue(e.handle({"cmd": "power", "on": True}, "gui")["ok"])               # it can be switched on: the app still holds it off
        self.assertTrue(e.state()["stepped_aside"])

    def test_without_a_probe_nothing_is_looked_for(self):
        e, _ = engine()
        self.assertIsNone(e.app_probe)                                                     # a pretend keyboard never looks
        e.check_app()
        self.assertFalse(e.state()["stepped_aside"])

    def test_the_worker_lets_go_of_the_keyboard_for_the_app_and_takes_it_back(self):
        import threading
        links, app = [], {"open": False}

        class Link(DemoKeyboard):
            def __init__(self):
                super().__init__()
                self.closed = False
                links.append(self)

            def close(self):
                self.closed = True

        def wait_for(cond, what, seconds=12):
            end = time.time() + seconds
            while time.time() < end and not cond():
                time.sleep(0.05)
            self.assertTrue(cond(), what)

        s = settings.Settings(Path(tempfile.mkdtemp()) / "s.json")
        s.patch({"apps": {"matrix": "clock"}})
        e = Engine(settings=s, keyboard=Link, clock=time.time, threads=False, probe=lambda: (frozenset(), ""),
                   shortcuts=smartkeys.Library(Path(tempfile.mkdtemp()) / "shortcuts.json"), app_probe=lambda: app["open"])
        threading.Thread(target=e._worker, daemon=True).start()
        try:
            wait_for(lambda: e.connected and links and links[0].frames, "connected and drawing")
            app["open"] = True
            wait_for(lambda: links[0].closed, "the link is closed once the Leobog app opens")
            self.assertTrue(e.state()["stepped_aside"])
            frames = links[0].frames
            time.sleep(0.5)
            self.assertEqual((links[0].frames, len(links)), (frames, 1))                  # nothing sent, nothing reopened
            app["open"] = False
            wait_for(lambda: len(links) == 2 and e.connected, "back by itself after the app closes")
            self.assertFalse(e.state()["stepped_aside"])
        finally:
            e.quit.set()

    def test_the_mcp_server_explains_it(self):
        reply = {"ok": False, "denied": power.ASIDE_MESSAGE, "connected": False}
        with mock.patch.object(mcp_server, "send", return_value=reply):
            self.assertEqual(mcp_server.call_tool("show_text", {"text": "hi"}), mcp_server.ASIDE_TEXT)
        with mock.patch.object(mcp_server, "get", return_value={"stepped_aside": True, "switched_off": False, "permissions": {}}):
            self.assertIn('"stepped_aside": true', mcp_server.call_tool("leo_status", {}))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_the_process_list_lookup_finds_a_running_program_and_not_a_missing_one(self):
        from leosys import device
        self.assertTrue(device._process_listed(os.path.basename(sys.executable)))          # this very test run
        self.assertTrue(device._process_listed(os.path.basename(sys.executable).upper()))  # file names are not case sensitive
        self.assertFalse(device._process_listed("no-such-program-9f3a.exe"))
        self.assertIn(device.official_app_running(), (True, False))


class TaskbarAlerts(unittest.TestCase):
    CFG = {"enabled": True, "seconds": 6, "cooldown_s": 60, "ignore": "", "only": ""}

    def alert_engine(self, **taskbar):
        e, clock = engine(alerts={"taskbar": dict(self.CFG, **taskbar)}, push={"enabled": True})
        return e, clock

    def test_program_lists_and_names(self):
        self.assertEqual(taskalerts.parse_names("MSRDC, steam.exe;  Discord\nzoom"), {"msrdc.exe", "steam.exe", "discord.exe", "zoom.exe"})
        self.assertEqual(taskalerts.parse_names(""), set())
        self.assertTrue(taskalerts.accept("Discord.exe", {"ignore": "", "only": ""}))
        self.assertFalse(taskalerts.accept("msrdc.exe", {"ignore": "msrdc", "only": ""}))
        self.assertFalse(taskalerts.accept("chrome.exe", {"ignore": "", "only": "discord.exe"}))
        self.assertTrue(taskalerts.accept("discord.exe", {"ignore": "", "only": "discord.exe"}))
        self.assertFalse(taskalerts.accept("", {"ignore": "", "only": ""}))
        self.assertEqual(taskalerts.describe("discord.exe"), ("DISCORD", (88, 101, 242)))
        self.assertEqual(taskalerts.describe("MyChat.exe"), ("MYCHAT", taskalerts.ORANGE))

    def test_the_listener_keeps_a_flash_once_per_cooldown_and_only_the_file_name(self):
        now = [100.0]
        w = taskflash.FlashWatch(clock=lambda: now[0])
        self.assertTrue(w.note("Discord.exe", 123))
        self.assertFalse(w.note("discord.exe", 123))                     # the same program a moment later: one event
        self.assertTrue(w.note("chrome.exe", 456))
        now[0] += taskflash.COOLDOWN_S + 1
        self.assertTrue(w.note("discord.exe", 123))
        self.assertFalse(w.note("", 1))
        self.assertEqual([e["exe"] for e in w.pop_new()], ["discord.exe", "chrome.exe", "discord.exe"])
        self.assertEqual(w.pop_new(), [])
        self.assertEqual(set(w.recent()[0]), {"t", "exe", "hwnd"})        # no window title is ever kept
        seen = w.seen()
        self.assertEqual([(e["exe"], e["count"]) for e in seen], [("discord.exe", 2), ("chrome.exe", 1)])   # once each, newest first

    @unittest.skipUnless(os.name == "nt", "taskbar flashes are a Windows thing")
    def test_the_real_listener_starts_and_stops_cleanly(self):
        w = taskflash.FlashWatch()
        w.start()
        try:
            if w.error:
                self.skipTest("this session cannot register a shell hook: " + w.error)
            self.assertTrue(w.running())
        finally:
            w.stop()
        self.assertFalse(w.running())

    def test_a_flash_makes_the_keys_flash_and_the_matrix_show_the_name_then_it_hands_back(self):
        e, clock = self.alert_engine()
        e.compose(clock())
        self.assertTrue(e.flashes.running())                                # switched on: it listens
        e.flashes.note("discord.exe", 7)
        m, k = e.compose(clock())
        self.assertEqual(e.shown["kind"], "alert")
        self.assertIn("discord", e.shown["label"])
        self.assertIn((88, 101, 242), [c for row in m for c in row])
        self.assertEqual(k[keys.BY_NAME["Q"]], (88, 101, 242))              # keys on ...
        clock.t += 0.4
        self.assertEqual(e.compose(clock())[1], {})                          # ... then dark: they flash
        clock.t += 0.4
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["Q"]], (88, 101, 242))
        self.assertEqual(e.state()["alerts"]["showing"], "DISCORD")
        clock.t += 7
        e.compose(clock())
        self.assertNotEqual(e.shown["kind"], "alert")
        self.assertIsNone(e.state()["alerts"]["showing"])

    def test_ignored_and_repeating_programs_do_not_alert(self):
        e, clock = self.alert_engine(ignore="msrdc.exe")
        e.compose(clock())
        e.flashes.note("msrdc.exe", 1)
        e.compose(clock())
        self.assertNotEqual(e.shown["kind"], "alert")
        e.flashes.note("discord.exe", 2)
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "alert")
        clock.t += 10
        e.flashes.last.clear()                                                # the listener's own cooldown is separate
        e.flashes.note("discord.exe", 2)
        e.compose(clock())
        self.assertNotEqual(e.shown["kind"], "alert")                         # inside the 60 s cooldown: no second alert
        clock.t += 61
        e.flashes.last.clear()
        e.flashes.note("discord.exe", 2)
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "alert")

    def test_switched_off_it_does_not_listen_and_a_pick_of_yours_beats_an_alert(self):
        off, clock = engine()
        off.compose(clock())
        self.assertFalse(off.flashes.running())
        e, clock = self.alert_engine()
        e.compose(clock())
        e.handle({"cmd": "text", "text": "mine", "seconds": 5}, "push")
        e.flashes.note("discord.exe", 7)
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "push")
        e.settings.patch({"alerts": {"taskbar": {"enabled": False}}})
        e.compose(clock())
        self.assertFalse(e.flashes.running())                               # the switch stops the listener
        self.assertIsNone(e.alert)

    def test_the_listener_never_makes_the_engine_wait_and_survives_any_failure(self):
        started = []

        class Spy(taskflash.FakeFlashWatch):
            def start(self, wait=3.0):
                started.append(wait)
                super().start()
        e, clock = self.alert_engine()
        e.flashes = Spy(clock=clock)
        e.compose(clock())
        self.assertEqual(started, [0])                                       # asked not to wait: the engine's lock is held
        w = taskflash.FlashWatch()
        w._listen = lambda: (_ for _ in ()).throw(ValueError("odd failure"))   # not an OSError
        w.start(wait=3.0)
        self.assertTrue(w.ready.is_set())                                    # nobody is left waiting
        self.assertIn("odd failure", w.error)

    def test_switching_alerts_off_forgets_what_flashed(self):
        e, clock = self.alert_engine()
        e.compose(clock())
        e.flashes.note("discord.exe", 1)
        e.compose(clock())
        self.assertTrue(e.state()["alerts"]["seen"])
        e.settings.patch({"alerts": {"taskbar": {"enabled": False}}})
        e.compose(clock())
        self.assertEqual(e.state()["alerts"]["seen"], [])
        self.assertEqual(e.flashes.pop_new(), [])

    def test_the_test_button_is_for_the_panel_only(self):
        e, clock = self.alert_engine()
        self.assertFalse(e.handle({"cmd": "alerttest"}, "mcp")["ok"])
        self.assertTrue(e.handle({"cmd": "alerttest"}, "gui")["ok"])
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "alert")

    def test_a_listener_that_cannot_start_is_not_retried_every_tick(self):
        class Broken(taskflash.FakeFlashWatch):
            starts = 0

            def start(self, wait=3.0):
                Broken.starts += 1
                self.error = "no shell hook here"
        e, clock = self.alert_engine()
        e.flashes = Broken(clock=clock)
        for _ in range(5):
            e.compose(clock())
        self.assertEqual(Broken.starts, 1)
        clock.t += 31
        e.compose(clock())
        self.assertEqual(Broken.starts, 2)
        self.assertEqual(e.state()["alerts"]["error"], "no shell hook here")


class PrivacyUse(unittest.TestCase):
    BOOT = 1_000_000

    def entry(self, name, start, stop, path=None):
        return {"name": name, "start": start, "stop": stop, "path": path}

    def test_only_programs_that_are_really_using_the_device_count(self):
        items = [self.entry("C:#Apps#Discord#app-1#Discord.exe", self.BOOT - 5, 0, "C:\\Apps\\Discord\\app-1\\Discord.exe"),   # stale: before boot
                 self.entry("C:#Apps#Discord#app-2#Discord.exe", self.BOOT + 5, 0, "C:\\Apps\\Discord\\app-2\\Discord.exe"),   # running
                 self.entry("C:#Apps#Zoom#Zoom.exe", self.BOOT + 5, 0, "C:\\Apps\\Zoom\\Zoom.exe"),                          # not running
                 self.entry("C:#Apps#Obs#obs.exe", self.BOOT + 5, self.BOOT + 9, "C:\\Apps\\Obs\\obs.exe"),                  # stopped
                 self.entry("MSTeams_8wekyb3d8bbwe", self.BOOT + 7, 0),                                                      # a store app
                 self.entry("Microsoft.WindowsCamera_8wekyb3d8bbwe", 0, 0)]                                                   # never used
        running = lambda: ["c:\\apps\\discord\\app-2\\discord.exe", "c:\\windows\\explorer.exe"]
        self.assertEqual(privacyuse.in_use(items, self.BOOT, running), ["Discord.exe", "MSTeams"])

    def test_a_truncated_path_is_matched_by_its_end_and_nothing_is_listed_when_nothing_qualifies(self):
        item = self.entry("Program Files#Tool#bin#Tool.exe", self.BOOT + 1, 0, "Program Files\\Tool\\bin\\Tool.exe")
        self.assertEqual(privacyuse.in_use([item], self.BOOT, lambda: ["d:\\program files\\tool\\bin\\tool.exe"]), ["Tool.exe"])
        called = []
        self.assertEqual(privacyuse.in_use([self.entry("X", self.BOOT - 1, 0)], self.BOOT, lambda: called.append(1) or []), [])
        self.assertEqual(called, [])                                           # the process list is only read for a candidate

    def test_friendly_names(self):
        self.assertEqual(privacyuse.friendly(self.entry("MSTeams_8wekyb3d8bbwe", 1, 0)), "MSTeams")
        self.assertEqual(privacyuse.friendly(self.entry("x", 1, 0, "C:\\a b\\Zoom.exe")), "Zoom.exe")

    def test_the_real_record_and_process_list_can_be_read(self):
        got = privacyuse.read_in_use()
        self.assertEqual(set(got), {"mic", "cam"})
        self.assertTrue(all(isinstance(n, str) for v in got.values() for n in v))

    def test_the_watcher_keeps_the_last_answer_and_survives_errors(self):
        w = privacyuse.UseWatch(reader=lambda: {"mic": ["Zoom.exe"], "cam": []}, threads=False)
        w._step()
        self.assertEqual(w.snapshot()["mic"], ["Zoom.exe"])
        w.reader = lambda: (_ for _ in ()).throw(OSError("registry busy"))
        w._step()
        snap = w.snapshot()
        self.assertEqual((snap["mic"], "registry busy" in snap["error"]), ([], True))

    def test_the_keys_pulse_while_in_use_and_each_has_its_own_switch(self):
        base = {"base": "solid", "colour": "#00ff00"}
        e, clock = engine(keys=dict(base, indicators={"mic_use": True, "cam_use": True}))
        e.privacy.value = {"mic": ["Zoom.exe"], "cam": ["Zoom.exe"]}
        k = e.compose(clock())[1]
        self.assertEqual(e.privacy.wanted, 1)
        self.assertNotEqual(k[keys.BY_NAME["Home"]], (0, 255, 0))
        self.assertNotEqual(k[keys.BY_NAME["Backspace"]], (0, 255, 0))
        self.assertGreater(k[keys.BY_NAME["Backspace"]][0], 100)               # red
        self.assertEqual(k[keys.BY_NAME["Backspace"]][1:], (0, 0))
        e2, clock2 = engine(keys=dict(base, indicators={"mic_use": True, "cam_use": False}))
        e2.privacy.value = {"mic": ["Zoom.exe"], "cam": ["Zoom.exe"]}
        k2 = e2.compose(clock2())[1]
        self.assertNotEqual(k2[keys.BY_NAME["Home"]], (0, 255, 0))
        self.assertEqual(k2[keys.BY_NAME["Backspace"]], (0, 255, 0))             # the camera switch is off
        e3, clock3 = engine(keys=base)                                          # both off by default: never read
        e3.compose(clock3())
        self.assertEqual(e3.privacy.wanted, 0)
        self.assertEqual(e3.state()["privacy"]["mic"], [])


class PushCommand(unittest.TestCase):
    def test_the_command_line_becomes_a_request(self):
        from leosys import cli
        self.assertEqual(cli.parse_push(["text", "BUILD", "OK", "--colour", "green", "--seconds", "5", "--keys", "red"]),
                         {"cmd": "text", "text": "BUILD OK", "colour": "green", "keys": "red", "seconds": 5.0})
        self.assertEqual(cli.parse_push(["--color", "red", "text", "hi"]), {"cmd": "text", "text": "hi", "colour": "red"})
        self.assertEqual(cli.parse_push(["colour", "#ff0000"]), {"cmd": "keys", "colour": "#ff0000"})
        self.assertEqual(cli.parse_push(["bar", "40", "--colour", "blue"]), {"cmd": "bar", "percent": 40.0, "colour": "blue"})
        self.assertEqual(cli.parse_push(["clear"]), {"cmd": "clear"})
        self.assertEqual(cli.parse_push(["text", "-"], stdin="line one\n  line two\n"), {"cmd": "text", "text": "line one line two"})

    def test_mistakes_are_explained_not_sent(self):
        from leosys import cli
        for bad in ([], ["wobble"], ["text"], ["text", "-"], ["colour"], ["colour", "a", "b"], ["bar"], ["bar", "lots"],
                    ["text", "hi", "--seconds"], ["text", "hi", "--seconds", "soon"]):
            with self.assertRaises(ValueError, msg=bad):
                cli.parse_push(bad, stdin="")

    def test_the_command_sends_through_the_push_door_and_reports_the_result(self):
        import contextlib
        import io
        from unittest import mock
        from leosys import cli
        out = io.StringIO()
        with mock.patch("leosys.client.send", return_value={"ok": True}) as send, contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["push", "text", "hi"]), 0)
        send.assert_called_once_with({"cmd": "text", "text": "hi"}, "push")
        with mock.patch("leosys.client.send", return_value={"ok": False, "denied": "switched off"}), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["push", "clear"]), 1)
        self.assertIn("switched off", out.getvalue())

    def test_text_colours_and_bars_show_for_the_time_asked(self):
        e, clock = engine(push={"enabled": True})
        self.assertTrue(e.handle({"cmd": "text", "text": "build ok", "colour": "green", "seconds": 5}, "push")["ok"])
        m = e.manual
        self.assertEqual((m["kind"], m["text"], m["colour"], m["source"], m["until"] - clock()), ("text", "BUILD OK", (0, 255, 60), "push", 5))
        e.compose(clock())
        self.assertEqual(e.shown["kind"], "push")
        clock.t += 6
        e.compose(clock())
        self.assertNotEqual(e.shown["kind"], "push")                      # it hands back by itself
        self.assertTrue(e.handle({"cmd": "keys", "colour": "red", "seconds": 3}, "push")["ok"])
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["Q"]], (255, 0, 0))
        self.assertTrue(e.handle({"cmd": "bar", "percent": 50, "colour": "blue", "keys": "#00ff00"}, "push")["ok"])
        frame = e.compose(clock())[0]
        self.assertEqual(frame[2][50], faces.dim((0, 120, 255), 0.12))      # the empty half of the bar is dim
        self.assertEqual(frame[2][20], (0, 120, 255))                       # the filled half is bright
        self.assertEqual(e.compose(clock())[1][keys.BY_NAME["Q"]], (0, 255, 0))
        self.assertTrue(e.handle({"cmd": "clear"}, "push")["ok"])
        self.assertIsNone(e.manual)
        self.assertIsNone(e.manual_keys)

    def test_the_bar_fills_from_the_left_in_proportion(self):
        full = faces.bar_frame(100, (255, 0, 0))
        empty = faces.bar_frame(0, (255, 0, 0))
        half = faces.bar_frame(50, (255, 0, 0))
        lit = lambda f: sum(1 for x in range(63) if f[2][x] == (255, 0, 0))
        self.assertGreater(lit(full), lit(half))
        self.assertGreater(lit(half), lit(empty))
        self.assertEqual(faces.bar_frame(500, (1, 2, 3)), faces.bar_frame(100, (1, 2, 3)))     # clamped

    def test_it_is_not_claudes_permissions_but_it_has_its_own_switch(self):
        e, clock = engine(claude={"enabled": False}, push={"enabled": True})   # Claude's master switch is off
        self.assertTrue(e.handle({"cmd": "text", "text": "still works"}, "push")["ok"])
        e, clock = engine(push={"enabled": False})
        r = e.handle({"cmd": "text", "text": "no"}, "push")
        self.assertFalse(r["ok"])
        self.assertIn("switched off", r["denied"])
        self.assertIsNone(e.manual)
        self.assertFalse(e.log[0]["ok"])

    def test_nonsense_is_refused_politely_and_the_time_is_clamped(self):
        e, clock = engine(push={"enabled": True})
        for bad in ({"cmd": "face", "mood": "idle"}, {"cmd": "text", "text": "   "}, {"cmd": "bar", "percent": "lots"},
                    {"cmd": "text", "text": "x", "seconds": "soon"}):
            self.assertFalse(e.handle(bad, "push")["ok"], bad)
        e.handle({"cmd": "text", "text": "x", "seconds": 99999}, "push")
        self.assertEqual(e.manual["until"] - clock(), 300)
        e.handle({"cmd": "text", "text": "x", "seconds": 0.1}, "push")
        self.assertEqual(e.manual["until"] - clock(), 1)

    def test_pushes_start_switched_off(self):
        self.assertIs(settings.defaults()["push"]["enabled"], False)
        e, clock = engine()
        r = e.handle({"cmd": "text", "text": "hello"}, "push")
        self.assertFalse(r["ok"])
        self.assertIsNone(e.manual)

    def test_named_colours(self):
        from leosys import engine as engine_module
        self.assertEqual(engine_module._rgb("Green", None), (0, 255, 60))
        self.assertEqual(engine_module._rgb("#102030", None), (16, 32, 48))
        self.assertEqual(engine_module._rgb("nonsense", (1, 2, 3)), (1, 2, 3))
        self.assertIn("push", engine(push={"enabled": True})[0].state())


class CityRobustness(unittest.TestCase):
    def test_nonsense_coordinates_and_offsets_are_clamped_not_fatal(self):
        t = prayer.times(datetime.date(2026, 11, 19), 5000, 5000, 3, "mwl", offsets={"fajr": 99999})
        self.assertEqual(set(t), {"fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"})
        url = weather.build_url(5000, -5000)
        self.assertIn("latitude=90.00", url)
        self.assertIn("longitude=-180.00", url)
        cfg = settings.defaults()["apps"]
        cfg["prayer"]["enabled"] = True
        cfg["city"].update(lat=5000, lon=5000)
        frame = cityapps.prayer_frame(1e9, cfg, None)
        self.assertEqual((len(frame), len(frame[0])), (5, 63))
        e, clock = engine(apps={"prayer": {"enabled": True}, "city": {"lat": 5000, "lon": 5000}})
        self.assertIn("prayer", e.state()["city"])                       # the panel's summary survives it too

    def test_weather_stops_asking_about_a_minute_after_it_was_last_used(self):
        self.assertLessEqual(weather.IDLE_AFTER, 60)


class CityScreens(unittest.TestCase):
    def cfg(self, **over):
        cfg = settings.defaults()["apps"]
        cfg["weather"].update(enabled=True)
        cfg["prayer"].update(enabled=True)
        for k, v in over.items():
            cfg[k].update(v)
        return cfg

    def snap(self, **over):
        return {"data": dict(weather.parse(OPEN_METEO), **over), "stale": False, "error": ""}

    def test_icons_follow_the_weather_codes(self):
        names = {0: "sun", 1: "partly", 3: "cloud", 45: "fog", 61: "rain", 81: "rain", 73: "snow", 95: "storm", 999: "storm"}
        for code, name in names.items():
            self.assertEqual(cityapps.icon_name(code, True), name, code)
        self.assertEqual(cityapps.icon_name(0, False), "moon")
        for rows in cityapps.ICONS.values():
            self.assertEqual((len(rows), {len(r) for r in rows}), (5, {5}))

    def test_temperature_colours_run_from_cold_blue_to_hot_red(self):
        self.assertEqual(cityapps.temp_colour(-20), (0, 120, 255))
        self.assertEqual(cityapps.temp_colour(60), (255, 0, 0))
        self.assertEqual(cityapps.temp_colour(20), (0, 255, 60))

    def test_every_screen_state_is_a_full_frame(self):
        for cfg in (self.cfg(), self.cfg(weather={"fahrenheit": True}), self.cfg(weather={"enabled": False}),
                    self.cfg(prayer={"enabled": False})):
            for stats in ({"weather": self.snap()}, {"weather": self.snap(temp=-12.0, code=71, day=False)},
                          {"weather": {"data": None, "error": "offline"}}, {"weather": {"data": None, "error": ""}},
                          {"weather": dict(self.snap(), stale=True)}, None, {}):
                for frame in (cityapps.weather_frame(1e9, cfg, stats), cityapps.prayer_frame(1e9, cfg, stats)):
                    self.assertEqual((len(frame), len(frame[0])), (5, 63))

    def test_a_switched_off_app_says_so(self):
        off = cityapps.weather_frame(0, self.cfg(weather={"enabled": False}), None)
        self.assertEqual(off, cityapps.note("WEATHER OFF"))
        self.assertEqual(cityapps.prayer_frame(0, self.cfg(prayer={"enabled": False}), None), cityapps.note("PRAYER OFF"))

    def test_the_countdown_reads_plainly(self):
        td = datetime.timedelta
        self.assertEqual([cityapps.countdown_text(td(minutes=m)) for m in (125, 60, 59, 7)], ["2H05", "1H00", "59M", "7M"])
        self.assertEqual(cityapps.countdown_text(td(seconds=20)), "NOW")

    def test_the_prayer_screen_warns_as_the_time_nears(self):
        cfg = self.cfg()
        city, p = cfg["city"], cfg["prayer"]
        _, asr, _ = prayer.next_prayer(datetime.datetime(2026, 11, 19, 3, 0), city["lat"], city["lon"], p["method"], p["asr"],
                                       p["offsets"], enabled=("asr",))     # Asr in this PC's own time zone, whatever it is
        green = cityapps.prayer_frame((asr - datetime.timedelta(minutes=98)).timestamp(), cfg, None)
        red = cityapps.prayer_frame((asr - datetime.timedelta(minutes=8)).timestamp(), cfg, None)
        self.assertIn((0, 255, 60), [c for row in green for c in row])
        self.assertIn(faces.RED, [c for row in red for c in row])
        self.assertNotIn(faces.RED, [c for row in green for c in row])

    def test_a_switched_off_app_is_skipped_in_the_rotation_and_asks_for_nothing(self):
        cfg = settings.defaults()["apps"]            # weather and prayer start switched off
        cfg.update(matrix="rotation", rotation=[{"app": "clock", "seconds": 5}, {"app": "weather", "seconds": 5},
                                                 {"app": "prayer", "seconds": 5}])
        self.assertEqual({apps.rotation_item(t, cfg["rotation"], cfg) for t in (0, 5, 10, 14)}, {"clock"})
        self.assertEqual(apps.needs(cfg, "city"), set())
        cfg["weather"]["enabled"] = True
        self.assertEqual({apps.rotation_item(t, cfg["rotation"], cfg) for t in (0, 5, 10, 14)}, {"clock", "weather"})
        self.assertEqual(apps.needs(cfg, "city"), {"weather"})

    def test_the_cities_and_methods_are_listed_for_the_panel(self):
        self.assertIn("Baghdad", places.CITIES)
        for lat, lon in places.CITIES.values():
            self.assertTrue(-90 <= lat <= 90 and -180 <= lon <= 180)
        self.assertEqual(settings.defaults()["apps"]["city"]["name"], places.DEFAULT)

    def test_the_engine_fetches_weather_only_while_the_app_is_switched_on_and_in_use(self):
        off, clock = engine(apps={"matrix": "weather"})
        off.compose(clock())
        self.assertEqual(off.weather.wanted, 0)                              # the app is switched off: no request
        on, clock = engine(apps={"matrix": "weather", "weather": {"enabled": True}})
        on.compose(clock())
        self.assertGreater(on.weather.wanted, 0)
        self.assertEqual(on.state()["city"]["weather"]["temp"], 27.0)
        unused, clock = engine(apps={"matrix": "clock", "weather": {"enabled": True}})
        unused.compose(clock())
        self.assertEqual(unused.weather.wanted, 0)                           # switched on but not on the matrix
        self.assertIsNone(unused.state()["city"]["prayer"])
        pray, clock = engine(apps={"prayer": {"enabled": True}})
        times = pray.state()["city"]["prayer"]
        self.assertEqual(set(times["times"]), {"fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"})
        self.assertIn(times["next"], prayer.PRAYERS)


class Mcp(unittest.TestCase):
    def test_draw_pixels_checks_size(self):
        frame = ["a" * 63] * 5
        r = mcp_server.build_request("draw_pixels", {"frames": [frame], "palette": {"a": "#ff0000"}})
        self.assertEqual(len(r["frames"][0]["rows"][0]), 63)
        with self.assertRaises(ValueError):
            mcp_server.build_request("draw_pixels", {"frames": [["a" * 62] * 5], "palette": {}})

    def test_set_keys_by_name(self):
        e, _ = engine()
        req = mcp_server.build_request("set_keys", {"keys": {"Esc": "#00ff00"}})
        self.assertTrue(e.handle(req, "mcp")["ok"])
        self.assertEqual(e.manual_keys["colours"], {0: (0, 255, 0)})


if __name__ == "__main__":
    unittest.main()
