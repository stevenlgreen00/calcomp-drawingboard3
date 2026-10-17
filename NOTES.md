# CalComp driver: development log

Started 2026-10-15. This is the working log kept while the driver, tools and
menu daemon were written, kept as a record of what was found and why. Earlier
entries can be overtaken by later ones; the READMEs and `docs/` describe the
current state.

**In the published repository**, some files named here aren't included:
`PROMPT.md`, `PROTOCOL_SPEC.md` (wrong packet layout), the old X.Org driver
source, GTCO's PDFs (summarised in `docs/TABLET.md`) and most raw captures (see
`captures/README.md` for the ones that are).

## Scope (decided 2026-10-16)
The driver targets **only the tablet's Default setup**: the original binary
format 23, 9600 8N1, Run mode, 1000 lpi. This is the same setup the old X.Org
driver used. The menu strip is for configuring the tablet itself, not for
application keys, so the driver ignores it. It doesn't map macro keys and
doesn't support ASCII or other formats. The one requirement it does create:
the packet parser must resync cleanly on bit 7 and drop stray bytes, in case a
menu tap or config change sends something unexpected. The `menu` and `config`
capture groups remain as optional tools, not part of the default run.

## Files
- `PROMPT.md` — the original task prompt.
- `PROTOCOL_SPEC.md` — protocol write-up the user found. **Its binary packet
  layout is wrong.** Use the old driver below as the source of truth.
- `xf86-input-calcomp-1.1.2.tar.gz` + `reference-xf86-input-calcomp/` — the
  old X.Org driver (Martin Kroeker, 2000–2001, last release 2008), MIT/X11
  license, so it can be ported if the copyright notice is kept. The useful
  code is all in `src/xf86Calcomp.c` and `src/xf86Calcomp.h`.

## Manual: `docs/DrawingBoard-III-Printable-Files.pdf` (GTCO CalComp user guide)
Pages cited below are the manual's own page numbers.

### Menu strip (p.12–13): 28 keys, not 16
`Config/Exit` | Bank `A` `B` `C` | Configuration/Macro keys `1`–`18` |
Save `Default` `2` `3` | Restore `Default` `2` `3`
- **Normal mode:** keys 1–18 are *macro keys*. Macros are recorded only with
  the TabletWorks Windows software (p.12), so the tablet probably reports
  *which key* was hit and the host software runs the macro. What it actually
  sends is unknown, so capture it.
- **Config mode:** `Config/Exit` turns it on (config light on, one beep) and off
  again (double beep) (p.4). While in config mode, keys 1–18 toggle settings in
  the selected bank. Hover over a key and the power light shows its state
  (on = set). The tablet probably sends no coordinates during config: the
  troubleshooting chart lists "frozen screen pointer — menu strip is in
  Configuration Mode" (p.32).
- Changes take effect right away. They're lost at power-off **unless a Save key
  is pressed**, which overwrites a preset (p.15). So power-cycling is a full
  reset, as long as Save isn't touched. **Never press Save.**
- **Restore** keys load a preset (in config mode: Config/Exit → Restore →
  Config/Exit).

### Presets (p.13–14)
| | Default | Save 2 | Save 3 |
|---|---|---|---|
| Mode | Run (stream) | Track | **Point** |
| Serial | 9600 8N1 | 9600 8**O**1 | 9600 **7E1** |
| Rate | 125 pps | 150 pps | 125 pps |
| Resolution | 1000 lpi | 500 lpi | 200 lpi |
| Format | 23 | 30 | **0** |
| Emulation | GTCO DP5 Hi-Res Binary | Summagraphics MM1201 binary | **CalComp 2000 ASCII** |

`Restore 3` is almost certainly the ASCII mode you're expecting. Note: it's
**7E1** (bit 7 = parity at our 8N1, which capture.py's `ascii-7bit` decode
handles) and **Point mode** (sends one point per tip/button *press*, nothing
while hovering). The Default preset matches the old driver's settings exactly
(9600 8N1, 1000 lpi, format 23 = `ESC%^23`).

### Configuration keys (p.15–21)
- **Bank A:** 1–2 operating mode (Line/Point/Track/Run); 3–4 increment
  (none/1/5/10); 5 prompt mode; 6–8 data rate; 9–11 resolution
  (200…2540 lpi); 12–16 output format (32 formats, table p.18: 0 CalComp 2000
  ASCII, 23 CalComp Hi Res, 24 GTCO DP5 Hi Res, 28 CalComp 2000 Bin, 21 Wacom
  Binary, 30 Summa MM1201 B...); 17 add line feed; 18 7/8 data bits.
- **Bank B:** 1–3 baud (1200–19200); 4–6 parity; 7 corded frequency;
  **8 accept Summagraphics/CalComp 2000 commands** (if this is set to "do not
  use", the old driver's `ESC%` init will be ignored); 9 require ESC for 9X00
  commands; 10 send data out of proximity; 11 pressure data; 12 pen height
  data; 13 tilt data; 14 tilt correction; 15–16 **mouse mode** (Mouse Systems
  / Microsoft emulation); 17 proximity high/low; 18 CTS enable.
- **Bank C:** 1+6 rotation (0/90/180/270°); 2 CR on ASCII; 3 pen sound;
  4 speaker; 5 tilt→pressure; 8 high-accuracy cursor.
- Pressure, height, and tilt options probably add bytes to the packet. Keep them
  off at first.

### Tools (p.5, 10–11, 29–30)
- Pen: tip = button 0, lower side = button 1, upper side = button 2. Click-tip,
  pressure-tip, and lite-touch versions exist.
- 16-button cursor: buttons labeled 0–9, A–F; 0 = pick. Tools sleep after
  1–5 min with no button press; press any button to wake.
- **Cordless puck: holding buttons 1+2 for ~3 s changes its RF frequency.**
  Cordless pen: tip + both side buttons for ~3 s does the same. Repeat to revert.
  Avoid both in captures.
- Resolution up to 12,700 lpi (p.34), which fits the 16/18-bit coordinate
  fields in the old driver's packet layout.

## First contact with the real tablet (2026-10-16)
- Adapter: Prolific PL2303 (067b:2303) → `/dev/ttyUSB0`, stable path
  `/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0`.
- The init sequence was accepted silently (no replies to the setup commands).
- `ESC%__V` → `70171A 07/14/94 16906-4`; `ESC%__p` → `CalComp 3400`.
- `ESC%VS` → `81 0c 50 20 5d 60` → max_x = (1&3)<<14 | 0x0c<<7 | 0x50 =
  **18000**, max_y = 0x5d<<7 | 0x60 = **12000**. At 1000 lpi that's 18"×12",
  i.e. **model 34180**. This confirms the old driver's 7-bit coordinate packing.
- In that reply B4 = `0x20` (bit 5 set) with no tool nearby, which fits the old
  driver's `PROXIMITY_BIT 0x20` (active-low: set = out of range). Confirm with
  the pen steps.
- With nothing near the tablet: 0 bytes in 3 s. Even in Run mode, it sends
  nothing while out of proximity.
- **First capture attempt: no data while the pen hovered**, even though queries
  are answered. `?` (prompt) gets no reply. Still diagnosing: either the pen
  isn't sensed (asleep, battery, corded tool not in POINTER, or check whether the
  power light is steady or blinking), or the tablet only sends when something is
  pressed (Track/Point mode). Tool: `tools/monitor.py` (a live decode of every
  byte; keys i=init, p=prompt, v=ESC%VS).

## Puck capture results (2026-10-16, captures/20261016-200836 + -201619)
Use buttons 0–7 from `200836` and 8–F onwards from `201619`. In `200836`,
`puck_btn_8` was a mis-press (it shows code 27 = B). The user redid it.
The pen was not working yet (its battery contacts are corroded). The puck works on new AAA cells.
- **Decoding is confirmed.** X/Y follow the old driver's packing. The puck moved
  over x 2589–13881, y 2082–8825, inside the 18000×12000 max. No junk bytes,
  no framing errors.
- **Buttons:** B1 bits 6..2 are `1 n3 n2 n1 n0` while a button is held, and
  `0 0000` when none is. So bit 6 = "a button is down" and bits 5..2 = **button
  number 0–15** (labels 0–9, A–F match 16–31 exactly). Each press is one packet
  with the code and the release is a packet with 0, often repeated 2–3 times.
  It's a *code*, not a bitmask: two buttons together (4+5) reported only `5`,
  so the driver can only report one puck button at a time. The old driver's
  `v-15` mapping matches this. The pen capture settles bit 6: it's "puck button down". The pen never sets it (see Pen capture results).
- **Proximity: the tablet never sets a prox bit.** B4 was `0000xxxx` in every
  packet, including `puck_leave`. When the tool leaves, the tablet just
  **stops sending**. The old driver's `PROXIMITY_BIT 0x20` never fires here.
  The `0x20` in the `ESC%VS` reply is something else.
- **No packets while the puck sits still**, even when held over the tablet
  (`puck_hover`: 0 packets; button steps 3–9 packets). The cause is the old
  init's increment mode (`ESC%X1`/`ESC%Y1`): it reports only on movement or
  button change. Combined with the point above, **a stationary tool looks the
  same as an absent one**.
  - **Solved (increment test, captures/20261016-increment-test.json):** with
    the puck resting still, increment 1 gave 0 packets in 5 s. After
    `ESC%X0\r ESC%Y0\r` it gave **632 packets in 5 s = 126 pps**, steady, with
    the same x/y every time. Back to `X1/Y1` gave 0 again.
  - **Driver init uses `ESC%X0` / `ESC%Y0` instead of the old `X1`/`Y1`.** The
    tablet then streams at ~125 pps (one packet every 8 ms) whenever a tool is
    in range. **Proximity-out = no packet for ~50 ms** (6 packet periods), using a
    timer in the driver that is re-armed on every packet. Proximity-in = first packet.
- The puck sleeps after a few minutes; the first `puck_hover` had 0 packets
  partly for that reason.

## Pen capture results (2026-10-16, captures/20261016-204600)
The pen works after its contacts were cleaned. Caveat: `monitor.py` was still
running and reading the same port during this run, so some chunks went to it
instead. All packets captured are intact (0 junk), but **packet counts and rates
from this file are not reliable**.
- **Pen buttons form a bitmask** in B1 bits 4..2: tip = `0x04`, lower side =
  `0x08`, upper side = `0x10`. Combinations OR together (tip+lower = 3,
  tip+upper = 5). **B1 bit 6 is never set by the pen.**
- **Tool ID, resolved:** when a button is down, B1 bit 6 = 1 → puck (bits 5..2 =
  button number), bit 6 = 0 → pen (bits 4..2 = bitmask). With no button down
  both send B1 = `0x80`, so a hovering tool can't be identified until it presses
  something. The driver keeps the last identified tool, defaulting to pen.
- **Coordinate range is 0–17999 × 0–11999** (corners: BL x0 y0, BR x17999, TR
  y11998, TL y11999). **Origin is bottom-left, Y increases upward**, even
  though the init sends `ESC%L1` "origin upper-left". So the driver flips Y
  (`y = 11999 - y`), as the old driver did. In the TR/BR packets B1 bit 0 is set
  (x ≥ 16384), which confirms X15..14 sit in B1 bits 1..0.
- No prox bit from the pen either (B4 high bits always 0). When the pen leaves,
  the stream stops, same as the puck.
- While the pen hovers still, Y jitters by about ±15 counts and X is stable.
  That's hand or height noise; the spec is ±1 lsb on a fixed tool.

## Driver (2026-10-16)
Written in `driver/`: a serio driver (`calcomp_drv.c`) bound by
`tools/calcomp_attach.py`, which does the same job as inputattach. It uses
serio protocol ID `0xc0`, not a mainline ID (mainline ends at 0x43). The
decoder passes `make test` against all 9812 captured packets. **Built 2026-10-17** against 7.2.6+deb13-amd64 with no
warnings, including at `W=1`. The modalias is `serio:ty02prC0id*ex*`, and
0xc0 is still unused in that kernel's serio.h. See `driver/README.md`.
- **First load (2026-10-17):** the module bound and the input device registered
  (`input32`), and the size query gave 18000x12000. **Bug:** a tool moving in
  range sent packets during the ID queries. They filled the 48-byte reply
  buffer, so "firmware" was garbage, the firmware text was logged as
  "product", and the product ID was lost. **Fixed:** text replies now skip any
  sync byte plus the 5 bytes after it, and binary replies must start on a sync
  byte. The size query now retries up to 4 times and needs two consecutive
  replies to agree. Rebuilt, W=1 clean. **Re-test passed:** `firmware:
  70171A 07/14/94 16906-4` and `product: CalComp 3400` were logged correctly.
- **evtest (captures/evtest.log, 2026-10-17):** the capabilities are right (16
  puck keys, pen keys, X 0–17999 and Y 0–11999 at 39 units/mm). Pen tip, lower
  and upper buttons give BTN_TOUCH, BTN_STYLUS and BTN_STYLUS2. Puck 0/1/2 give
  LEFT/RIGHT/MIDDLE. Pen→puck switches cleanly (`BTN_TOOL_PEN=0` then
  `BTN_TOOL_MOUSE=1 BTN_LEFT=1` in the same instant). Y is flipped as intended.
- **Problem: proximity flicker.** There were 125 tool-out events in about
  60 s, and 119 of them came back within 300 ms (most within 1–30 ms).
  So the packet stream really does pause for more than 50 ms while a tool is
  in use, both pen and puck, during hover and with the tip pressed. The
  driver's 50 ms timeout is too short. (evtest's frame gaps can't measure this,
  because the input core drops frames where nothing changed.)
- **Gap measurement (captures/gaps-20261017-024933.json, 24.6 s of pen and
  puck use, driver detached):** 2319 packets arrived in 1027 reads (about 2.3
  per read), so the PL2303 delivers in bursts. Gaps between deliveries: p50
  16 ms, p90 32 ms, and many at 40–48 ms. A few 60–120 ms dropouts. The gaps
  over 300 ms were deliberate lifts. **Fix: `prox_timeout_ms` default 50 →
  150.** Rebuilt, W=1 clean.
- **Re-test with 150 ms (captures/evtest2.log, 42.7 s):** 11 tool-out events,
  versus 125 per minute at 50 ms. Two were clean pen↔puck switches (out and in
  in the same frame). Most returns came 0.3–16 s later (real lifts). Only 4
  returned within 23–128 ms of the out event, i.e. 0.17–0.28 s of total
  silence, which fits quick lifts or hovering at the top of the range. All
  buttons were seen: puck LEFT/RIGHT/MIDDLE/SIDE/BTN_1/BTN_5, pen
  TOUCH/STYLUS/STYLUS2. **The driver works.** If strokes ever break up in an
  application, raise `prox_timeout_ms` to 200–300 live.

## Permanent install (2026-10-17)
`packaging/install.sh` installs: DKMS (`driver/dkms.conf`, source in
`/usr/src/calcomp_drv-0.1`), `/usr/local/sbin/calcomp-attach`, and
`calcomp-attach@.service`, started by the udev rule
`99-calcomp-drawingboard.rules` (067b:2303, which also sets
ID_MM_DEVICE_IGNORE), plus `serport` in modules-load.d.
`uninstall.sh` reverses it. Verified without root: `bash -n`,
`udevadm verify`, `systemd-analyze verify` (its only complaint was that the
helper isn't installed yet), and a build from just the DKMS source set.
**Not yet run with sudo.** The user needs `apt install dkms` first.
- From the manual's Restore Default table (Bank A keys 3–4 off = increment
  None, keys 12–16 = 10111 = format 23), the tablet's power-on Default
  already streams format 23 continuously. So it still works if it was
  switched on after the setup commands were sent.

## Desktop / libinput (2026-10-17)
- udev tags the device `ID_INPUT_TABLET=1`, 461×307 mm. KDE Plasma 6.3
  (Wayland), libinput 1.28. The cursor follows the pen and tip taps click. But
  the Drawing Tablet calibration screen ignored the pen, and the user journal had
  hundreds of `qt.qpa.wayland: Can't send tablet event with no proximity
  surface`.
- **Cause (captures/libinput.log):** libinput forced `proximity-out` exactly
  50 ms after the last event, 20 times in 3.6 s with the pen held still. A still
  pen sends identical packets, the input core filters the repeats, and
  libinput's forced proximity-out timer for tablets without a working prox-out
  kicks in.
- **Fix:** send `MSC_SERIAL` (pen 1, puck 2) in every frame. MSC events aren't
  filtered, so libinput sees a frame every 8 ms. Rebuilt, W=1 clean.
- **Install script bug:** the first re-test (captures/libinput2.log) still
  flickered because the fix was never loaded. `install.sh` checked
  `lsmod | grep -q` under `set -o pipefail`: grep exits on the first match,
  lsmod gets SIGPIPE, and the pipeline counts as failed. So `rmmod` was skipped,
  and the hand-loaded 02:53 module stayed in memory (device `EV=b`, no MSC).
  Fixed: it now checks `/sys/module/calcomp_drv`, and `dkms status` goes through
  `$(...)`. Check after install: `/sys/class/input/inputN/capabilities/msc`
  should be non-zero.
- **Verified (captures/libinput3.log, 2026-10-17 03:36):** the DKMS module was
  loaded at 03:34:59 and the device has `EV=1b MSC=1`. In 34 s of use libinput
  reported only 3 proximity in/out pairs (lifts that returned 0.3–0.5 s later)
  instead of 20 in 3.6 s. The pen shows as serial `0x1`, the four taps are
  clean tip down/up pairs, and there were 0 Qt "no proximity surface" warnings
  since the fix loaded.
- **KDE calibration** is for display tablets (pen on the screen), so it hides
  the cursor on purpose. It doesn't apply to an opaque tablet like this one.
  Use the screen/area mapping instead.

- **Puck didn't move the pointer (captures/libinput-puck.log):** libinput
  listed the puck as `pen (0x2)` and kept forcing proximity in/out (20 times in
  17 s), but its BTN_LEFT/RIGHT/MIDDLE got through. libinput disables
  BTN_TOOL_MOUSE/BTN_TOOL_LENS on non-Wacom tablets, so the puck's
  proximity was invisible to it. **Fix:** the puck now uses `BTN_TOOL_PEN`
  (serial 2 keeps it a separate tool), with a `puck_as_mouse` parameter to bring
  back BTN_TOOL_MOUSE. Rebuilt, W=1 clean. Reinstalled 03:47 (`KEY` bits no
  longer include BTN_TOOL_MOUSE). The re-test log (libinput-puck.log, 03:51)
  showed 3 clean proximity in/out pairs for `pen 0x2` in 5 s, with motion and
  BTN_LEFT. Whether the puck now moves the desktop pointer still needs the
  user to confirm.

- **A resting puck dropped out of proximity (libinput-puck.log, 03:51):** in
  the user's capture there were 3 s gaps with no events while the puck sat
  still, and buttons 1 and 2 never showed up. Their packets arrived as the first
  packet of a new proximity-in, and libinput ignores buttons already down at
  proximity-in. dmesg2.log showed setup running cleanly every time, so the
  commands were sent, just in the wrong **order**: the driver (and gaps.py)
  sent `X0`, `Y0`, then `ESC%IR`, while the working increment test sent `IR`
  first. **`ESC%IR` turns increment mode back on.** That also explains the
  0.3–3.4 s "lifts" in the gaps measurement. Fix: `IR` moved into the setup
  list and `X0`/`Y0` are now the last commands. Rebuilt, W=1 clean.
  **Verified (libinput-puck2.log, 03:58):** the resting puck stayed in
  proximity for 24 s straight (13 s of it still), and buttons 0/1/2/3 gave
  LEFT/MIDDLE/SIDE/RIGHT as clean press/release pairs. Tool ID switched from
  pen 0x1 to puck 0x2 on the wake-up button press.

- **Puck button map changed (user request):** buttons 0 and 3 are the large
  buttons on opposite sides of the top row, so the default is now 0 = LEFT,
  1 = MIDDLE, 2 = SIDE, 3 = RIGHT (previously 1 = RIGHT, 2 = MIDDLE,
  3 = SIDE). 4–7 = EXTRA/FORWARD/BACK/TASK, 8–F = BTN_0–7.
- **Button 3 didn't right-click on the KDE desktop**, not even as BTN_STYLUS2
  on its own. It **works once KDE's Drawing Tablet "pen button 2" is set to
  right-click**: KWin rebinds BTN_STYLUS/BTN_STYLUS2 into real clicks, and a
  pen's BTN_RIGHT/BTN_MIDDLE go nowhere. Default map is now **0 = BTN_STYLUS,
  1 = LEFT, 2 = SIDE, 3 = BTN_STYLUS2**. The user corrected the layout: on their
  puck the large left-click button is **1**, opposite 3. (STYLUS/STYLUS2 were
  tested live via the puck_keys parameter first.) Rebuilt, W=1 clean.

## Replug problem and the official command reference (2026-10-17)
- **Symptom:** after unplugging and replugging the USB adapter, there was no
  data. The light was steady, the setup ran, and the ID queries were answered,
  so the link worked. Restarting the attach (re-sending setup) didn't help; a
  tablet power cycle did.
- **docs/DrawingBoard-III-Commands.pdf** (GTCO legacy page) is the real
  reference. Key facts:
  - `ESC%IR` = **incremental** run, `ESC%R` = run, `ESC%Q` = clear prompt,
    `ESC%QH` = prompt with char H, `ESC%H` = halt, `ESC%A0/1` = data output
    off/on, `ESC%VR` = reset, `ESC%VRn` = recall bank n (1 = Default = format
    23, run, 9600 8N1, 1000 lpi, 125 pps), `ESC%VS` size, `ESC%Wn` data rate,
    `ESC%Xn/Yn` increment, `ESC%^n` format, `ESC%Zn` out-of-prox, `ESC%L0/1`
    **line feed** (not origin; the old driver mislabeled it), `ESC%JRn,0`
    resolution.
  - **Single-byte commands are always obeyed:** DC1/DC3 = XON/XOFF, plus
    2000/MM letters, e.g. lowercase `d`–`q` set the resolution (`p` = 4 lpi). In
    the monitor test a raw `p` set 4 lpi (next point x=33,y=23).
  - Format 23 B4 = `0 0 PR X17 X16 Y16 Y15 Y14`. The decoder is fixed (it used
    bits 3..0 for Y). PR = 1 means out of proximity, so that's the 0x20 in the
    ESC%VS reply.
- **Fix:** the driver now starts setup with DC1 + `ESC%VR1` (1 s wait), then
  `A1`, `Q`, `C1N81`, `^23`, `JR1000,0`, `VA0`, `X1`, `Y1`, `IR` (quiet
  during queries), the queries, then `X0`, `Y0`, `R`. Verified by hand first:
  in the stuck state `\x11\e%VR1\r` from monitor.py restored normal
  streaming. New `reset_on_attach` parameter. Rebuilt, W=1 clean, `make test`
  passes. **Verified 2026-10-17 04:40:** reinstalled; after unplugging and
  replugging the adapter, the pen and puck work. Setup now takes about 6 s, so
  install.sh polls up to 15 s for the device.
- **Tablet power cycle while attached: works** (2026-10-17). The tablet
  comes back in its Default setup, which streams by itself; no re-setup needed.

## Puck button 1 = BTN_TOUCH (2026-10-17)
Puck button 1 sent BTN_LEFT, which only clicked in GIMP. A stroke needs a
tip contact. It now sends `BTN_TOUCH`, the same as the pen tip, so it draws,
and it still left-clicks on the desktop. Default map: 0 = STYLUS, 1 = TOUCH,
2 = SIDE, 3 = STYLUS2, 4–7 = EXTRA/FORWARD/BACK/TASK, 8–F = BTN_0–7. Puck
button reporting now releases before it presses, so two buttons mapped to the
same code don't cancel out. **Verified in GIMP (04:50):** puck button 1 and the pen tip
both draw lines. User's KDE Drawing Tablet settings: pen button 1 = left
click, pen button 2 = right click (these apply to puck 0/3 and the pen's side
buttons).
- **Correction (04:55):** the user's big left button **is** labeled 0, as the
  manual says, so the earlier "1 is the big left button" was a misreading. With
  0 = BTN_STYLUS mapped to KDE left-click it drew dots in GIMP, and 1 = TOUCH
  drew. **The default map is now 0 = TOUCH, 1 = STYLUS, 2 = SIDE,
  3 = STYLUS2.** Suggested KDE settings: pen button 1 = middle click, button 2 =
  right click.

## Absolute-mouse mode is now the default (2026-10-17)
- **Checklist results with tablet mode + tap_click:** hover works for both
  tools. Puck 0 drags windows but doesn't click window controls or app buttons.
  Puck 1 (KDE pen button 1 = left) clicks menu items and double-clicks icons,
  but not window controls, and can't drag. Right-click (pen upper / puck 3) and
  middle-click (pen lower / puck 1, after KDE pen button 1 = middle) work.
  Conclusion: none of KDE's tablet input paths gives a left click that window
  decorations and app buttons accept.
- **Decision (user):** the default is now an **absolute-position mouse** (like a
  VM "USB tablet"): only ABS_X/Y plus BTN_LEFT/MIDDLE/RIGHT (and puck extras),
  no BTN_TOOL_*/BTN_TOUCH/BTN_STYLUS* (those would make udev/libinput call it
  a tablet again; such puck_keys are rejected in mouse mode). Pen tip/lower/
  upper = LEFT/MIDDLE/RIGHT; puck 0/1/3 = LEFT/MIDDLE/RIGHT. When the tool goes
  quiet for `prox_timeout_ms`, held buttons are released. `tablet_mode=1` keeps
  everything above (tools, MSC_SERIAL, tap_click, KDE pen-button settings).
  `puck_keys` now defaults per mode (0 = mode default). Built, W=1 clean,
  `make test` passes. Needs a reinstall and the checklist again.

## Strip scanner for other users: tools/scan_bands.py (2026-10-17)
- A guided scan, run with the driver attached: 3 vertical sweeps (finding
  horizontal strips) and 3 horizontal sweeps (vertical strips). It reads the
  evdev device by name, gets the size via EVIOCGABS, and resets `area_*` to the
  whole tablet during the scan, restoring it after (including on Ctrl-C).
- Detection: 50-count bins. A bin is bad when at least 2 of the sweeps spanning
  it never reported it (or the only sweep, if just one spans it). A neighbouring
  pile-up bin (>2× median density) is the stuck value. Bands within 200 counts
  merge. The area suggestion takes the largest clean interval per axis with a
  100-count margin, optionally centred-cropped to `--screen WxH`.
- **Validated:** a replay of captures/evtest-sweep.log finds y 500–1049 (sticks
  at about 525) and 6800–8399 (sticks at about 7225). Its suggestion for
  2992×1253 is x 2372–15627, y 1149–6700, which matches the hand-picked area.
  Synthetic tests: clean sweeps give nothing; a planted 3000–3400 gap is found
  with its stuck value; a gap skipped by 1 of 3 sweeps is ignored, and by 2 of 3
  it's reported. The live path (root) is untested; device lookup and the
  EVIOCGABS constant (0x80184541) were checked.

## Reboot (2026-10-17)
- **Verified:** after a full system reboot the digitizer came back up
  automatically. udev coldplug started calcomp-attach@ttyUSB0, and the DKMS
  module bound and set the tablet up, with no user action needed.

## Mouse-mode checklist (2026-10-17)
- **Passed:** 1 hover, 2 click/double-click, 4 window controls and app buttons,
  5 right-click menu, 6 middle-click new tab, 7 window and file drag, 8 GIMP
  drawing, 10 pen↔puck. udev: `ID_INPUT_MOUSE=1`, plus a harmless `js0` (joydev
  attaches because the extra puck buttons stop the device matching joydev's
  "3-button absolute mouse" exception).
- **#9:** lifting the puck with a button held released it (correct), but the
  button was pressed again on return. **Fixed:** if a button was down when the
  tool timed out, buttons stay ignored after it returns until all are released.
  Tool switches don't trigger this, so the identifying first puck press still
  counts. Built, W=1 clean. Not installed yet.
- **#2 ("snap to row") = a faulty strip in the tablet's sensing grid.** The
  arrow sticks at the same screen height in every app (Dolphin, System
  Settings), and a normal mouse is fine. Raw evtest data, with no buttons held
  (captures/evtest-dolphin.log): Y hovers at 7215–7229 for up to 0.9 s while X
  keeps changing, then jumps (7234→7343, 7240→7322). Every capture since
  2026-10-16 (pen and puck, tablet and mouse mode) has the band Y 7200–7239 at
  about 20× the average report density, over X 3900–14600. **Dedicated sweep
  (captures/evtest-sweep.log):** puck dragged top→bottom at left, middle and right
  (X ≈ 450 / 8500 / 17700). All three plateau at Y ≈ 7200–7218 for about 360 ms,
  then jump 570–1036 counts. Y 6800–7099 and 7300–7599 are almost never
  reported, and 7600–8200 is sparse. So there's a **dead/sticky band at Y ≈
  6800–7600 (top-origin), i.e. 6.8"–7.6" from the top / 4.4"–5.2" from the
  bottom, across the full width.** Hardware (a grid wire or its channel), so
  the driver can't recover the position. Monitor: one output, DP-2, 2992×1253
  logical (scale 1.15), so not a multi-monitor dead zone.
- **Workaround: active area.** New live params `area_x0/y0/x1/y1` (counts,
  origin top-left; x1/y1 = 0 → far edge) map a rectangle onto the full output
  range; outside it, positions clamp to the edge. Recommended for this tablet:
  x 1000–17000, y 0–6700 = 16"×6.7" = 2.39:1, which matches the screen exactly.
  Built, W=1 clean, `make test` passes. Mapping maths checked.
  **Installed 07:33** (with the #9 fix). The area was set live, and the user
  made it permanent in `/etc/modprobe.d/calcomp-drawingboard.conf`:
  `options calcomp_drv area_x0=1000 area_x1=17000 area_y0=0 area_y1=6700`.
- **Second sticky strip near the top (user report).** In the sweep and all
  captures, Y 400–599 piles up (sticks at about 520) and Y 600–899 is almost
  never reported, over the full X width; normal again from about 1000. So
  the bad bands (top-origin) are **≈600–950 and ≈6800–7600**. New recommended
  area between them, keeping the 2.39:1 screen shape: **x 2200–15800,
  y 1000–6700** (13.6"×5.7"). **Applied and verified (2026-10-17):** no
  sticking anywhere. Live values and `/etc/modprobe.d/calcomp-drawingboard.conf`
  both read `area_x0=2200 area_x1=15800 area_y0=1000 area_y1=6700`.

## Tap = click, drag = stroke (2026-10-17)
- **Problem:** in KDE pop-up menus a tablet **tip tap** (BTN_TOUCH) does
  nothing, for the **pen too**, not just the puck. BTN_LEFT (as a tool button)
  clicks menus but doesn't draw in GIMP, and a KDE pen button set to left-click
  gives dots. One code couldn't do both.
- **Fix (user's idea, in the driver):** the tip (pen tip, or the puck button
  mapped to BTN_TOUCH) is held back. A release with no movement sends a BTN_LEFT
  click on release. Moving more than `tap_move` (40 counts) or holding for
  `tap_hold_ms` (300 ms) sends BTN_TOUCH down. Parameters are 0644. Built,
  W=1 clean, `make test` passes. Needs a reinstall and testing of menus and
  GIMP.

## Fast strokes: pen dropouts (2026-10-17)
- **Symptom (GIMP):** quick pen-down strokes have gaps, and the tablet clicks
  (it beeps on each new pen-down).
- **Pen (captures/evtest-fast.log):** during fast strokes the tablet goes silent
  for 80–320 ms. Either the tip is then reported up (55–122 ms, then down again)
  or the pen drops out of proximity entirely, with the pen having moved up to
  4.4" (about 14 in/s) during the silence. 9 tip presses for about 6 strokes.
- **Puck, same test (captures/evtest-fast-puck.log):** no dropouts at all
  in 28.7 s at up to 26 in/s, with 17 clean button presses. Gaps >60 ms only
  happened with zero movement.
- **Conclusion: pen hardware.** Most likely the battery contacts damaged by the
  old leak losing power when jolted (or the tip switch). The driver, adapter and
  tablet are fine. Driver-side debounce or longer proximity while the tip is down
  could hide short glitches, but would turn a 0.3 s dropout into a straight line.
  Not done.

## User's hardware
- Stylus (pen) + **16-button puck**. No 4-button puck, so 4-button support
  can be skipped or left untested.
- This may solve tool ID. With no 4-button puck, a packet with the 16-button
  flag (B1 bit 6) comes from the puck and anything else comes from the pen.
  **Catch:** the flag may only be set while a puck button is held. If so, a
  hovering puck with no button pressed would look like the pen. Check this in
  the capture. If it's true, track the last-seen tool, or add a module parameter
  for the tool type.

## Protocol, as implemented by the old driver

### Serial
9600 8N1, no flow control (from `default_options`, xf86Calcomp.c:81).

### Init sequence (`QueryHardware`, xf86Calcomp.c:541)
Commands are `ESC % ... CR`. The old driver waits about 500 ms between them.
| Step | String | Purpose |
|---|---|---|
| 1 | `\033%C1N81\r` | comm setup (N81) |
| 2 | `\033%L1\r` | origin upper-left |
| 3 | `\033%^23\r` | CalComp binary format |
| 4 | `\033%X1\r` / `\033%Y1\r` | increment = 1 |
| 5 | `\033%JR1000,0\r` | 1000 lpi |
| 6 | `\033%IR\r` | stream mode |
| 7 | `\033%VA0\r` (or `VA1`) | button reporting (or pressure) |
| 8 | `\033%__V\r` | query firmware ID (35-byte reply) |
| 9 | `\033%__p\r` | query product ID (20-byte reply) |
| 10 | `\033%VS\r` | query max coords (6-byte reply, see below) |
| 11 | `\033%IR\r` | absolute mode |

Others defined but not used: `\033%IT\r` relative mode, `\033%Q?\r` prompt
mode (marked "untested"), `?` = request position.

### 6-byte binary packet (comment at xf86Calcomp.c:381)
A packet starts with a byte that has bit 7 set. The other five bytes have bit 7 clear.
```
B1: 1 | b6: 16-btn-cursor flag | b5..b2: button bits 3..0 | b1..b0: X15..X14
B2: 0 | X13..X7
B3: 0 | X6..X0
B4: 0 | b6?: proximity (see below) | b3..b0: Y17..Y14
B5: 0 | Y13..Y7
B6: 0 | Y6..Y0
```
- X = (B1 & 0x03) << 14 | B2 << 7 | B3
- Y = B4(high bits) << 14 | B5 << 7 | B6. The old driver adds **all of B4** with
  no mask, so any prox bit leaks into Y. It gets away with it because it only
  uses Y when in proximity. Mask it properly.
- **Proximity is active-low:** the bit is set when the tool is *out* of range.
  The comment says bit 6, but the code uses `PROXIMITY_BIT 0x20` (bit 5).
  Confirm which one with a raw capture.
- Y is flipped (`y = max_y - y`) even though the driver sets origin upper-left.
- `ESC%VS` reply uses the same encoding: max_x = (b0&3)<<14 | b1<<7 | b2,
  max_y = b4<<7 | b5.

### Buttons (xf86Calcomp.c:416)
`v = (B1 & 0x7C) >> 2`, which gives 5 bits (bit 4 is the 16-button flag):
- `v > 15` → 16-button cursor, button = v − 15 (1..16), i.e. a **button
  number**, not a bitmask.
- else → button = v/2, then +1 if < 4. This is an odd mapping for the stylus
  and the 4-button cursor. Treat it as a hint and confirm it with a capture.
- In pressure mode (`VA1`) the same bits carry pressure instead, compared against
  `ButtonThreshold` (default 16).
- The packet has no tool ID. Pen vs 4-button puck can't be told apart, and the man
  page says they work "without changing the configuration". A new driver will
  probably report one tool type, or let the user pick one with a module parameter.
- The old driver's button-event logic (`delta`, line 449) is buggy. Don't copy it.
  Diff the button state and report each change instead.

## Still open
1. Proximity bit 5 vs 6, the stylus button codes, all 16 puck buttons, and
   whether the 16-button flag is set while the puck hovers with no button pressed.
   Settle these with `tools/capture.py` (below).
2. The driver doesn't need ASCII mode, since the old driver only used binary.
   But the ASCII status codes (`config_ascii_*` steps) give a second, readable
   view of the button encoding.
3. ~~Menu strip~~ Out of scope (see Scope). Optional `menu` / `config`
   capture groups exist if it's ever needed.

## Capture tool: `tools/capture.py`
Uses only the Python standard library. It sends the old driver's init sequence,
logs the replies to the ID and size queries, then walks through about 40
actions: pen hover/move/corners/tip/barrel buttons, puck hover/move, each of
the 16 puck buttons, and switching tools. For each one, press Enter, do the
action, then press Enter to stop. Results are saved to
`captures/<timestamp>.json` (all raw bytes plus decoded packets) and a `.txt`
summary.
```
python3 tools/capture.py --list              # see the steps
python3 tools/capture.py                     # full run on /dev/ttyUSB0
python3 tools/capture.py --steps pen         # or: baseline, puck, switch, menu, config, or step ids
python3 tools/capture.py --steps menu        # macro keys 1-18 in normal mode (safe)
python3 tools/capture.py --steps config      # Config/Exit, Restore 3 (ASCII), Restore Default
python3 tools/capture.py --no-init           # just listen, tablet as configured
python3 tools/capture.py --simulate          # dry run with a fake tablet
```
**Menu-strip groups**: optional, out of scope for the driver, and not in the
default run. Run them only with `--steps menu` / `--steps config`:
- `menu`: sweep over the strip, tap macro keys 1–18 (`--menu-buttons`,
  default 18) in normal mode, then key 1 with the puck. After each tap you hover
  at the center, so each step records the tap and the output right after it.
  Doesn't change settings.
- `config`: `config_enter` / `config_exit` (does output stop?);
  `config_restore_3` → ASCII (7E1, Point mode, so press the tip);
  `config_ascii_pen` / `_puck` / `_macro` record ASCII status codes per button
  and what a macro key sends in ASCII; `config_restore_default` →
  `config_after_check` should be binary again.
- **Save keys are never used.** The warning before these groups says so, and
  power-cycling always returns to Default.
- Both groups ask what the tablet did (light, beep...) and save the answer as a
  note. The re-init offer is skipped in the steps where ASCII is expected.

**Format detection.** Each step is classified as `binary`, `ascii`,
`ascii-7bit`, `binary+ascii` (it switched mid-step), `unknown`, or `none`.
`ascii-7bit` is 7-bit ASCII (7N1/7E1) read at 8N1: the stop or parity bit
shows up as bit 7, so the script masks it off. ASCII lines are matched against
the spec's `+XXXXX,+YYYYY,SS` pattern, and the parsed X/Y ranges and status
values go into the summary. The full lines are saved under `ascii` in the JSON.
If a step comes out as ASCII, the script offers to re-send the init (which
includes the binary-format command) and checks whether that worked. `unknown`
data may mean the baud rate changed, so try `--baud 19200` or `38400`.
(Hidden test option: `--simulate-format binary|ascii|ascii7|switch`.) Any non-packet bytes are printed live after each step.
At the "ready?" prompt, `s` skips and `q` quits and saves. At "keep?", `r`
redoes the step. Ctrl-C also saves. The decode shows both candidate prox bits
(B4 bit 5 and bit 6), the button field, and the 16-button flag, so these can be
compared across steps. The `ESC%VS` max-coords reply looks just like a packet,
so read it from that step's `reply_hex` in the JSON.

## Architecture note
The prompt has the module open `/dev/ttyUSB0` by name from kernel space.
Mainline kernels don't do this for serial input devices. The usual pattern is
a **serio driver** that gets bound to the tty with `inputattach` (that's how
wacom_serial4, the old serial mice, etc. work). It's cleaner, avoids
kernel-side file I/O, and works with USB-serial adapters. The serio driver
can send the init strings with `serio_write()`. One catch: `inputattach` needs
a new serio type ID for CalComp, or one of the generic ones can be reused.
A user-space `uinput` prototype (Python) is a quick way to confirm the decoding
first.

## Menu areas daemon: menud/ (2026-10-17)
- **Decision:** the kernel driver stays policy-free. `menud/calcomp_menud.py`
  (a per-user systemd unit, Python stdlib only, raw evdev/uinput ioctls,
  no python3-evdev) grabs the driver's device and creates
  `CalComp DrawingBoard III (mapped)` (abs mouse, same buttons, ID_INPUT_MOUSE)
  and `... (menu keys)` (keyboard). The drawing area → full range; menu
  rectangles → key combo or command on press (250 ms debounce); everything else
  dropped. Layout: `~/.config/calcomp/areas.toml` (reloaded on change/SIGHUP).
  Also `measure` (counts under the tool, two presses → rect), `overlay` (1:1
  SVG/PDF, tiled to Letter, alignment crosses + 1" bar), and `check`. See
  menud/README.md.
- Needs the driver's `area_* = 0` and udev `uaccess`
  (`menud/71-calcomp-menud.rules`): both are done by `sudo menud/install-root.sh`,
  which also comments the area options out of /etc/modprobe.d (and `--remove`
  restores them). Without the daemon, the pointer then covers the whole tablet.
- **Found:** /etc/modprobe.d/calcomp-drawingboard.conf actually reads
  `area_x0=1000 area_x1=16999 area_y0=0 area_y1=6700`, not 2200–15800/1000–6700
  as noted above; only the live values were 2200/15800/1000/6700.
- Verified without root: 15 unit tests (mapping, drag clamping, menus,
  debounce, dead space, SYN_DROPPED resync, reload); `udevadm verify`;
  `systemd-analyze --user verify`; the virtual devices created live: the
  mapped one is `ID_INPUT_MOUSE=1`, 346×144 mm (= the drawing area), KEY=ff00ff,
  ABS=3; overlay PDF = 2 Letter pages. User part installed (`menud/install.sh`),
  not enabled yet.
- **Root step done (08:08):** rule installed, `area_* = 0` live, conf line
  commented out. `menud/tests/live_test.py` **PASS**: with evtest it checked
  0/max corners, a click in the middle, shift+F13 from one menu square, the
  command from another, and nothing from dead space or the faulty strip.
  (The first run expected a 0,0 event the kernel filters because the device
  starts at 0,0; the test order was fixed.) Service enabled and running
  (graphical-session.target) and grabbing event28. `libinput list-devices`:
  `(mapped)` = pointer, 346×145 mm; `(menu keys)` = keyboard.
- **Real pen check (08:13–08:16, captures/libinput-menud.log):** in the
  journal, every bottom-row square fired (undo, redo, copy, paste, save,
  terminal); Konsole opened. libinput saw a keyboard press/release pair for
  each key action on `(menu keys)` (key names are hidden without
  --show-keycodes) and 2639 absolute motions on `(mapped)`. The pointer reached
  99.7 % down when the pen left the drawing area. There were no clicks in the
  drawing area during this capture; pass-through clicks are covered by
  live_test.py.
- **Overlay images (user request):** `image = "file.svg|png|jpg"` on a
  `[[menu]]`. It's drawn in the square on the overlay, embedded as a data URI,
  with paths relative to areas.toml. With an image, `label` is only printed if
  given. A missing file is only a warning for the daemon but an error for
  `overlay`. The example uses Breeze icons. Rendered with rsvg-convert and
  checked by eye; 16 unit tests pass. The user's own areas.toml (a copy of the
  old example) was left unchanged.
- **Personal layout (user request, 08:52):** ~/.config/calcomp/areas.toml is a
  3×22 grid of 0.625" squares with 0.125" gaps, x 812–17187, y 9150–11275
  (centred between the faulty strip at ~8450 and the bottom edge). Only row 1,
  columns 1–8 have actions (Undo, Redo, Cut, Copy, Paste, Select all, Delete,
  Find, with Breeze icons). The other 58 squares are placeholders named and
  labelled "(row,col)" with no action. The user will fill them in. The
  previous config is in areas.toml.bak.
- Config: `keys`/`command` are now optional (a placeholder has no action; a
  press logs `no action`).
- **Overlay tiling changed:** the user's sheet is the dashed frame, so nothing
  may be drawn outside it. `tile_starts()` picks page breaks that never cut a
  button (a page ends before the first button that doesn't fit, and the next
  starts 0.4" before that button). Each page draws only the buttons wholly
  inside its frame, clips the rest, and has its own 4 crosses and 1" bar. For
  the 3×22 grid on Letter, page 1 = columns 1–12 and page 2 = 13–22. Previews:
  menud/overlay-preview-page{1,2}.png. 18 unit tests pass.
- **`overlay --full` (user request):** one 18×12 in page (PDF 1296×864 pt) of
  the whole active area: drawing outline, faulty strips (hatched and labelled
  with coordinates), all buttons, 4 corner crosses, and a 1" bar. Written to
  ~/calcomp-overlay-full.pdf for later large-format printing. Checked by
  rendering it.
- **4th row + `visible` (user request, 09:0x):** 4 rows fit between the strip
  and the edge. The sweep capture shows normal density over y 8500–11999. The
  layout is now 4×22 at y 8800–11675 (rows 8800/9550/10300/11050), 88 squares,
  with row 4 all placeholders. The old 3-row file is
  ~/.config/calcomp/areas.toml.3rows.bak. New `visible = false`: the menu is
  parsed into `cfg.hidden`, isn't printed or active, and isn't
  overlap-checked.
- **Alignment marks are now placed, not fixed:** with 4 rows, the fixed
  corner crosses landed on button (4,12) and on the faulty strip (bad
  `measure` readings there). A `Placer` puts each cross, the 1" bar and the
  note in the free spot nearest its corner: inside the frame, 40 counts clear
  of buttons, faulty strips and the drawing area. A corner with no room is
  skipped with a note. On the 4×22 Letter pages, page 1 gets 4 crosses (left
  gutter) and page 2 gets 2 (right side); the --full sheet gets all 4 corners.
  20 unit tests pass.
