# CalComp DrawingBoard III: specifications, protocol and commands

This is a reference for the CalComp (GTCO CalComp, later GTCO CalComp by
Turning Technologies) **DrawingBoard III** serial digitizer. It's written for
anyone who wants to use one with a modern computer. It summarises GTCO's two
documents, the *DrawingBoard III user guide* and *DrawingBoard III Commands*,
and adds what was measured on a real tablet while writing this project's
driver. Where the two disagree, the measured behaviour is given and the
difference noted.

Tested hardware: **model 34180** (18" × 12" active area), firmware
`70171A 07/14/94 16906-4`, product string `CalComp 3400`, with a cordless pen
and a cordless 16-button cursor (puck).

The original PDFs are not included here, because they're GTCO's copyrighted
documents. They were distributed on GTCO CalComp's legacy support pages, titled
"DrawingBoard III Printable Files" (the user guide, 48 pages) and
"DrawingBoard III Commands" (18 pages). Copies turn up by searching for those
titles.

Contents:
[Models](#models-and-specifications) ·
[Hardware](#the-tablet-and-its-tools) ·
[Connection](#connecting-it) ·
[Setups](#setups-presets) ·
[Configuration keys](#configuration-keys-menu-strip) ·
[Modes](#operating-modes) ·
[Format 23](#format-23-the-default-binary-format) ·
[Commands](#host-commands) ·
[Init sequence](#a-working-init-sequence) ·
[Quirks](#behaviour-and-quirks-measured)

## Models and specifications

| Model | Active area |
|---|---|
| 34120 | 12" × 12" |
| **34180** | **18" × 12"** (the guide's parts list says "12 x 18") |
| 34240 | 24" × 18" |
| 34360 | 36" × 24" |
| 34480 | 48" × 36" |
| 34600 | 60" × 44" |

The model number is on the label under the tablet. The tablet also reports its
size: `ESC%VS` returns the maximum coordinates at the current resolution. The
34180 at 1000 lpi reports 18000 × 12000.

| Feature | Value |
|---|---|
| Resolution | up to 12,700 lpi (500 lines/mm), set by command or configuration key. Default 1000 lpi |
| Accuracy | ±0.010" (standard models); ±0.005" (-H1) and ±0.002" (-H2) high-accuracy versions of the large models |
| Repeatability | 0.001" |
| Jitter | ±1 count |
| Proximity (sensing height) | more than 0.5" (12 mm) |
| Maximum data rate | over 200 points/s (corded tools), over 170 points/s (cordless). Default 125 points/s |
| Interface | asynchronous serial RS-232C (CCITT V.24). Default 9600 baud, 8 data bits, no parity, 1 stop bit |
| Power | wall power supply, 12–17 V DC, 400 mA, 2.1 mm barrel plug, **positive on the outside** (centre negative) |
| Operating temperature | 10–40 °C (50–104 °F) |
| Storage temperature | −40 to 70 °C |
| Humidity | 0–95 %, non-condensing |

## The tablet and its tools

### Menu strip and lights

Along one edge is a printed **menu strip** of 28 keys. You tap them with the
pen or puck:

`Config/Exit` · Bank `A` `B` `C` · keys `1`–`18` · Save `Default` `2` `3` ·
Restore `Default` `2` `3`

- In normal use, keys 1–18 are **macro keys**. Macros were recorded with GTCO's
  Windows TabletWorks software, which isn't needed or used here.
- `Config/Exit` enters **configuration mode** (configuration light on, one
  beep). Exiting gives a double beep. While in configuration mode, keys 1–18
  toggle settings in the selected bank. Hover over a key and the power light
  shows its state (on = set). The tablet sends no coordinates in this mode; the
  guide's troubleshooting chart lists a "frozen screen pointer" for it.
- Changes apply immediately but are **lost at power-off unless a Save key is
  pressed**. Save overwrites a preset, and the Default preset is what loads at
  power-on. If you never press Save, a power cycle always brings back the
  factory Default.
- **Restore** loads a preset: `Config/Exit`, then the `Restore` key, then
  `Config/Exit`.

Lights: the **power light** is steady when a tool is in range over the active
area, and blinks when the tool is out of range or asleep. The **configuration
light** is on in configuration mode.

### Tools

- **Pens:** a tip switch plus two barrel buttons (lower and upper). Versions:
  click tip (light blue ring), pressure tip (black ring), and lite-touch tip
  (red ring). The cordless pen uses two **393 silver-oxide cells**.
- **Cursors (pucks):** 4- or 16-button, with a crosshair lens. The 16-button
  cursor's keys are labelled 0–9 and A–F; key 0 is the "pick" button. The
  cordless puck takes two AAA cells. The corded high-accuracy cursor has an
  illuminated lens.
- **Sleep:** cordless tools sleep after 1–5 minutes without a button press.
  Press any button to wake one.
- **Careful:** holding cordless puck buttons 1 + 2 for about 3 s (or pen tip +
  both barrel buttons) changes the tool's radio frequency. Repeat it to change
  back.
- **The point that's sensed:** for the pen it's a coil a little above the tip,
  so a tilted pen reads slightly off. For the puck it's the crosshair centre.

## Connecting it

The tablet's serial cable carries the RS-232 lines plus a power-jack junction
for the wall supply. A modern PC needs a **USB-to-RS-232 adapter**. This
project was developed with a Prolific PL2303 adapter (USB ID `067b:2303`),
which appears as `/dev/ttyUSB0`.

- Linux's ModemManager probes new serial ports with AT commands, which can
  confuse the tablet. Tell it to ignore the adapter
  (`ENV{ID_MM_DEVICE_IGNORE}="1"`; `packaging/99-calcomp-drawingboard.rules`
  does this).
- USB-serial adapters deliver data in bursts: gaps of 16–40 ms between reads
  are normal, with occasional 60–120 ms dropouts, even though the tablet sends
  every 8 ms.

## Setups (presets)

Three setups are stored in the tablet: Default (loaded at power-on), 2 and 3.

| | **Default** | Save 2 | Save 3 |
|---|---|---|---|
| Emulation | GTCO DP5 High Resolution Binary | Summagraphics MM1201 binary | CalComp 2000 ASCII |
| Output format | **23** | 30 | 0 |
| Mode | **Run** (stream) | Track | Point |
| Serial | **9600 8N1** | 9600 8O1 | 9600 7E1 |
| Data rate | 125 pps | 150 pps | 125 pps |
| Resolution | **1000 lpi** | 500 lpi | 200 lpi |

The host command `ESC%VRn` recalls "room defaults" (factory settings), which
the command reference lists like this:

| | Recall 1 | Recall 2 | Recall 3 | Recall 4, 8 | Recall 5, 7 | Recall 6 |
|---|---|---|---|---|---|---|
| Format | 23 | 30 | 0 | 7 | 23 | 20 |
| Mode | run | track | point | run | run, increment | run, increment |
| Serial | 9600 8N1 | 9600 8O1 | 9600 7E1 | 9600 8N1 | 9600 8N1 | 9600 8N1 |
| Resolution | 1000 lpi | 500 lpi | 200 lpi | 1000 lpi | 1000 lpi | 1000 lpi |
| Data rate | 125 | 100 | 125 | 125 | 40 | 40 |

`ESC%VR1` therefore restores the Default setup this project uses.

## Configuration keys (menu strip)

In configuration mode, keys 1–18 set options in the selected bank. These are
the options the guide documents (guide pp. 15–21):

- **Bank A:** 1–2 operating mode (Line/Point/Track/Run); 3–4 increment (none,
  1, 5, 10); 5 prompt mode; 6–8 data rate; 9–11 resolution (200 to 2540 lpi);
  12–16 output format (32 formats, e.g. 0 CalComp 2000 ASCII, 21 Wacom binary,
  23 CalComp high-res binary, 24 GTCO DP5 high-res, 28 CalComp 2000 binary,
  30 Summagraphics MM1201 binary); 17 add a line feed; 18 7 or 8 data bits.
- **Bank B:** 1–3 baud rate (1200–19200); 4–6 parity; 7 corded frequency;
  **8 accept Summagraphics/CalComp 2000 commands** (if this is off, the
  `ESC%` host commands below are ignored); 9 require ESC for 9x00 commands;
  10 send data out of proximity; 11 pressure data; 12 pen height data; 13 tilt
  data; 14 tilt correction; 15–16 mouse mode (Mouse Systems or Microsoft
  emulation); 17 proximity high/low; 18 CTS enable.
- **Bank C:** 1 + 6 rotation (0/90/180/270°); 2 CR on ASCII; 3 pen sound;
  4 speaker; 5 tilt → pressure; 8 high-accuracy cursor.

Pressure, height and tilt options add bytes to the packet. Leave them off for
this driver.

## Operating modes

- **Run:** streams coordinates continuously while a tool is in range.
- **Track:** sends only while a button is down.
- **Line:** like Track, plus one point when the button is released.
- **Point:** one point per button press.
- **Prompt:** an extra rule on top of any mode: the host must send the prompt
  character `?` for each point.
- **Increment N:** a movement filter. Data is sent only after the tool moves N
  counts, or on a button change. **Grid update** is the same, but only the axis
  that moved is updated.
- **Send data out of proximity / margin data:** also report when the tool is
  out of range or in the margins (off by default).
- **Mouse mode:** emulates a 1200-baud Microsoft or Mouse Systems serial mouse
  with relative data.

## Format 23 (the Default binary format)

Each report is 6 bytes. **Only the first byte has bit 7 set**, which is how
the host finds the start of a packet; after a lost byte, resynchronise on
bit 7.

```
       bit 7   6    5    4    3    2    1    0
B1       1    C4   C3   C2   C1   C0  X15  X14
B2       0   X13  X12  X11  X10   X9   X8   X7
B3       0    X6   X5   X4   X3   X2   X1   X0
B4       0     0   PR  X17  X16  Y16  Y15  Y14
B5       0   Y13  Y12  Y11  Y10   Y9   Y8   Y7
B6       0    Y6   Y5   Y4   Y3   Y2   Y1   Y0
```

- `X = X17..X0`, `Y = Y16..Y0`, in counts at the current resolution.
- **The origin is the bottom-left corner**, with Y increasing upwards. Input
  devices on a PC use top-left, so the driver reports `max_y − Y`.
- **PR** = 1 means out of proximity. It's only ever set when "send data out of
  proximity" is on, which it isn't in the Default setup. Normally the tablet
  simply **stops sending** when the tool leaves.
- **Cursor code C4..C0** (format 23 coding):

| Tool | C4..C0 |
|---|---|
| nothing pressed (pen or puck) | `00000` |
| pen tip / lower barrel / upper barrel | `00001` / `00010` / `00100` (bits combine: tip + lower = `00011`, …) |
| 16-button puck, button n (0–15 = 0–F) | `1` followed by n in 4 bits: button 0 = `10000`, button F = `11111` |

So a 16-button puck reports a button **number**, not a bitmask. Holding two
buttons reports only one. The packet doesn't identify the tool until a button
is pressed: a hovering pen and a hovering puck look the same.

With pressure data on, B1's C4..C0 carry pressure (0–31) instead. With tilt
data on, two bytes of X/Y tilt follow. Other binary formats (20 ATF, 21 Wacom,
22, 26, 28, 30 Summagraphics, 31) and the ASCII formats are documented in
*DrawingBoard III Commands*. They aren't used here.

**Corrections to other sources:** a widely copied protocol write-up has the
format 23 packet layout wrong. The old X.Org `xf86-input-calcomp` driver gets
the layout right, but reads Y without masking B4 and has a buggy button-change
routine. Use the table above, which was checked against about 9800 captured
packets (`driver/test`).

## Host commands

Commands are sent over the serial line. The tablet doesn't acknowledge them;
it just changes behaviour. Allow some time between commands (the driver waits
250 ms).

### Single-byte commands

Always obeyed, at any time (the menu setting can't disable them):

| Byte | Effect |
|---|---|
| DC1 (0x11, XON) | resume transmission after XOFF |
| DC3 (0x13, XOFF) | stop transmission |
| BEL (0x07) | beep (if a beeper is fitted) |

The MM/2000 one-letter commands below can be turned off by a configuration
setting (the guide's "one byte command enable" menu bit), but they're on in
the Default setup. On the test tablet, a raw `p` switched to 4 lpi:

| Byte | Effect |
|---|---|
| `a` / `b` / `c` | send size / origin upper-left / origin lower-left |
| `d`–`q` (lower case) | resolution: `d` 100 lpi, `e` 200, `g`/`h` 400, `j` 1000, `k` 1270 (2x00 mode only), `l` 1 lpi, `n` 2, `p` 4; `f` 10 lines/mm, `i` 20, `q` 40, `o` 50 |
| `@`–`G` / `H`–`O` | 2000 mode: track / run mode at 1, 5, 10, 20, 40, 75, 100, 125 pps |
| `P` `Q` `R` `S` `T` | 2000 mode: point, point-prompt, run-prompt, halt, track-prompt |
| `?` | the default prompt character (prompt mode) |

**Beware:** a stray letter, such as from ModemManager or line noise when the
adapter is plugged in, can change the resolution or stop output. This is why
the driver starts with XON and a full reset.

### `ESC %` commands (9100/2500 command set)

Format: `ESC % <command> CR` (0x1B 0x25 … 0x0D).

| Command | Effect |
|---|---|
| `ESC%VR` | reset |
| `ESC%VRn` | recall room default *n* (1 = Default: format 23, run, 9600 8N1, 1000 lpi, 125 pps) |
| `ESC%R` | run mode (stream) |
| `ESC%IR` | **incremental** run mode (only sends after movement) |
| `ESC%H` | halt |
| `ESC%Q` | clear prompt mode; `ESC%QH` = prompt with character H |
| `ESC%A0` / `ESC%A1` | data output off / on |
| `ESC%C1N81` | serial format (here 8N1) |
| `ESC%^n` | output format *n* (e.g. `ESC%^23`) |
| `ESC%JRn,0` | resolution *n* lines per inch (e.g. `ESC%JR1000,0`) |
| `ESC%Wn` | data rate |
| `ESC%Xn` / `ESC%Yn` | increment in X / Y (counts) |
| `ESC%Zn` | out-of-proximity setting |
| `ESC%L0` / `ESC%L1` | line feed off / on (the old X.Org driver calls this "origin"; it isn't) |
| `ESC%VA0` / `ESC%VA1` | report buttons / report pressure |
| `ESC%VS` | **query size**: 6-byte reply in the same packing as a format 23 packet, giving max X and max Y (34180 at 1000 lpi: `81 0c 50 20 5d 60` = 18000, 12000) |
| `ESC%__V` | **query firmware**: e.g. `70171A 07/14/94 16906-4` |
| `ESC%__p` | **query product**: `CALCOMP 3400` or `CALCOMP 3300` |

The MM (Summagraphics) and 2000 command sets, and the format-switch commands
(`mA`, `mB`, `2A`–`2G`, `2M`, `2m`), are in *DrawingBoard III Commands*.
Format and communication commands change the current settings but don't save
them.

## A working init sequence

The driver sends this when it attaches. It's taken from GTCO's reference and
the old X.Org driver, and corrected on the real tablet:

1. `DC1`, then `ESC%VR1`, then wait 1 s: resume output and recall the Default
   setup. This is the only reliable way to recover a tablet left stopped or
   misconfigured, e.g. after the USB adapter was replugged.
2. `ESC%A1`, `ESC%Q`, `ESC%C1N81`, `ESC%^23`, `ESC%JR1000,0`, `ESC%VA0`,
   `ESC%X1`, `ESC%Y1`, `ESC%IR`: output on, no prompt, 8N1, format 23,
   1000 lpi, buttons, and incremental run so the tablet is quiet while a tool
   rests during the queries.
3. Queries: `ESC%__V`, `ESC%__p`, `ESC%VS`. Packets from a moving tool can
   arrive mixed in with the replies. Skip any sync byte and the 5 bytes after
   it in text replies, and require two identical `VS` replies.
4. `ESC%X0`, `ESC%Y0`, `ESC%R`: increment off and plain run mode, so the
   tablet streams continuously while a tool is in range.

**Order matters:** `ESC%IR` turns increment mode back on. If it's sent after
`X0`/`Y0`, a resting tool goes silent, and the host sees the tool leave.

## Behaviour and quirks (measured)

- **Packet rate:** one packet every 8 ms (125 pps) while a tool is in range,
  including when it's resting (in run mode with increment off).
- **No "tool left" message:** the tablet just stops. A host needs a timeout.
  150 ms works well with USB-serial adapters, given the bursty delivery
  described above.
- **Identical packets while still:** a resting tool sends the same position
  again and again. On Linux, the input layer drops repeated values, so a driver
  that wants to show the tool is still there must send something that changes
  (this driver uses `MSC_SERIAL` in tablet mode).
- **Cordless pen dropouts:** a worn cordless pen can go silent for 80–320 ms
  during fast strokes. The puck on the same tablet didn't, so suspect the pen
  (battery contacts) before the tablet.
- **Faulty grid strips:** old tablets can have a damaged wire in the sensing
  grid. Along that strip the position sticks at one value and then jumps,
  across the full width. The test tablet has two (top-origin y ≈ 600–950 and
  ≈ 6800–8400 counts). `tools/scan_bands.py` finds them.
- **Power cycle:** the tablet returns to its Default setup and streams on its
  own, so it keeps working if it's switched on after the computer attached it.

Development details, with the captures behind each finding, are in
[`../NOTES.md`](../NOTES.md).
