# leo-sys

**Claude Code's live status on a LEOBOG AMG65 keyboard.** The keyboard's 63 × 5 LED matrix shows a small Claude
critter and what it is doing (THINKING, EDITING, TESTING, NEEDS YOU, DONE). The key lights breathe, sweep or flash
along with it. A local panel lets you decide what Claude may do there. When Claude is quiet the keyboard becomes a
live display: clock, PC monitor, now playing, sound level, an album of your own pictures and more.

**leo-sys never writes to the keyboard's LCD or sets its clock.** It only drives the matrix and the key lights; use
the Leobog app for the LCD and the clock.

Windows, Python 3.9+, **no third-party packages**. Talks to the keyboard over USB HID with `ctypes`. Nothing leaves
your machine.

## Quick start

Plug the keyboard in **by cable** (the screens only work wired) and close the Leobog app from its tray icon, then
either:

- **Download `leo-sys-setup.exe`** from the [latest release](https://github.com/haithamexe/leo-sys/releases/latest) and
  double-click it. It asks once, then installs leo-sys and opens the panel. (Needs Python 3.9 or newer on your PATH.
  The file is not code-signed, so Windows SmartScreen may warn: choose **More info, Run anyway**. Check it against the
  `.sha256` file if you like.) Running a newer setup file later updates the app and keeps your settings.
- or, from a checkout: `python leo.py setup`.

Either way, setup puts **`leo-sys.exe`** in `%LOCALAPPDATA%\Programs\leo-sys\` (no administrator rights; it shows as
"leo-sys", with its own icon, in Startup apps and Installed apps), starts the background service, makes it start with
Windows, adds the Claude Code hooks to `~/.claude/settings.json` (keeping a backup and everything already there,
including other tools' hooks), registers the MCP server and puts a **leo-sys** icon on the desktop. Restart Claude Code
afterwards. Settings and logs live in `%LOCALAPPDATA%\leo-sys`, apart from the program, so updates never touch them.

Open the panel from the desktop icon, or `python leo.py gui` (http://127.0.0.1:47841/). No keyboard? `python leo.py demo`
runs the panel against a pretend one. Remove everything from **Settings, Apps, Installed apps**, or with
`leo-sys.exe uninstall`.

## What it shows

| Claude is | Matrix | Keys |
| --- | --- | --- |
| thinking | critter looking up, THINKING, dots growing | slow amber breathing |
| using a tool | eyes darting, EDITING / TESTING / READING..., a scanner | orange wave left to right |
| waiting on you | arms up, NEEDS YOU, flashing | flashing yellow |
| finished | DONE with sparkles, for a minute | a green fade, once |
| a tool failed | a short red flash | a red blink, once |
| idle / nothing open | the keyboard's own effect | the keyboard's own lighting |

With several sessions open, the most urgent one is shown and each session gets a dot at the right edge.

The keyboard goes back to its own effects about a second after frames stop, so the master switch, quiet hours and
an idle Claude simply stop sending.

## Modes and screen-colour accuracy

**Modes** (Apps page): a few settings switched together, with a way back. *Claude and the screen* puts Claude's status on
the matrix and makes the keys follow the colours of your screen (Claude's own key effects are switched off while it is
on, or they would cover the screen). Switching it off restores what you had before.

Screen colours are made as faithful as a 32 x 18 picture allows: the screen is sampled densely (layered windows and real
pixels on high-DPI screens included), each key averages its own patch of the picture, and both that averaging and the
smoothing are done in linear light, the way light really adds up, so mixed colours stay true and flashes do not turn
muddy. Colour boost and brightness boost default to 1.5 to make dull screens vivid; **1 and 1 are the most faithful**.
A **white balance** (percent of red, green, blue) corrects any tint of the keyboard's LEDs.

## Typing speed and key heatmap

Two things that need to count your key presses, so they are **off by default**, behind one switch (**Count key presses**,
Apps page, Typing speed and heatmap card): the **Typing speed** matrix app (words per minute, and a bar for each of the
last six minutes in ten-second stretches) and the **Typing heatmap** key light (every key coloured from blue, rarely, through
green and yellow to red, the most pressed).

**Counts only.** While the switch is on, leo-sys asks Windows about 100 times a second (a little less on older Python) which
keys are down right now and counts a key each time it goes from up to down (holding a key counts once). In memory only, it keeps
a count for each key, how many typing keys were pressed in each ten-second stretch of the last six minutes, and the best speed.
It never keeps which key followed which, the time of any single key press (only those ten-second tallies), or any text; nothing
is saved to disk or sent anywhere, and the panel's data never carries the per-key counts. The one place they show is the Typing
heatmap key light, which colours each key by its relative use (and the panel's picture of the keyboard shows those colours).
Switching the switch off, pressing **Reset the counts**, or restarting leo-sys wipes everything. It is not a keyboard
hook, but security software may still notice a program asking about letter keys. Speed is the characters typed over the last 20 to
30 seconds, as words of five; modifiers, arrows, Enter and Backspace do not count as characters.

## Taskbar alerts

When a program flashes its taskbar button (Discord, WhatsApp, a browser tab with a message, a mail program), the keys
flash in its colour and the matrix scrolls its name. Known programs get their own name and colour (Discord blue-purple,
WhatsApp green, Telegram blue, Slack, Teams, Outlook, Zoom, browsers...); any other shows its file name in orange. It is
**off by default**; the switch is in the **Taskbar alerts** card on the Apps page.

How it knows: Windows tells programs that register as a "shell hook window" when a taskbar button starts flashing. That is
a notification about windows, not a keyboard hook: nothing typed is seen, and leo-sys keeps only the file name of the program
that flashed, never the window's title (which can contain message text). With the switch off it does not listen at all.

Some programs flash all the time (a Remote Desktop window on one PC did), so each program can raise an alert only once per
cooldown (60 seconds by default), you can list programs to **ignore** (the card shows the programs it has seen flashing, each
with an Ignore button) or give an **only these** list, and there is a **Test an alert** button. Your own pushes and picks
come before an alert; an alert comes before Claude's status. `python tools/test_flash.py` makes a window flash on purpose.

## Camera and microphone in use

Two optional key lights: a **pulsing orange key while a program is using the microphone** and a **pulsing red key while a
program is using the camera**. Each has its own switch and its own key (Apps page, Key lights card). They read Windows'
own privacy record (the list behind Settings, Privacy, Microphone and Camera), once or twice a second, only while one of
the two switches is on. Nothing is hooked. What is read is the record's program names and times, and, only when a program
looks in use, the file paths of the programs running now (to check it, nothing kept). The panel says which programs are using
them.

That record has stale entries (a program killed or updated in the middle of a call keeps looking "in use"), so a program
only counts if it started after this boot and, for ordinary programs, a process is really running from that file (store apps
cannot be checked that way). Like the muted-microphone key,
these show while leo-sys drives the key lights, not over the keyboard's own lighting.

## Scripts: the push command

Anything on this PC can show text or a colour, for example when a build finishes:

```
leo-sys push text "BUILD OK" --colour green --seconds 10
leo-sys push colour red --seconds 5
leo-sys push bar 40 --colour blue
leo-sys push clear
echo Disk almost full | leo-sys push text - --colour amber
```

(`python leo.py push ...` from a checkout; the panel's **Push from scripts** card shows the exact command for your
install.) Colours are names (red, green, blue, orange, yellow, white, purple, cyan, pink, amber) or `#rrggbb`. It exits with
0 when something was shown, so scripts can tell. From other languages, POST
`{"source": "push", "req": {"cmd": "text", "text": "BUILD OK", "colour": "green"}}` to `http://127.0.0.1:47841/api/cmd`
with the header `X-Leo: 1` (only this PC can reach it). A push stays for 10 seconds unless it says otherwise (1 to 300),
then everything is handed back. Pushes are **off by default**: switch on **Allow pushes from scripts** in the panel first.
Anything that can run commands on this PC can then push, Claude Code included, and Claude's own switches (master, tools,
quiet hours, rate limit) do not limit it; the one switch turns every push off.

## Smart keys

Hold **Ctrl, Alt, Shift or the Windows key** and only the keys that do something with it glow, coloured by what they
do: orange edit, blue files, green move, purple view, amber tools, red careful (Win+L, Ctrl+W). Each program can have
its own map; VS Code, browsers, File Explorer and Photoshop are built in, and anything else uses the generic Windows
shortcuts. The keyboard has no function row, so only the keys it really has can light.

It polls which of the four modifiers is down and the file name of the focused program, and nothing else: no keyboard
hook, no letters, nothing typed is ever seen. It works while leo-sys drives the keys and no Claude effect or picked
colour is showing; on the keyboard's own lighting the other keys go dark while a modifier is held. The panel's
**Smart keys** card (Apps page) switches it, shows what leo-sys sees right now, and can **create an example
`shortcuts.json`** in `%LOCALAPPDATA%\leo-sys`. Add your own programs there:

```json
{
  "categories": {"edit": "#ff6018"},
  "apps": {
    "notepad": {"name": "Notepad", "exes": ["notepad.exe"],
                "keys": {"ctrl": {"edit": "C X V Z A H", "file": "S N O P", "nav": "F G"},
                         "ctrl+shift": {"file": "S N"}}}
  }
}
```

A combo is modifiers joined by `+` (`ctrl`, `alt`, `shift`, `win`, in any order). Under each, list keys by category
(`edit`, `file`, `nav`, `view`, `tool`, `danger`, or one you define in `categories` with a colour). Keys are written
plainly: letters, `0`-`9`, `- = [ ] ; ' , . / \`, `Tab`, `Esc`, `Space`, `Enter`, `Backspace`, `Del`, `Home`, `PgUp`,
`PgDn`, `Up`, `Down`, `Left`, `Right`. Your file's apps replace a built-in one with the same name; mistakes are
skipped and listed in the panel. A "program" is its `.exe` file name, shown as **Now** in the card.

## Screen colours

Pick **Screen colours** in the Key lights list (Apps page) and the keys follow the colours of your screen, like a lamp
strip behind a monitor. In **edges** mode each key takes the colour at the nearest edge of the screen; in **mirror**
mode the keyboard is a tiny picture of the whole screen. Colour boost, brightness boost, smoothing and the number of
updates per second are in the **Screen colours** card.

What it reads, in plain words: Windows shrinks the main monitor to a 32 × 18 grid of colours about 10 times a second
and only the latest grid is kept, in memory. No picture is saved, nothing is sent anywhere, and the screen is not read
at all unless Screen colours is the picked key light (or for a few seconds after you pick something else). It costs
about 6% of one CPU core while on. Protected video may show as black, and only the main monitor is read.

## Weather and prayer times

Two more apps for the matrix, each with its own switch (both start switched off; a switched-off one is skipped in the
rotation). Pick your city, or type coordinates, in the **Weather and prayer times** card on the Apps page.

- **Weather**: an icon, the temperature (colour from cold blue to hot red), today's high and low, and a bar for each of the
  coming hours. Data comes from [Open-Meteo](https://open-meteo.com): free, no account. This is **opt-in** and the only
  time leo-sys uses the internet besides the ping app: each request carries your city's coordinates (rounded to about a
  kilometre) and, like any web request, lets the service see your internet address. At most one request every 30
  minutes, and only while the Weather app is on the matrix or in the rotation (it stops about a minute after). Celsius
  or Fahrenheit.
- **Prayer times**: the next prayer, its time and the time left (green, amber under 30 minutes, red under 10). They are
  **worked out on your PC** from the date and your city, with no internet and nothing sent anywhere. Choose the
  calculation method (Muslim World League, Jafari, Umm al-Qura, Egyptian, Karachi, ISNA or Tehran), standard or Hanafi
  Asr, which prayers count, and minutes to add to each time to match a timetable you trust. Times follow this PC's
  time zone. Checked against a public prayer-times service for Baghdad: every time matched to the minute except Asr, which
  that service gives about 2 minutes later than the precise solar formula.

## Claude's side

- **Hooks** make the keyboard follow Claude Code automatically.
- **MCP tools** `set_face`, `show_text`, `draw_pixels`, `set_keys` and `leo_status`, all behind the panel's switches:
  a master switch, one per tool and per face, a rate limit, quiet hours and a Pin.

## Commands

```
python leo.py setup | uninstall     install or remove the app, hooks, MCP server, startup entry and icon
python leo.py build-installer       build dist/leo-sys-setup.exe (what a release ships)
python leo.py gui                   open the panel
python leo.py start | stop          run or stop the background service
python leo.py demo                  try the panel without the keyboard
python leo.py face <mood>           idle thinking working attention done error sleep splash
python leo.py text HELLO            show a message on the matrix
python leo.py status                what is connected and showing
python tools/play_session.py        play a pretend Claude session (about 45 s) to watch it react
```

## How it works

| File | Role |
| --- | --- |
| `leosys/hid.py` | Windows HID access with `ctypes` (the keyboard's two vendor channels) |
| `leosys/matrix.py`, `keys.py` | The two wire formats: live matrix frames and live key colours |
| `leosys/faces.py`, `keyfx.py` | The art: critter, 3 × 5 font, faces, key effects |
| `leosys/engine.py` | Tracks sessions, decides what to show, runs the one thread that talks to the keyboard |
| `leosys/policy.py` | The permission rules, as a pure function |
| `leosys/server.py`, `web/index.html` | The local panel and its JSON API (127.0.0.1 only, per-run token) |
| `leosys/hook.py`, `mcp_server.py`, `setup_cmd.py` | Claude Code hook, stdio MCP server, one-step install |
| `leosys/cli.py`, `launch.py`, `appinstall.py` | The command line, where leo-sys lives and how to start it again, building and installing `leo-sys.exe` (pip's launcher stub + icon and version info + the program as a zip) |

The protocol was decoded from a USB capture of the official driver: [docs/PROTOCOL.md](docs/PROTOCOL.md). leo-sys
only sends the live-frame and key-colour commands seen there, never settings, firmware, clock or LCD commands.

## Development

```
python -m unittest discover -s tests
```

No keyboard and no network needed.
