# calcomp-menud: menu buttons and drawing-area mapping

A per-user daemon for the CalComp DrawingBoard III (`calcomp_drv`, default
absolute-mouse mode). It turns the parts of the tablet outside the drawing area
into macro buttons:

- **Drawing area:** the pointer follows the tool, rescaled so the area covers
  the whole screen. Buttons pass through. This is what the driver's `area_*`
  parameters used to do.
- **Menu rectangles:** the pointer stays still, and a button press runs the
  menu's action: a key combo (sent from a virtual keyboard) or a shell command.
- **Everywhere else** (faulty strips, gaps): nothing happens and the pointer
  stays still.

How it works: the daemon grabs the driver's evdev device (`EVIOCGRAB`), so the
desktop no longer sees it, and creates two uinput devices:

| device | what it is |
|---|---|
| `CalComp DrawingBoard III (mapped)` | absolute mouse: ABS_X/Y 0–17999/0–11999, the driver's buttons (LEFT/RIGHT/MIDDLE/SIDE/EXTRA/FORWARD/BACK/TASK, BTN_0–7). No BTN_TOOL_*/TOUCH/STYLUS, so udev calls it `ID_INPUT_MOUSE`, not a tablet. Its size is the drawing area's size. |
| `CalComp DrawingBoard III (menu keys)` | keyboard used for `keys` actions |

The daemon uses only the Python standard library (3.11+; no python3-evdev). It
runs as you, not root, so commands start in your desktop session.

Behaviour details:
- A button pressed in the drawing area stays a drag until it's released. The
  pointer clamps to the drawing area's edge if you leave the area, so strokes
  aren't cut off and menus don't trigger mid-drag.
- A button pressed outside the drawing area is swallowed until it's released,
  even if the tool then moves into the drawing area.
- A menu triggers on the press. A second press on the same menu within
  `debounce_ms` (250 ms) is ignored, because the pen's tip can bounce.
- If the tablet disappears (replug, driver reattach), the daemon releases any
  held buttons and waits for the tablet to come back. The virtual devices stay.

## Setup

### 1. Root part (once): udev rule and the whole tablet

```bash
sudo menud/install-root.sh
```

This script:
- Installs `/etc/udev/rules.d/71-calcomp-menud.rules`. The `uaccess` tag gives
  the user logged in on the desktop seat an ACL on the tablet's
  `/dev/input/eventN`, on the daemon's virtual devices (so `evtest` and
  `libinput debug-events` work without sudo), and on `/dev/uinput`. You don't
  need to join the `input` group, which would let every program you run read
  every keyboard.
- Sets the driver's live `area_x0/y0/x1/y1` to 0, so it reports the whole
  18"×12" tablet unmapped.
- Comments out the `area_*` options in
  `/etc/modprobe.d/calcomp-drawingboard.conf` (backup: `.bak`) so they stay 0
  after a reboot.

Without the daemon running, the pointer now covers the **whole tablet,
including the faulty strips**. `sudo menud/install-root.sh --remove` removes
the rule and puts the driver's area back (in the file and live).

To do it by hand instead:

```bash
sudo install -m 644 menud/71-calcomp-menud.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=input
for p in area_x0 area_y0 area_x1 area_y1; do echo 0 | sudo tee /sys/module/calcomp_drv/parameters/$p; done
```

Then delete the `area_*` options from `/etc/modprobe.d/calcomp-drawingboard.conf`.

### 2. User part

```bash
menud/install.sh
```

```bash
systemctl --user enable --now calcomp-menud
```

`install.sh` copies the program to `~/.local/lib/calcomp-menud/`, puts a
`calcomp-menud` command in `~/.local/bin`, installs the user unit, and, if you
don't have one yet, copies `areas.example.toml` to `~/.config/calcomp/areas.toml`.
It then runs `calcomp-menud check`, which reports problems such as the device
not being readable or the driver area still being set.

The unit is part of `graphical-session.target`. It starts with the Plasma
session, so commands get `WAYLAND_DISPLAY` and similar variables. Logs:

```bash
journalctl --user -u calcomp-menud -f
```

Each triggered menu is logged, e.g. `menu 'undo': keys ctrl+z`.

Uninstall: `menud/install.sh --remove` (your `areas.toml` is kept), then
`sudo menud/install-root.sh --remove`.

## Config: `~/.config/calcomp/areas.toml`

Coordinates are **tablet counts**: 1000 per inch, origin at the top-left, x to
the right, y down. A rectangle includes `x0`/`y0` and ends just before
`x1`/`y1`. The file is reloaded when it changes (or on
`systemctl --user reload calcomp-menud`). An invalid file is logged and the
old layout stays active. `calcomp-menud check` validates it and prints the
layout.

```toml
[tablet]
device = "CalComp DrawingBoard III"   # evdev name to grab
width = 18000
height = 12000

debounce_ms = 250

[drawing]                 # mapped onto the whole screen
x0 = 2200
y0 = 1000
x1 = 15800
y1 = 6700

[[faulty]]                # optional: only for warnings, measure and the overlay
x0 = 0
y0 = 6750
x1 = 18000
y1 = 8450

[[menu]]
name = "undo"
image = "icons/undo.svg"  # optional picture for the overlay (PNG/JPEG/SVG)
label = "Undo"            # printed on the overlay ("\n" for a line break)
keys = "ctrl+z"           # or a list: ["ctrl+a", "ctrl+c"]
x0 = 1000
y0 = 9000
x1 = 2250
y1 = 10250

[[menu]]
name = "terminal"
command = "konsole"       # run with sh -c, detached; exit codes are logged
buttons = ["left"]        # optional: only these buttons trigger it
x0 = 14500
y0 = 9000
x1 = 15750
y1 = 10250
```

- **keys:** names from `linux/input-event-codes.h` without `KEY_`, in lower
  case: `a`–`z`, `0`–`9`, `f1`–`f24`, `enter`, `esc`, `tab`, `space`,
  `delete`, `home`, `pageup`, `left`, `volumeup`, `playpause`, … Aliases:
  `ctrl`, `shift`, `alt`, `altgr`, `super`/`meta`/`win`, `del`, `pgup`/`pgdn`,
  `plus` (keypad +), `print`. These are **key codes**, not characters, so the
  letter that arrives depends on your keyboard layout, like a physical key.
- **image:** a PNG, JPEG or SVG (or .svgz) drawn in the square on the printed
  overlay, scaled to fit with its proportions kept. The label, if any, goes
  underneath in smaller text. Without `label`, a square with an image has no
  text except its action. `~` works, and relative paths are taken from the
  folder `areas.toml` is in. The file is embedded in the SVG/PDF. A missing
  image is only a warning for the daemon, since it doesn't need the picture,
  but `overlay` stops with an error. Icon themes are a good source, e.g.
  `/usr/share/icons/breeze/actions/24/edit-undo.svg`.
- **buttons:** `left right middle side extra forward back task btn0`…`btn7`.
  Pen: tip = left, lower = middle, upper = right. Puck: 0/1/2/3 =
  left/middle/side/right, 4–7 = extra/forward/back/task, 8–F = btn0–btn7.
  Without `buttons`, any button triggers the menu. That means with the puck,
  you can give one square to each puck button, or restrict a square to a
  single button.
- **visible = false** hides a menu completely: it isn't printed, a press there
  does nothing (dead space), and it isn't checked for overlaps, so you can
  keep a button definition without using it. `check` lists hidden menus.
- A menu with neither `keys` nor `command` is a placeholder: it prints on
  the overlay, and a press on it does nothing (the journal logs `no action`).
- Menus can't overlap each other or the drawing area (this is checked). A menu
  over a `[[faulty]]` strip gives a warning.
- Changes to the drawing area take effect on reload. A change to
  `[tablet] device` needs a restart.

### This tablet's layout (`areas.example.toml`)

Faulty strips (top-origin): y ≈ 600–950 and ≈ 6800–8400, full width. The
example's `[[faulty]]` entries include a margin. Usable space for menus:

| region | counts | size |
|---|---|---|
| bottom | y 8500–12000, full width | 18" × 3.5" |
| left margin | x 0–2200, y 1000–6700 | 2.2" × 5.7" |
| right margin | x 15800–18000, y 1000–6700 | 2.2" × 5.7" |
| top strip | y 0–500 | 18" × 0.5" |

The example has a row of 1.25" squares in the bottom region at y 9000–10250:
Undo, Redo, Copy, Paste and Save on the left (key combos), and Konsole and
Screenshot (`spectacle --region`) on the right (commands). Each has a Breeze
icon for the overlay. Commented examples
show the side margins and the top strip.

## Measuring: `calcomp-menud measure`

```bash
systemctl --user stop calcomp-menud
```

```bash
calcomp-menud measure
```

```bash
systemctl --user start calcomp-menud
```

The daemon holds the grab, so stop it first; `measure` says so if you forget.
`measure` grabs the tablet itself, so the pointer doesn't move while it runs.
It shows the live position in counts and inches, and what's there according
to your config (drawing, menu name, FAULTY STRIP). Each button press marks a
point. Every two marks print a ready-to-paste rectangle:

```
mark 1: x 980, y 8990 (left) 
mark 2: x 2260, y 10270 (left) menu 'undo'
  x0 = 980, y0 = 8990, x1 = 2260, y1 = 10270   (1.28" x 1.28")
```

`measure` warns if the driver's `area_*` is still set. Its coordinates are only
tablet counts when the area is 0.

## Printable overlay: `calcomp-menud overlay`

```bash
calcomp-menud overlay -o ~/calcomp-overlay.pdf
```

```bash
calcomp-menud overlay --full -o ~/calcomp-overlay-full.pdf
```

The first command tiles the region around the menu buttons onto US Letter
landscape pages (`--page a4|a3|tabloid`, `--portrait`, `--margin 0.5`).

The second (`--full`, short for `--region tablet --page none`) makes one
18"×12" page covering the whole active area: the drawing-area outline, the
faulty strips (hatched, with their coordinates) and every button. The sheet's
edge is the active area's edge (x 0, y 0 = top-left). It needs a large-format
printer or a print shop (e.g. ARCH B 18"×12", or larger paper trimmed to the
edge). Print it at 100%.

The drawing is 1:1: 1 tablet count = 0.001". It shows the menu buttons with
their images, labels and actions, the drawing-area outline, the faulty strips (hatched),
a 1-inch scale bar, and four alignment crosses labelled with their tablet
coordinates.

- **Print at 100% / "actual size"**, not "fit to page", then check the 1-inch
  bar with a ruler.
- Each page has a dashed frame, which is the sheet: everything is drawn inside
  it. Page breaks are chosen so no button is ever cut. A page ends before the
  first button that wouldn't fit, and the next page starts just before that
  button. Only buttons that fit wholly inside a frame are drawn on that page.
  Each sheet has its own alignment crosses (up to four, one per corner) and a
  1-inch bar, so each one can be aligned on the tablet by itself. They're
  placed in the free space nearest each corner, never on a button or a faulty
  strip, where `measure` would read wrong numbers. If a corner has no room,
  that cross is left out (with a note); two crosses are enough to align a
  sheet. A button bigger than the frame is an
  error (use a bigger `--page` or a smaller `--margin`).
- To align the sheet under the tablet's clear overlay, stop the daemon and
  run `calcomp-menud measure`. Put the tool on an alignment cross; the printed
  counts should match. Move the sheet until two opposite crosses match.
- PDF output uses `rsvg-convert` (`librsvg2-bin`). Pass an `.svg` file name
  for SVG only. `--keep-svg` keeps the per-page SVGs next to the PDF.

## Testing the virtual devices

These need the udev rule (or sudo):

```bash
evtest
```

With no argument, `evtest` lists the devices: pick
`CalComp DrawingBoard III (mapped)` or `(menu keys)`. While the daemon runs,
the original `CalComp DrawingBoard III` device shows nothing, because it's
grabbed.

```bash
libinput debug-events --device /dev/input/eventN
```

`libinput list-devices` should list the mapped device with `Capabilities:
pointer` (not `tablet`), and `(menu keys)` as a keyboard.

Unit tests (no devices needed):

```bash
python3 -m unittest discover -s menud/tests
```

End-to-end test with a fake tablet (needs the udev rule; the real tablet and
the running daemon aren't touched):

```bash
python3 menud/tests/live_test.py
```

It creates a fake `CalComp DrawingBoard III test source`, runs the daemon on
it with test device names (which the udev rule hides from libinput, so no
synthetic click or key reaches your desktop), and checks with `evtest` that
the drawing area maps to 0–17999 / 0–11999, a click passes through, a menu
square sends shift+F13, another runs a command, and presses in dead space or
on a faulty strip produce nothing.

## Files

| file | |
|---|---|
| `calcomp_menud.py` | the daemon and the `run`/`measure`/`overlay`/`check` commands |
| `keycodes.py` | key names → Linux key codes |
| `areas.example.toml` | layout for this tablet |
| `layouts/grid-4x22.toml` | a 4 × 22 grid of 5/8" buttons across the bottom of a 34180 (edit commands filled in, the rest placeholders) |
| `calcomp-menud.service` | systemd user unit |
| `71-calcomp-menud.rules` | udev: uaccess for the tablet, the virtual devices and /dev/uinput |
| `install.sh` / `install-root.sh` | user part / root part of the setup |
| `tests/test_menud.py` | config and mapping tests |
| `tests/live_test.py` | end-to-end test with a fake tablet and evtest |
