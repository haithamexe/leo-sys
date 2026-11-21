# Changelog

## 0.3.1 (2026-11-21)

- **Master off switch** (*leo-sys on* in the panel's top bar, `leo-sys off` / `on`): leo-sys lets go of the keyboard
  completely, so the Leobog app can use it without both fighting over the screens. While off it stops every watcher, refuses
  Claude's tools, the hooks and scripts, and cannot be woken: the hooks and the MCP server no longer start the service. It
  is a file in the data folder, so it stays off across restarts and updates until switched on. Uninstall removes it.
- Tests no longer depend on whether Caps Lock is on.

## 0.3.0 (2026-11-19)

- **Modes**: a switch that sets a few settings together and puts them back when switched off. The first, *Claude and the
  screen*: Claude's status on the matrix and the keys following the screen.
- **More accurate screen colours**: denser sampling (layered windows, real pixels on high-DPI screens), each key averages its
  own patch of the screen, averaging and smoothing in linear light, and a white balance for the LEDs.

- **Typing speed** matrix app and **Typing heatmap** key light, counts only, opt-in: a count per key and keys per ten seconds
  in memory, never the order or any text, wiped on switching off, Reset or restart.
- **Taskbar alerts**: when a program flashes its taskbar button, the keys flash in its colour and the matrix shows its name.
  Off by default; ignore and only-these lists, a per-program cooldown, and a test button. Uses Windows' shell notification,
  keeps only the program's file name.
- **Camera and microphone in use**: a pulsing red key while a program uses the camera and a pulsing orange one while a
  program uses the microphone, each with its own switch and key. Read from Windows' privacy record, ignoring its stale
  entries.
- **Push command**: `leo-sys push text|colour|bar|clear` lets a script on this PC show text, a key colour or a progress
  bar (text can come from stdin), with its own switch in the panel, **off by default**; not subject to Claude's permissions, so anything that can run
  commands on the PC can use it once it is on.
- **Weather** and **Prayer times** matrix apps, each with its own switch and skipped in the rotation while off. Weather is
  opt-in, from Open-Meteo, and sends only your city's coordinates. Prayer times are calculated on your PC with the
  method you choose (Muslim World League, Jafari, Umm al-Qura and more).
- **Screen colours** key light: the keys follow the colours of your screen, in an "edges" mode (each key takes the
  colour at the nearest screen edge) or a "mirror" mode (a tiny picture of the screen). Only a 32 x 18 grid is kept, in
  memory, and the screen is read only while this key light is picked.
- **Smart keys.** Hold Ctrl, Alt, Shift or the Windows key and only the keys that do something with it glow, coloured
  by what they do. Built-in maps for VS Code, browsers, File Explorer and Photoshop plus the generic Windows
  shortcuts; add your own programs in `shortcuts.json`. Only the four modifiers and the focused program's file name
  are read: nothing you type is seen. Optional soft glow of a focused program's Ctrl shortcuts.

## 0.2.0

- **A real Windows app.** `leo-sys.exe` is one file with its own name, icon and version information, so Startup apps
  and Installed apps list **leo-sys** instead of "Python". The program lives inside the file; settings live
  separately in `%LOCALAPPDATA%\leo-sys`, so updating or reinstalling never touches them. Settings from a checkout's
  `data/` folder are carried over once.
- **`leo-sys-setup.exe`**, a download on each release. Double-click it: it installs the app, starts with Windows, adds
  the Claude Code hooks, the MCP server and a desktop icon, and opens the panel. A newer setup file updates an
  existing install and keeps your settings. It needs Python 3.9+ on PATH and is not code-signed; each release carries
  a `.sha256` file. The release workflow builds it on a clean Windows runner.
- Installed from the app, Uninstall appears under **Settings, Apps, Installed apps**.
- Apps for the matrix: clock, PC monitor (CPU, memory, GPU, disk, network, ping), now playing, sound level, your own
  album of pictures and GIFs, a message, and the Claude status; a rotation of any of them.
- Key lights: breathing, one colour or rainbow, with the Caps Lock sign, the volume on the number row and a red key
  while the microphone is muted. Colours are corrected for the LEDs so orange stays orange.
- MCP tools `now_playing` and `media_control`, behind panel switches.
- **leo-sys only ever drives the matrix and the key lights.** It never writes to the keyboard's LCD or sets its clock;
  use the Leobog app for those.

## 0.1.0

- Claude Code's live status on the LEOBOG AMG65's 63 x 5 LED matrix and key lights: hooks, MCP tools (`set_face`,
  `show_text`, `draw_pixels`, `set_keys`), a local control panel and one-step setup.
