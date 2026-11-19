# LEOBOG AMG65 — USB protocol notes

Decoded on 2026-11-18 from one USBPcap recording of the official Leobog driver
(`C:\Program Files (x86)\LEOBOG AMG65\DeviceDriver.exe`, firmware
`1.14LCD_HFD80CP100_V1.31_20250215_0x01DD`). Everything here was **observed**;
nothing has been sent to the keyboard by our own code yet.

Status words: **confirmed** = seen and checked against a known input;
**likely** = consistent with the data but not proven; **unknown** = seen, meaning unclear.

## Device

| | |
|---|---|
| USB id | `0C45:800A`, product string `LEOBOG AMG65` (wired only — screens work only on cable) |
| Command channel | interface 2, HID usage page `0xFF68`, 64-byte reports. OUT endpoint `0x03`, IN `0x84` |
| Bulk image channel | interface 3, HID usage page `0xFF67`, 4096-byte output reports on endpoint `0x06` |
| Report ID | none — the 64 bytes start directly with the command byte |

The official driver must be **closed** while our tool drives the screens; both
would write to the same channels.

## Transaction shape (confirmed)

Every operation is a short conversation on the command channel. Each OUT report
gets a reply on `0x84`.

```
04 18                         → 04 18 00 01        begin
04 <op> 00 00 00 00 00 00 <n> → 04 <op> 00 01      op + number of data chunks that follow
<data chunks>                 → 04 41 00 01 each   (64 B on 0x03, or 4096 B on 0x06)
04 02                         → 04 02 00 01        end
04 f0                         → 04 f0 00 01        (only after flash writes: save)
```

All multi-byte payloads end with the marker `aa 55`.

## 1. Live matrix frame — op `0x35` (confirmed) ← the realtime path

The dot matrix is **63 columns × 5 rows = 315 LEDs**, LED 0 = top-left.
On the wire the LEDs are **not** in plain row order. The matrix is five panels side
by side, 14, 14, 14, 14 and 7 columns wide, and each panel is numbered row by row:

```
panel = x // 14 (x < 56), else 4
index = panel * 70 + y * panel_width + (x - panel * 14)
```

Confirmed three ways:
- the driver holds this exact 315-entry table (4-byte ints at file offset 1685488 of `DeviceDriver.exe` v1.31);
- the recorded CPU-view frames decode cleanly with it;
- a test bar sent with plain row order showed up as scattered bars.

(The driver's saved animations in `db/ledlayer_v2.data` use plain row order. The
driver applies the table before sending.)

```
04 18
04 35 00 00 00 00 00 00 0f      n = 15
15 × 64-byte chunks             = 960-byte frame buffer:
    bytes 0..944   315 × (R, G, B), one byte each, 0..255, at 3 × index (see map above)
    byte  945      00
    bytes 946..947 aa 55
    bytes 948..959 00
1 × 64-byte chunk of zeros      (driver always sends it; reply is 00 00 00 ff)
04 02
```

No `04 f0`: it's a RAM-only preview, with **no flash wear**. The driver streams it at
about **9 frames a second** (one frame every ~110 ms) for the editor preview, the
music visualizer and the CPU view. Our own sender reached **19 frames a second**
(about 47 ms per frame), and the keyboard acknowledged every chunk.

When the stream stops, the last frame stays frozen for a short while, then the
keyboard goes back to its own built-in effect (seen 2026-11-18). So a face that
should stay up has to be **resent regularly**. The exact timeout hasn't been
measured; resending about once a second is cheap (≈ 47 ms per frame).

> **leo-sys never sends any of section 2.** These notes are kept as a record of what the capture and our tests
> showed. Findings from our own tests (2026-11-19): the animation type byte is `0x0d` (`0x13` was ignored), the
> delay unit is about **2 ms** (a 600-unit loop took about 1.1 s, so a frame lasts at most about 0.5 s), the
> block count is one byte (so at most 16 frames per transfer), and the first block's answer can be missing right
> after another upload or while another program talks on the same channel.

## 2. LCD image or animation — op `0x72` (confirmed format, flash write)

The LCD is **240 × 135**, pixels **RGB565 little-endian** (red = `00 f8`,
blue = `1f 00`), row-major, top-left first. A 240×240 red PNG arrived scaled to
135×135, centred at column 52, black around it.

```
04 18
04 72 <k> 00 00 00 00 00 <n>    n = number of 4096-byte blocks
n × 4096-byte blocks on endpoint 0x06, each one answered on 0x84 with 04 41 <block number> 01
    (1, 2, 3...) once the keyboard has stored it: WAIT for that answer before the next block.
    The first takes ~0.3 s, the rest ~0.08 s. Sent without waiting, blocks are dropped and the
    LCD's progress stops part way (we saw 75% = 12 of 16).
  blocks hold:
    bytes 0..255   header: [0] = frame count, [1..count] = per-frame delay, rest ff
    then count × 64 800 bytes of RGB565 (240×135×2)
    padded with ff to a multiple of 4096
04 02
```

| Upload | header | k | n |
|---|---|---|---|
| 1 still frame | `01 19 ff…` (delay 0x19) | `09` | `0x10` (16 blocks) |
| 3-frame GIF, 128×128 | `03 fa fa fa ff…` (delay 0xfa each) | `0d` | `0x30` (48 blocks) |

- `k` is a **type flag**, not a frame count: `0x09` = a still, `0x0d` = an animation (one bit apart). Measured
  2026-11-19: a 6-frame upload with `k = 0x13` was accepted block by block but the LCD kept its old picture; the same
  upload with `k = 0x0d` played all 6 frames.
- **Delay unit ≈ 2 ms (corrected).** The first guess of 0.1 s came from a misread answer. Measured by hand: a loop
  of delay bytes 50, 100, 200, 250 (600 units) took about 1 to 1.1 s. That fits the driver's own "Allow 30-500M
  delay modification": a frame lasts at most about 0.5 s.
- **One byte counts the blocks.** Everything seen has `n` as a single byte (16, 48; our 95-block test worked). A frame
  is 64 800 bytes, so 16 frames = 254 blocks is the most one transfer can count. leo-sys refuses to send more. A
  wider count (maybe big-endian over the last two bytes) is a guess nobody has tried; the driver's own text says
  "Animation supports up to 260 frames", which cannot fit one byte, so the driver must know something we do not.
- The first block's answer can take a long time (up to ~20 s seen) or not come at all right after a previous
  upload; a retry about 5 s later worked every time.
- Upload speed: about 0.14 s a block, 254 blocks in ~35 s. The matrix and key streams cannot run at the same time
  (same command channel), so they pause during an upload.
- From the driver's language file (`language/1033.lan`): "Allow 30-500M delay modification" (its LED-matrix animation
  editor), "Boot Animation supports up to 200 frames", "Animation supports up to 260 frames", and the app reads a
  per-keyboard "Maximum Frames" (so the limit may come from the keyboard, perhaps through an info read we have not
  seen).
- This is stored on the keyboard, so it is **not** a realtime path. Keep LCD
  updates rare (on a status change, not every second) to spare the flash.

> leo-sys does not send section 3 either: the clock is the one the LCD watch shows.

## 3. Time + PC stats — op `0x28` (likely)

Sent twice when the driver opened (21:50:40 and 21:50:42 on 2026-11-18).

```
04 28 00 00 00 00 00 00 01
64-byte chunk: 00 00 5a YY MM DD hh mm ss 00 WD 00 00 a b c d … aa 55   (aa 55 at bytes 62-63)
```

`1a 0b 12 15 32 28` = 26-11-18 21:50:40 (confirmed). `WD` = 03 = Wednesday (likely).
The second send carried `02 25 00 24`, which looks like CPU % / CPU °C / GPU % / GPU °C
(the driver's `data.ini` showed CPU 37 °C, GPU 35 °C). These are likely the numbers the
keyboard's own CPU/GPU matrix effects display. `5a` is unknown.

## 4. Per-key backlight — op `0x20` (confirmed)

```
04 18
04 20 00 00 00 00 00 00 08      n = 8
8 × 64-byte chunks = 512 bytes: up to 128 entries of [light index, R, G, B], zero-padded
04 02
```

- No `aa 55` trailer and no `04 f0` save, so it's live like the matrix frames.
- The driver resent it every 70–90 ms during the recording: grey `2c 2c 2c` on most
  keys, red `fe 00 00` on a few.
- There are 69 entries, one per key. The light indices come from the driver's
  `KeyboardLayout.xml`, and the table is copied into `leosys/keys.py` along with each
  key's row and position, so key waves can be drawn left to right.
- Indices 13 and 14 belong to the volume controls (`VOLINC`/`VOLDEC`).
- Our sender: about 29 ms per update, up to 35 updates a second, every chunk
  acknowledged. Confirmed on the keyboard on 2026-11-19: all keys orange, then a
  left-to-right wave, both showed as sent, with no lighting mode change needed. Not yet
  noted: what the keys go back to when updates stop (the matrix goes back to its own
  effect).

## Seen but not needed — do not send

| Op | What it looked like | Why we leave it |
|---|---|---|
| `0x33` + `04 f0` | matrix animation saved to the keyboard (1 or 60 blocks on 0x06) | flash write; the live stream covers our needs |
| `0x13` + `04 f0` | settings write (`0b 5f 01 fd … 04 00 01 … aa 55`), byte 9 varied 04/00/05 | flash write, meaning not pinned down |
| `0xf6` | read of 8 pages of per-key values | read only, unclear purpose |
| anything firmware | the driver folder also ships a firmware updater | never touch |

## How this was decoded

`tools/re/` holds the scripts: `pcap.py` reads USBPcap files, `tx.py` groups
transactions, `dump.py` prints a time window, `layouts.py` renders matrix frames,
`lcd.py` decodes LCD uploads, `mkimg.py` makes the solid-colour test images.
The raw recording is only in the session's temp folder, not in this project,
because it also holds keystrokes.
