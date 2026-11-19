# leo-sys roadmap

leo-sys started as Claude's live status on the AMG65. From here it becomes a general **realtime display for the
keyboard**, with Claude status as one source among many.

Planned 2026-11-19. Standard library only, as before. Each phase is tried on the real keyboard before
the next starts.

## What the hardware gives us

| Output | Speed | Stored? | Good for |
| --- | --- | --- | --- |
| LED matrix 63 × 5 | up to 19 frames/s | no (falls back to its own effect ~1 s after frames stop) | anything live |
| Key lights, 69 keys | up to 35 updates/s | no | indicators, meters, per-key meaning |
| LCD 240 × 135 | one picture ≈ 2 s | **yes**, flash | **not used: leo-sys never writes to the LCD** (a rule of the project) |
| Time sync (op 0x28) | once | the keyboard keeps its own clock | **not used: it is the clock the LCD watch shows** (same rule) |

Finding that shapes phase 1: the keyboard's "CPU view" in the Leobog app is **drawn by the app** and streamed as live
matrix frames (op 0x35). The keyboard itself only needs the time. So replacing the app means: send the time, and draw
our own monitor screens.

Not available: the keyboard's own brightness and built-in effect switching (settings op 0x13, not decoded, kept away
from on purpose).

## Phase 1 — foundation and replacing the Leobog app (done, checked on the keyboard 2026-11-19)

Built: matrix apps (clock, message, critter, rotation), key bases (keyboard's own, one colour, breathing,
rainbow), the Caps Lock indicator on top, time sync, and the panel's Apps page. The clock, rainbow and Caps
indicator were confirmed on the device.


- Turn the engine into **sources** with priorities: alerts > Claude (when active) > the chosen matrix app or rotation.
  Key lights become **layers**: a base effect, indicators drawn on top (volume, mic, Caps).
- Time sync was built and then removed by decision (the clock belongs to the LCD, which leo-sys never touches).
- Panel: an "Apps" page to pick what the matrix shows when Claude is quiet, and a rotation.
- The Leobog app is still needed for the LCD and the keyboard's clock; it must be closed while leo-sys runs.

## Phase 2 — PC monitor (done, checked on the keyboard 2026-11-19; the clock itself shipped in phase 1)

Built 2026-11-19 and confirmed on the keyboard: `leosys/pcstats.py` reads Windows' performance counters
(PDH, English names so any Windows language works), memory, and ping through IcmpSendEcho; nothing is read
unless an app on the matrix or in the rotation needs it. Apps: PC (four bars), CPU, Memory, GPU, Disk, Network,
Ping. The Apps page has a PC monitor card (ping host, bits or bytes, live readings).


- Clock with seconds and date; CPU and RAM bars; network up/down graph; GPU use; ping graph; disk use.
- Readings through Windows APIs with ctypes (GetSystemTimes, GlobalMemoryStatusEx, GetIfTable2, PDH for GPU).
- CPU temperature: not readable without admin or a kernel driver; shown only if Windows offers it.

## Phase 3 — media, and a media MCP for Claude (done, checked on the keyboard 2026-11-19)

Built and confirmed 2026-11-19: `leosys/media.py` reads Windows' media sessions (WinRT
over ctypes; interface IDs and method order read from Windows' own metadata) and Core Audio. Matrix apps Now playing
and Sound level; volume on the number row and a red mic key; MCP tools `now_playing` and `media_control`.
Not done here: the spectrum visualizer (the level meter covers it for now). The matrix font is Latin only, so Arabic
titles fall back to the artist or the player's name.
Also from testing: apps now come first and Claude's status takes over only when it needs you
(`scene.claude_takes_over`: alerts by default, or always, never), and matrix and key colours get LED gamma
correction (`colour_gamma` 2.2) because the linear LEDs washed orange out to pale yellow.


- Now playing from Windows' own media sessions (the same source as the volume pop-up), so Spotify, YouTube and other
  sites in any browser, VLC and most players work: title and artist scrolling on the matrix, with a progress line.
- MCP tools for Claude: `now_playing` (what is playing, in which app, how far in) and `media_control` (play, pause,
  next, previous, volume up and down, mute). Controls go through the media keys, so they work on whatever is playing.
- Volume bar on the number row when the volume changes; a red key while the microphone is muted (Core Audio over
  raw COM with ctypes).
- Visualizer on the matrix and keys: a level meter first (audio peak meter), a spectrum later if worth the work.

## Phase 4 — matrix album (done 2026-11-19; the LCD half was dropped)

`leosys/wic.py` decodes GIF, PNG, JPEG and BMP with Windows' own imaging component (GIF disposal handled, scaled and
fitted: contain, cover or stretch). `leosys/albums.py` plays a folder on the matrix: stills for N seconds, GIFs at their
own speed, optional shuffle, built in the background and streamed live (no memory wear). Panel: the Matrix album card
on the Apps page, with a folder chooser. Checked on the keyboard.

**The LCD half was removed by decision.** An LCD album and an LCD picture feed were built and tested on the keyboard:
uploads worked, but the stored delay unit is about 2 ms (a frame lasts at most about half a second, 16 frames per
transfer), so a stored slideshow was never possible, and the uploads kept misbehaving in testing. The decision: never
overwrite or change the LCD; only the keys and the matrix are touched. All LCD code (upload, pictures,
the data channel, panel controls) is gone and a test guards it.

## Phase 5 — cancelled

The GIPHY feed for the LCD, with the LCD rule above.

## Phase 6 — a real Windows app (done 2026-11-19, version 0.2.0)

Built and installed: `leosys/launch.py`, `appinstall.py` and `cli.py`. `leo-sys.exe` is pip's windowless launcher stub with leo-sys's icon and version information
and the program appended as a zip; it installs to `%LOCALAPPDATA%\Programs\leo-sys\`, lists itself in Installed
apps, keeps settings in `%LOCALAPPDATA%\leo-sys`, and the hooks, MCP server, start-with-Windows entry and desktop
icon all run it. `python leo.py build-installer` makes `leo-sys-setup.exe`; `.github/workflows` run the tests and build
the installer on a clean Windows runner for each release. Found while installing for real: setup now rewrites an
older start-with-Windows entry instead of leaving it on the old script.

- `leo-sys.exe`: one file with its own name, icon and version information, built from pip's small windowless launcher
  stub with the program appended as a zip. Installed in
  `%LOCALAPPDATA%\Programs\leo-sys\` without admin rights, listed under Settings, Apps, Installed apps with an
  Uninstall, and shown as **leo-sys** (not "Python") in Startup apps.
- Settings, token and logs move to `%LOCALAPPDATA%\leo-sys`, so reinstalling or updating never touches them; a
  checkout's `data/` folder is brought across once.
- Setup, the Run entry, the hooks, the MCP server and the desktop icon point at the exe instead of `leo.py`.
- `leo-sys-setup.exe`: a double-click installer that also updates an existing install and keeps the settings. Built by
  a release workflow on a clean Windows runner, with a `.sha256` file; not code-signed, so SmartScreen may warn.
- Still runs on the installed Python (3.9 or newer on PATH).

## Phase 7 — smart key lights (done, confirmed on the keyboard 2026-11-19)

`leosys/winstate.py` polls the four modifiers and the focused program's .exe name; `leosys/smartkeys.py` holds the
built-in maps (VS Code, browsers, Explorer, Photoshop, generic Windows) and loads your `shortcuts.json`; the engine
overlays it on the key base (not over Claude's effects or a picked colour). Panel: the Smart keys card. Not done: a
map editor in the panel (the JSON file is the editor for now).

- Per-program shortcut maps (VS Code, browsers, Explorer, Photoshop, more by JSON): their useful keys light up when
  the program is focused.
- Hold Ctrl, Alt, Shift or Win: only the keys that do something with that modifier glow.
- Foreground window and modifier state by polling (no keyboard hook, nothing typed is recorded).

## Phase 8 — screen-colour keys (done, confirmed on the keyboard 2026-11-19)

`leosys/screen.py`: GDI shrinks the main monitor to a 32 x 18 grid (a fast rough shrink, then an averaging one,
about 14 ms), a watcher thread reads it only while the Screen colours key base is in use and keeps only the latest
grid; `edges` and `mirror` modes map it to the keys, with colour and brightness boost and smoothing. About 6% of
one core at 10 updates a second. Not done: other monitors, and capturing games in exclusive full screen.

- The key lights follow the colours at the edges of the screen, about 10 times a second (GDI capture, scaled down).

## Phase 9 — city info (done, confirmed on the keyboard 2026-11-19)

Weather and prayer times, Baghdad by default, Muslim World League by default (Jafari and the others stay selectable);
no USD/IQD rate. `leosys/prayer.py` works out prayer times on the PC (checked against a public service), `weather.py`
fetches Open-Meteo only while the Weather app is switched on and in use, `cityapps.py` draws both screens, and the
Weather and prayer times card on the Apps page has a switch for each, the city, the method and per-prayer switches and
offsets. Not done: other weather providers, an adhan sound (leo-sys never makes sound), the USD/IQD rate.

## Phases 10 to 13 — four ideas picked from the list (built and checked 2026-11-19)

Each has its own on/off switch, and only the matrix and the key lights are ever touched.

- **10, a local command for scripts**: `leo-sys push text "BUILD OK" --colour green --seconds 10`, `push colour red`,
  `push bar 40`, `push clear`, text can come from stdin. A switch in the panel turns the whole thing off.
- **11, camera and microphone in use**: Windows records which program is using them (the privacy settings' own list);
  a key lights while one is. Read from the registry, no hook.
- **12, taskbar-flash alerts**: when Discord, WhatsApp or any program flashes in the taskbar, the keys flash in its colour
  and the matrix shows its name. Uses Windows' shell notification for flashing windows (not a keyboard hook).
- **13, typing speed and key heatmap**: counts only (how many times each key was pressed, keys per 10 seconds), in memory
  only, opt-in, with a reset. Polls key state; never records which key follows which or any text.

## More ideas, not scheduled

- Timers and pomodoro.
- A pixel editor in the panel for the matrix (GIF import is now phase 4).
- A tiny endless-runner game on the matrix.
