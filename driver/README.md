# CalComp DrawingBoard III Linux driver

A kernel input driver for the CalComp (GTCO) DrawingBoard III serial
digitizer. It works with a USB-to-serial adapter. By default the tablet appears
as an **absolute-position mouse**: the pen and the 16-button puck move the
pointer directly to where they are on the tablet, with real left, middle and
right buttons. With `tablet_mode=1` it appears as a pen tablet instead.

It's a **serio driver**, the same design mainline uses for serial tablets such
as `wacom_serial4`. A small helper (`tools/calcomp_attach.py`) hands the serial
port to the kernel. This is what `inputattach` does, but `inputattach` has no
CalComp type. The driver then binds, sets up the tablet and creates the input
device.

Tested on a model 34180 (18"×12", firmware `70171A 07/14/94 16906-4`).

## Requirements

- The tablet in its **Default setup** (binary format 23, 9600 8N1, Run mode).
  Power-cycling restores it, as long as no Save key on the menu strip was
  pressed.
- Kernel headers and build tools, plus `evtest` for testing:
  ```bash
  sudo apt install linux-headers-$(uname -r) build-essential evtest
  ```
- `serport` (the serial line discipline), a standard module:
  `sudo modprobe serport`.

## Build

```bash
cd driver
make            # builds calcomp_drv.ko for the running kernel
make test       # optional: checks the packet decoder against real captures
```

## Load and attach

```bash
sudo modprobe serport
sudo insmod calcomp_drv.ko
sudo python3 ../tools/calcomp_attach.py /dev/ttyUSB0
```

The attach helper stays in the foreground while the tablet is in use. On
attach, the driver sends the setup commands, which takes about 3 s, and logs
what it found, something like:

```bash
sudo dmesg | grep -i calcomp
```
```
calcomp_drv serio1: firmware: 70171A 07/14/94 16906-4
calcomp_drv serio1: product: CalComp 3400
calcomp_drv serio1: CalComp DrawingBoard III, 18000x12000 counts, on ttyUSB0/serio0
```

For a stable port name, use
`/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0`
instead of `/dev/ttyUSB0`.

## Verify

```bash
grep -A6 "CalComp DrawingBoard" /proc/bus/input/devices
```

This shows the device with `Bus=0013` (RS232) and an `event` handler. Then:

```bash
sudo evtest
```

Pick "CalComp DrawingBoard III" and use the tools. In the default mouse mode:

| Action | Events |
|---|---|
| Pen or puck in range, moving | `ABS_X`, `ABS_Y` |
| Pen tip / lower side / upper side button | `BTN_LEFT` / `BTN_MIDDLE` / `BTN_RIGHT` |
| Puck button 0 / 1 / 3 | `BTN_LEFT` / `BTN_MIDDLE` / `BTN_RIGHT` (others: table below) |
| Tool lifted away | any held buttons released about 150 ms later; the pointer stays put |

With `tablet_mode=1` you get `BTN_TOOL_PEN`, `BTN_TOUCH`, `BTN_STYLUS`/`STYLUS2`
and `MSC_SERIAL` (1 = pen, 2 = puck) instead (see "Tablet mode" below).

Coordinates are 0–17999 × 0–11999 (for the 34180), with the origin top-left
and a resolution of 39 units/mm (1000 lpi). By default the whole tablet maps to
the whole desktop. To use only part of the tablet, see "Active area".

## Unload

Stop the attach helper with Ctrl-C (the input device disappears), then:

```bash
sudo rmmod calcomp_drv
```

## Install permanently (starts automatically)

```bash
sudo apt install dkms
sudo ../packaging/install.sh
```

This sets up:

- **The module through DKMS** (`/usr/src/calcomp_drv-0.1`), so it's rebuilt
  automatically when Debian installs a new kernel. Without DKMS, use
  `install.sh --no-dkms` and re-run it after every kernel update.
- **`/usr/local/sbin/calcomp-attach`**, the attach helper.
- **`calcomp-attach@.service`**: a udev rule starts `calcomp-attach@ttyUSB0`
  whenever the PL2303 adapter appears, at boot or on hot-plug, and systemd stops
  it when the adapter is unplugged. The same rule stops ModemManager from
  probing the port.
- **`serport`** loads at boot (`/etc/modules-load.d/`).

Afterwards, check:

```bash
systemctl status 'calcomp-attach@*'
journalctl -u 'calcomp-attach@*' -b
```

The tablet's Default setup already streams format 23 with increment off.
So if the tablet is switched on *after* the adapter was attached, it still
works, even though it missed the setup commands. It just reports the
fallback size if the size query went unanswered.

To set module parameters permanently, create
`/etc/modprobe.d/calcomp-drawingboard.conf`, for example:

```
options calcomp_drv prox_timeout_ms=200
```

To remove everything: `sudo ../packaging/uninstall.sh`.

The udev rule matches **any** PL2303 adapter, because the adapter has no
serial number. If you use other PL2303 devices, restrict the rule to a USB
port as described in `packaging/99-calcomp-drawingboard.rules`.

## Module parameters

| Parameter | Default | Meaning |
|---|---|---|
| `tablet_mode` | N | N: absolute mouse. Y: pen tablet (see "Tablet mode") |
| `prox_timeout_ms` | 150 | Treat the tool as gone after this long without a packet (releases buttons; in tablet mode, proximity out). Live-changeable |
| `puck_keys` | see below | 16 comma-separated key codes for puck buttons 0–F. Leave unset (or 0) for the mode's default |
| `area_x0`, `area_y0`, `area_x1`, `area_y1` | whole tablet | **Active area**: the rectangle (in counts, origin top-left) that maps onto the whole screen; positions outside it stick to its edge. `x1`/`y1` = 0 means the far edge. Live-changeable |
| `max_x`, `max_y` | 0 = ask the tablet | Override the size if the `ESC%VS` query fails (the fallback is 18000×12000) |
| `reset_on_attach` | Y | Send XON + `ESC%VR1` (recall the Default bank) before configuring. Note: if you ever press the menu strip's Save Default key, this recalls *your* saved setup |
| `send_init` | Y | Set to N if the tablet must not be reconfigured (it must already stream format 23) |
| `cmd_delay_ms` | 250 | Pause after each setup command |
| `tap_click`, `tap_move`, `tap_hold_ms` | Y, 40, 300 | Tablet mode only, see below |
| `puck_as_mouse` | N | Tablet mode only: report the puck as `BTN_TOOL_MOUSE` (libinput ignores that on non-Wacom tablets) |

Parameters are read when the tablet attaches. To set them permanently, put
them in `/etc/modprobe.d/calcomp-drawingboard.conf`, for example
`options calcomp_drv prox_timeout_ms=200`, then reinstall or re-plug.

Default puck button mapping. Button 0 (the manual's "pick" button) and button
3 are the large buttons on opposite sides of the top row:

| Puck | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8–F |
|---|---|---|---|---|---|---|---|---|---|
| Mouse mode | LEFT | MIDDLE | SIDE | RIGHT | EXTRA | FORWARD | BACK | TASK | BTN_0–BTN_7 |
| Tablet mode | TOUCH | STYLUS | SIDE | STYLUS2 | EXTRA | FORWARD | BACK | TASK | BTN_0–BTN_7 |

`BTN_SIDE` … `BTN_TASK` are the extra mouse buttons, often "back"/"forward"
in browsers. `BTN_0`–`BTN_7` show up in `evtest` but most desktops ignore them.
To remap, pass all 16 codes, for example
`puck_keys=272,274,273,275,276,277,278,279,256,257,258,259,260,261,262,263`
(LEFT, RIGHT, MIDDLE, SIDE for buttons 0–3; the codes are in
`linux/input-event-codes.h`). In mouse mode, tablet codes (`BTN_TOOL_*`,
`BTN_TOUCH`, `BTN_STYLUS*`) are rejected, because they'd make the device a
tablet again.

## Active area

The `area_*` parameters pick the rectangle of the tablet that is stretched over
the screen. Coordinates are in counts (1000 per inch), origin top-left. Use it
to match the screen's shape, or to avoid a damaged strip. Try values live:

```bash
echo 1000  | sudo tee /sys/module/calcomp_drv/parameters/area_x0
echo 17000 | sudo tee /sys/module/calcomp_drv/parameters/area_x1
echo 6700  | sudo tee /sys/module/calcomp_drv/parameters/area_y1
```

When you're happy with it, make it permanent in
`/etc/modprobe.d/calcomp-drawingboard.conf`:

```
options calcomp_drv area_x0=2200 area_x1=15800 area_y0=1000 area_y1=6700
```

**The author's tablet** has two faulty strips of the sensing grid across the
full width, at y ≈ 600–950 and y ≈ 6800–7600 (0.6"–0.95" and 6.8"–7.6" from the
top): the position sticks and then jumps. The clean area between them, cut to a
2992×1253 ultrawide screen's 2.39:1 shape, is x 2200–15800, y 1000–6700
(13.6" × 5.7").

To use the rest of the board as menu buttons instead, leave the area at 0 and
use the `menud` daemon (`../menud/README.md`), which does this mapping itself.

### Finding faulty strips: `tools/scan_bands.py`

Old tablets can have a damaged wire in the sensing grid: along that strip the
pointer sticks and then jumps, the same in every application. To check yours
and get a suggested area, run this with the driver attached:

```bash
sudo python3 ../tools/scan_bands.py --screen 2992x1253
```

Use your own screen size in pixels; leave `--screen` out for the largest clean
area. It guides you through six slow sweeps: top to bottom at the left, middle
and right, then left to right at the top, middle and bottom. It reports any
strips and prints the `area_*` commands and modprobe.d line to use. During the
scan the area is reset to the whole tablet, then restored. A strip only counts
if at least two of the three sweeps crossing it skip it, so one hurried sweep
doesn't cause a false alarm. `--axis y` does only the top-to-bottom sweeps.

To analyse a saved `evtest` log instead:
`python3 ../tools/scan_bands.py --replay sweep.log --axis y`.

## How it behaves, and why

The details are in `../NOTES.md`.

- **Mouse mode is the default** because KDE's handling of tablet input left
  window controls and app buttons ignoring left clicks, however they were sent
  (tip, a `BTN_LEFT` tool button, or KDE's pen-button rebinding). With real mouse
  buttons everything works: clicks, double-clicks, menus, dragging, and drawing
  in GIMP with the left button held. The tablet has no pressure or tilt, so
  little is lost. KDE's Drawing Tablet page doesn't list it in this mode.
- **The setup commands** come from GTCO's command reference
  (summarised in `docs/TABLET.md`). On attach the driver first sends XON and
  `ESC%VR1` (recall the Default setup), because the tablet obeys single-byte
  commands at any time, and line noise from plugging the adapter in can leave it
  stopped or misconfigured. Then it sets format 23 and 1000 lpi, runs the ID and
  size queries in incremental run mode (quiet while a tool rests), and finishes
  with plain run mode (`ESC%R`), which streams continuously while a tool is in
  range.
- **Tool gone:** the protocol has no proximity bit, so the tablet just stops
  sending when the tool leaves. After `prox_timeout_ms` without packets the
  driver releases any held buttons. The tablet sends every 8 ms, but USB-serial
  adapters deliver in bursts (gaps of 16–40 ms are normal, with occasional
  60–120 ms dropouts), so the default is 150 ms. If strokes break up, raise it
  (`tools/gaps.py` measures the gaps).
- **One puck button at a time:** the puck sends a button number, not a
  bitmask. Holding two buttons reports only one of them.
- **Y is flipped:** the tablet's origin is bottom-left, and input devices use
  top-left.
- Out of scope: the menu strip (it configures the tablet itself), ASCII and
  other output formats, and pressure or tilt pens.

### Tablet mode (`tablet_mode=1`)

Reports a pen tablet: the pen and the puck are both `BTN_TOOL_PEN` tools, told
apart by `MSC_SERIAL` (1 = pen, 2 = puck), with the tip as `BTN_TOUCH` and
side buttons as `BTN_STYLUS`/`BTN_STYLUS2`. KDE's Drawing Tablet page lists
it; set its **pen button 1** to middle-click and **pen button 2** to right-click.
Known limits under KDE: tip taps don't click pop-up menus or window controls,
and a pen's `BTN_RIGHT`/`BTN_MIDDLE` are ignored. Details:

- **Pen or puck?** The packet only identifies the tool while one of its buttons
  is down. Until then the driver assumes the last tool it identified, starting
  with the pen. When the guess turns out wrong, it ends the old tool first.
- **A serial number with every packet:** a tool held perfectly still produces
  identical positions, which the input layer filters out. Without `MSC_SERIAL`
  in every report, libinput would see 50 ms of silence, assume the tool left,
  and keep forcing it out and back in.
- **The puck is a second pen:** libinput turns off mouse and lens tools on
  non-Wacom tablets, so a `BTN_TOOL_MOUSE` puck could never move the pointer.
- **Tap = click, drag = stroke** (`tap_click`): a tip press is held back. If it's
  released without moving, the driver sends a `BTN_LEFT` click (for KDE menus).
  If it moves more than `tap_move` (40 counts, about 1 mm), or is held for
  `tap_hold_ms` (300 ms), it becomes `BTN_TOUCH`, so GIMP draws and drags work.
  The first ~1 mm of a stroke is lost, and a quick tap in GIMP is a click, not
  a dot. `tap_click=0` gives the raw tip.

## Troubleshooting

- **No events, and the tablet's power light blinks:** the tool isn't sensed.
  Press a button to wake it (tools sleep after 1–5 min), or replace the
  batteries (pen: two 393 silver-oxide cells; cordless puck: two AAA).
- **`can't switch ... line discipline`:** run the helper as root and load
  `serport`.
- **The device appears but doesn't move:** check `dmesg`. A
  "no valid size reply" warning means the setup commands may have been
  ignored. The menu strip's Bank B key 8 can disable host commands.
  Restore the Default setup (Config/Exit → Restore Default → Config/Exit) and
  re-attach.
- **Garbage right after plugging the adapter in:** ModemManager may be
  probing the port. Re-attach, or tell ModemManager to ignore the adapter with
  a udev rule (`ENV{ID_MM_DEVICE_IGNORE}="1"` for 067b:2303).

## Files

- `calcomp_drv.c`: the driver
- `calcomp_proto.h`: packet decoding, shared with the userspace test
- `Kbuild`, `Makefile`: out-of-tree build (`make`, `make test`, `sudo make install`)
- `test/`: decoder test against the captures in `../captures`
- `../tools/calcomp_attach.py`: attach helper (the `inputattach` equivalent)
- `dkms.conf`, `../packaging/`: permanent install (DKMS, systemd service, udev rule, install/uninstall scripts)
- `../tools/scan_bands.py`: finds faulty strips in the sensing grid and suggests an active area
- `../tools/gaps.py`: measures pauses in the packet stream, for tuning `prox_timeout_ms` (driver detached)
