"""The Windows app: leo-sys.exe, its installer, where its files live, and how the hooks and service find it.

The real-file tests build an actual .exe in a temporary folder; they run only on Windows with pip's launcher stub.
Nothing here touches the real install, the real settings or the keyboard.
"""
import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LEO_DATA", tempfile.mkdtemp(prefix="leo-test-"))

from leosys import __version__, appinstall, cli, config, launch, setup_cmd, settings  # noqa: E402


def fake_app(shebang_line=b"#!pythonw.exe\n"):
    """Stands in for a built app file on any OS: a fake stub, the '#!' line, then a real zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", "print('hi')\n")
        z.writestr("leosys/x.py", "X = 1\n")
    stub = b"MZ" + bytes(range(256)) * 4  # contains no '#!'
    path = Path(tempfile.mkdtemp()) / "setup.exe"
    path.write_bytes(stub + shebang_line + buf.getvalue())
    return path, stub, buf.getvalue()


class Paths(unittest.TestCase):
    def test_paths_follow_local_app_data(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/Profiles/u/AppData/Local"}):
            self.assertEqual(launch.install_exe(), Path("C:/Profiles/u/AppData/Local/Programs/leo-sys/leo-sys.exe"))
            self.assertEqual(launch.app_data_dir(), Path("C:/Profiles/u/AppData/Local/leo-sys"))

    def test_service_and_tool_commands(self):
        exe = Path("C:/Apps/leo-sys/leo-sys.exe")
        with mock.patch.object(launch, "installed", return_value=True), mock.patch.object(launch, "install_exe", return_value=exe):
            self.assertEqual(launch.service_command(), [str(exe), "daemon"])  # the app is its own windowless launcher
            self.assertEqual(launch.tool_command()[1:], [str(exe)])           # hooks and MCP need a python with stdin
        with mock.patch.object(launch, "installed", return_value=False), mock.patch.object(launch, "archive_path", return_value=None), \
                mock.patch.object(launch, "is_checkout", return_value=True):
            self.assertEqual(launch.entry_args(), [str(launch.checkout_entry())])
            self.assertEqual(launch.service_command()[1:], [str(launch.checkout_entry()), "daemon"])

    def test_a_checkout_is_not_packed(self):
        self.assertFalse(launch.packed())
        self.assertTrue(launch.is_checkout())

    def test_quote_wraps_every_part(self):
        self.assertEqual(launch.quote(["C:/Program Files/Py/python.exe", "a b"]), '"C:/Program Files/Py/python.exe" "a b"')

    def test_shebang_quotes_paths_with_spaces(self):
        self.assertEqual(appinstall.shebang("C:/Program Files/Py/pythonw.exe"), b'#!"C:/Program Files/Py/pythonw.exe"\n')
        self.assertEqual(appinstall.shebang("pythonw.exe"), b"#!pythonw.exe\n")


class AutostartAndUninstall(unittest.TestCase):
    def test_start_with_windows_runs_the_apps_service_command(self):
        from leosys import system
        exe = Path("C:/Apps/leo-sys/leo-sys.exe")
        with mock.patch.object(launch, "installed", return_value=True), mock.patch.object(launch, "install_exe", return_value=exe):
            self.assertEqual(system._daemon_command(), f'"{exe}" "daemon"')

    def test_setup_rewrites_an_older_start_with_windows_entry(self):
        """Seen on the real install: the entry existed (pointing at leo.py), so only a direct rewrite updates it."""
        calls = []
        fake_client = mock.MagicMock()
        fake_client.ensure_daemon.return_value = True
        fake_client.admin_token.return_value = "tok"
        fake_client._request.return_value = {"connected": True}
        from leosys import client as _real_client  # noqa: F401  (the package must have the attribute that gets replaced)
        with mock.patch("leosys.client", fake_client), \
                mock.patch("leosys.system.set_autostart", side_effect=lambda on: calls.append(on)), \
                mock.patch.object(setup_cmd, "create_shortcut", return_value="x.lnk"):
            setup_cmd.setup(app=False, hooks=False, mcp=False, autostart=True, shortcut=False, say=lambda m: None)
        self.assertEqual(calls, [True])

    def test_the_remove_helper_deletes_exactly_the_exe_and_only_an_empty_folder(self):
        folder = Path(tempfile.mkdtemp(prefix="leo-uninstall-"))
        exe, other = folder / "leo-sys.exe", folder / "keep-me.txt"
        exe.write_bytes(b"x")
        other.write_text("mine", encoding="utf-8")
        subprocess.run([sys.executable, "-c", appinstall._REMOVE_LATER, str(exe), str(folder)], timeout=60)
        self.assertFalse(exe.exists())
        self.assertTrue(other.exists() and folder.is_dir())          # another file in the folder: nothing else is touched
        other.unlink()
        exe.write_bytes(b"x")
        subprocess.run([sys.executable, "-c", appinstall._REMOVE_LATER, str(exe), str(folder)], timeout=60)
        self.assertFalse(folder.exists())                             # empty after the exe: the folder goes too


class LockedFile(unittest.TestCase):
    def test_a_short_lock_is_waited_out_and_a_long_one_is_reported(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.new").write_bytes(b"new")
        calls = []
        real = os.replace

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("locked by a hook")
            real(src, dst)
        with mock.patch("os.replace", flaky), mock.patch("time.sleep"):
            appinstall._replace(d / "a.new", d / "a.exe")
        self.assertEqual((len(calls), (d / "a.exe").read_bytes()), (3, b"new"))
        with mock.patch("os.replace", side_effect=PermissionError("still locked")), mock.patch("time.sleep"):
            with self.assertRaises(PermissionError):
                appinstall._replace(d / "b.new", d / "b.exe", tries=3)


class Hooks(unittest.TestCase):
    def test_hooks_recognise_the_app_and_the_script_form_but_not_other_tools(self):
        ours = ['"C:/Py/python.exe" "C:/work/leo-sys/leo.py" hook', '"C:/Py/python.exe" "C:/Users/u/AppData/Local/Programs/leo-sys/leo-sys.exe" hook',
                '"C:/Py/python.exe" -m leosys hook']
        theirs = ['"C:/Py/python.exe" "C:/work/divo-sys/divo.py" hook', '"C:/Py/python.exe" "C:/x/divo-sys.exe" hook', "echo hi"]
        for c in ours:
            self.assertTrue(setup_cmd._is_ours({"hooks": [{"command": c}]}), c)
        for c in theirs:
            self.assertFalse(setup_cmd._is_ours({"hooks": [{"command": c}]}), c)

    def test_hook_and_mcp_commands_use_the_entry(self):
        exe = Path("C:/Apps/leo-sys/leo-sys.exe")
        with mock.patch.object(launch, "installed", return_value=True), mock.patch.object(launch, "install_exe", return_value=exe):
            self.assertTrue(setup_cmd.hook_command().endswith('leo-sys.exe" hook'))
            self.assertEqual(setup_cmd.mcp_args()[1:], [str(exe), "mcp"])

    def test_switching_to_the_app_replaces_the_script_hooks_and_leaves_other_tools_alone(self):
        data = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": '"py" "C:/x/divo.py" hook'}]},
                                   {"hooks": [{"type": "command", "command": '"py" "C:/leo-sys/leo.py" hook'}]}]}}
        setup_cmd.merge_hooks(data, command='"py" "C:/Apps/leo-sys/leo-sys.exe" hook')
        commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(sorted(commands), sorted(['"py" "C:/x/divo.py" hook', '"py" "C:/Apps/leo-sys/leo-sys.exe" hook']))


class ProgramZip(unittest.TestCase):
    def test_program_zip_has_the_whole_package_the_panel_page_and_a_main(self):
        with zipfile.ZipFile(io.BytesIO(appinstall.program_zip())) as z:
            names = set(z.namelist())
            self.assertIn("__main__.py", names)
            self.assertIn("leosys/web/index.html", names)
            self.assertTrue({"leosys/cli.py", "leosys/engine.py", "leosys/appinstall.py", "leosys/launch.py"} <= names)
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
            self.assertIn("from leosys.cli import main", z.read("__main__.py").decode())


class DataFolder(unittest.TestCase):
    def test_environment_override_wins(self):
        with mock.patch.dict(os.environ, {"LEO_DATA": "C:/somewhere"}):
            self.assertEqual(config._data_dir(), Path("C:/somewhere"))

    @unittest.skipUnless(os.name == "nt", "Windows keeps its data in the user's app data folder")
    def test_windows_uses_the_app_data_folder(self):
        env = {k: v for k, v in os.environ.items() if k != "LEO_DATA"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config._data_dir(), launch.app_data_dir())

    def test_settings_are_picked_up_from_the_old_place_once(self):
        legacy, fresh = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (legacy / "settings.json").write_text(json.dumps({"brightness": 33, "claude": {"enabled": False}}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "LEO_DATA"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(settings, "DATA_DIR", fresh), \
                mock.patch.object(settings, "LEGACY_DATA_DIR", legacy):
            first = settings.Settings()
            self.assertEqual(first.snapshot()["brightness"], 33)
            self.assertFalse(first.snapshot()["claude"]["enabled"])
            first.patch({"brightness": 44})  # saving writes to the new place
            self.assertTrue((fresh / "settings.json").is_file())
            self.assertEqual(settings.Settings().snapshot()["brightness"], 44)  # and the new place wins from now on
            self.assertEqual(json.loads((legacy / "settings.json").read_text(encoding="utf-8"))["brightness"], 33)


class MigrateSettings(unittest.TestCase):
    def setUp(self):
        self.checkout, self.appdata = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (self.checkout / "data").mkdir()
        self.old = self.checkout / "data" / "settings.json"
        self.new = self.appdata / "leo-sys" / "settings.json"
        for p in (mock.patch.object(launch, "CHECKOUT", self.checkout),
                  mock.patch.object(launch, "app_data_dir", return_value=self.appdata / "leo-sys")):
            p.start()
            self.addCleanup(p.stop)

    def test_carries_the_old_choices_over_once(self):
        self.old.write_text('{"apps": {"matrix": "clock"}}', encoding="utf-8")
        said = []
        self.assertTrue(appinstall.migrate_settings(said.append))
        self.assertEqual(json.loads(self.new.read_text(encoding="utf-8")), {"apps": {"matrix": "clock"}})
        self.new.write_text('{"apps": {"matrix": "pc"}}', encoding="utf-8")   # a choice made since
        self.assertFalse(appinstall.migrate_settings(said.append))            # never overwritten
        self.assertEqual(json.loads(self.new.read_text(encoding="utf-8")), {"apps": {"matrix": "pc"}})

    def test_nothing_to_carry(self):
        self.assertFalse(appinstall.migrate_settings(lambda m: None))
        self.assertFalse(self.new.exists())


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_version_and_usage(self):
        self.assertEqual(self.run_cli("version"), (0, __version__ + "\n"))
        code, text = self.run_cli()
        self.assertEqual(code, 0)
        for word in ("setup", "build-installer", "uninstall"):
            self.assertIn(word, text)
        self.assertEqual(self.run_cli("no-such-command")[0], 1)


class RewriteShebang(unittest.TestCase):
    def test_splits_the_stub_from_the_program(self):
        path, stub, program = fake_app()
        got_stub, got_program = appinstall.split_app(path)
        self.assertEqual((got_stub, got_program), (stub, program))

    def test_only_the_python_changes(self):
        path, stub, program = fake_app()
        out = appinstall.rewrite_shebang(path, path.with_name("app.exe"), "C:/Program Files/Py/pythonw.exe")
        self.assertEqual(out.read_bytes(), stub + b'#!"C:/Program Files/Py/pythonw.exe"\n' + program)
        with zipfile.ZipFile(out) as z:  # still a valid program
            self.assertEqual(sorted(z.namelist()), ["__main__.py", "leosys/x.py"])
        again = appinstall.rewrite_shebang(out, out.with_name("again.exe"), "C:/A/pythonw.exe")  # and repeatable
        self.assertEqual(again.read_bytes(), stub + b"#!C:/A/pythonw.exe\n" + program)

    def test_the_source_file_is_left_alone(self):
        path, _, _ = fake_app()
        before = path.read_bytes()
        appinstall.rewrite_shebang(path, path.with_name("app.exe"), "C:/A/pythonw.exe")
        self.assertEqual(path.read_bytes(), before)


class DoubleClick(unittest.TestCase):
    def run_interactive(self, *, installed, running_from_install, accept=True, setup_code=0):
        installed_exe = Path("C:/Profiles/u/leo-sys/leo-sys.exe")
        here = installed_exe if running_from_install else Path("C:/Downloads/leo-sys-setup.exe")
        asked, shown, panel, ran = [], [], [], []

        def fake_setup(say=print, **kw):
            ran.append(True)
            say("  app: installed")
            return setup_code
        with mock.patch.object(launch, "installed", return_value=installed), \
                mock.patch.object(launch, "install_exe", return_value=installed_exe), \
                mock.patch.object(launch, "archive_path", return_value=here), \
                mock.patch("leosys.setup_cmd.setup", fake_setup):
            code = cli.interactive(ask=lambda t: asked.append(t) or accept, show=shown.append,
                                   open_panel=lambda: panel.append(True))
        return code, asked, shown, panel, ran

    def test_the_installed_app_just_opens_the_panel(self):
        self.assertEqual(self.run_interactive(installed=True, running_from_install=True), (0, [], [], [True], []))

    def test_a_fresh_setup_file_asks_then_installs_then_opens_the_panel(self):
        code, asked, shown, panel, ran = self.run_interactive(installed=False, running_from_install=False)
        self.assertEqual(code, 0)
        self.assertTrue(asked[0].startswith("Install leo-sys"))
        self.assertIn("never its LCD", asked[0])
        self.assertEqual((ran, panel), ([True], [True]))
        self.assertIn("installed", shown[0])

    def test_declining_does_nothing(self):
        code, asked, shown, panel, ran = self.run_interactive(installed=False, running_from_install=False, accept=False)
        self.assertEqual((code, shown, panel, ran), (0, [], [], []))

    def test_a_newer_setup_file_offers_an_update(self):
        _, asked, _, _, ran = self.run_interactive(installed=True, running_from_install=False)
        self.assertTrue(asked[0].startswith("Update leo-sys"))
        self.assertEqual(ran, [True])

    def test_a_failed_setup_is_reported_and_the_panel_is_not_opened(self):
        code, _, shown, panel, _ = self.run_interactive(installed=False, running_from_install=False, setup_code=1)
        self.assertEqual((code, panel), (1, []))
        self.assertTrue(shown)

    @unittest.skipUnless(os.name == "nt", "double-clicking is a Windows thing")
    def test_no_arguments_and_no_console_means_dialogs(self):
        with mock.patch.object(sys, "stdout", None), mock.patch.object(launch, "packed", return_value=True), \
                mock.patch.object(cli, "interactive", return_value=0) as dialogs:
            self.assertEqual(cli.main([]), 0)
        dialogs.assert_called_once()


def stub_available():
    try:
        return os.name == "nt" and appinstall.find_stub().is_file()
    except RuntimeError:
        return False


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def powershell(command):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                          text=True, timeout=60).stdout.strip()


def run_service_and_stop(exe, work):
    """Start `exe daemon` with a pretend keyboard on a free port, return (panel page, state, windows opened), stop it."""
    port = free_port()
    env = dict(os.environ, LEO_DEMO="1", LEO_DATA=str(work / "data"), LEO_PORT=str(port))
    proc = subprocess.Popen([str(exe), "daemon"], env=env, creationflags=0x08000000)
    try:
        page = state = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                c.request("GET", "/")
                page = c.getresponse().read().decode("utf-8")
                c.request("GET", "/api/state", headers={"X-Leo": "1"})
                state = json.loads(c.getresponse().read())
                break
            except OSError:
                continue
        windows = powershell("(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
                             f"$_.Path -like '*{work.name}*' }} | Measure-Object).Count")
        token = (work / "data" / ".token").read_text(encoding="utf-8").strip()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("POST", "/api/quit", "{}", {"X-Leo": "1", "X-Leo-Token": token, "Content-Type": "application/json"})
        c.getresponse().read()
        proc.wait(timeout=15)
        return page, state, windows
    finally:
        if proc.poll() is None:
            proc.kill()


@unittest.skipUnless(stub_available(), "needs Windows and pip's launcher stub")
class RealApp(unittest.TestCase):
    """Builds a real leo-sys.exe and checks what Windows sees, and that it runs with no window."""

    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix="leo-app-test-"))
        cls.exe = appinstall.build_exe(cls.work / "leo-sys.exe")

    def test_windows_shows_its_name_and_publisher(self):
        out = powershell(f"$v=(Get-Item '{self.exe}').VersionInfo; $v.FileDescription; $v.CompanyName; "
                         "$v.ProductName; $v.OriginalFilename; $v.FileVersion").splitlines()
        self.assertEqual([x.strip() for x in out], ["leo-sys", "leo-sys", "leo-sys", "leo-sys.exe", __version__])

    def test_the_icon_is_ours_not_the_stubs(self):
        out = powershell("Add-Type -AssemblyName System.Drawing; "
                         f"$b=[System.Drawing.Icon]::ExtractAssociatedIcon('{self.exe}').ToBitmap(); "
                         "$p=$b.GetPixel([int]($b.Width/2),[int]($b.Height/2)); '{0},{1},{2}' -f $p.R,$p.G,$p.B")
        self.assertEqual(out, "255,96,24")  # the critter's orange in the middle

    def test_the_program_runs_from_inside_the_file(self):
        done = subprocess.run([str(launch.console_python()), str(self.exe), "version"], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), __version__)

    def test_the_service_starts_from_the_app_serves_the_panel_and_opens_no_window(self):
        page, state, windows = run_service_and_stop(self.exe, self.work)
        self.assertTrue(page and "__TOKEN__" not in page, "the panel page was not served from inside the app")
        self.assertEqual(state["device"], "pretend keyboard")
        self.assertEqual(windows, "0")


@unittest.skipUnless(stub_available(), "needs Windows and pip's launcher stub")
class RealInstaller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix="leo-installer-test-"))
        cls.exe = appinstall.build_installer(cls.work / "leo-sys-setup.exe")

    def test_it_names_itself_as_the_setup_file(self):
        out = powershell(f"$v=(Get-Item '{self.exe}').VersionInfo; $v.FileDescription; $v.OriginalFilename").splitlines()
        self.assertEqual([x.strip() for x in out], ["leo-sys", "leo-sys-setup.exe"])

    def test_it_asks_windows_to_find_python_instead_of_hard_coding_a_path(self):
        stub, program = appinstall.split_app(self.exe)
        self.assertTrue(self.exe.read_bytes().endswith(program))
        self.assertIn(b"#!pythonw.exe\n", self.exe.read_bytes()[len(stub):len(stub) + 40])

    @unittest.skipUnless(shutil.which("pythonw.exe"), "pythonw.exe is not on PATH here")
    def test_it_runs_on_any_pc_that_has_python_on_its_path(self):
        page, state, _ = run_service_and_stop(self.exe, self.work)
        self.assertTrue(page, "the setup file could not find Python on PATH and start")

    def test_installing_it_pins_the_python_and_keeps_everything_else(self):
        python = Path(sys.executable).with_name("pythonw.exe")
        installed = appinstall.rewrite_shebang(self.exe, self.work / "installed" / "leo-sys.exe", python)
        self.assertNotEqual(installed.read_bytes(), self.exe.read_bytes())
        self.assertEqual(appinstall.split_app(installed)[0], appinstall.split_app(self.exe)[0])  # same stamped stub
        done = subprocess.run([str(launch.console_python()), str(installed), "version"], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), __version__)

    def test_the_build_installer_command(self):
        out, target = io.StringIO(), self.work / "cmd-out"
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["build-installer", "--out", str(target)]), 0)
        self.assertTrue((target / "leo-sys-setup.exe").is_file())
        self.assertIn("Python 3.9+", out.getvalue())


if __name__ == "__main__":
    unittest.main()
