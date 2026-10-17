# Step by step: a DrawingBoard III on a modern Linux desktop

This is the path that got a 1990s CalComp DrawingBoard III (model 34180, 18" ×
12") working as a pointing device on Debian 13 with KDE Plasma 6 (Wayland),
including menu buttons on the unused parts of the board. Follow it in order;
each step says how to check it worked before moving on.

For what the tablet is and how it talks, see [TABLET.md](TABLET.md).

## 0. What you need

- **The tablet**, its power supply (12–17 V DC, centre negative) and its serial
  cable (the one with the power-jack junction).
- **A pointing tool:** a pen and/or a 4- or 16-button puck, with fresh batteries
  if cordless (pen: two 393 cells; puck: two AAA).
- **A USB-to-RS-232 adapter.** This project used a Prolific PL2303 (`067b:2303`).
  Others should work, but the udev rule in `packaging/` matches the PL2303's
  ID, so edit it for a different adapter.
- **Linux** with kernel headers and build tools. Tested on Debian 13, kernel
  7.2, KDE Plasma 6.3 on Wayland, libinput 1.28. Nothing here is
  KDE-specific, except the notes on KDE's tablet settings.

```bash
sudo apt install build-essential linux-headers-$(uname -r) dkms evtest libinput-tools
```

Get the code:

```bash
git clone https://github.com/<you>/calcomp-drawingboard3.git
```

```bash
cd calcomp-drawingboard3
```

## 1. Put the tablet in its Default setup

The driver expects the **Default** setup: binary format 23, 9600 8N1, Run mode,
1000 lpi. A power cycle restores it, as long as nobody ever pressed a **Save**
key on the menu strip. If in doubt: tap `Config/Exit`, `Restore Default`,
`Config/Exit` on the menu strip. Never press the Save keys.

Plug in the adapter and connect the tablet. Switch it on. The power light
should glow steadily when a tool is over the tablet, and blink otherwise.

## 2. (Optional) See that it talks

With nothing else using the port:

```bash
python3 tools/monitor.py --init
```

Move the pen over the tablet. You should see decoded packets scroll by
(position and buttons). Press `v` to query the size; a 34180 answers 18000 ×
12000. Press `q` to quit. If nothing arrives, check the cable and the power
light, and wake the tool by pressing a button. Your user needs access to the
serial port (the `dialout` group on Debian), or run it with sudo.

`tools/capture.py` records a guided session (every button, both tools) to
`captures/` for deeper debugging. See NOTES.md.

## 3. Build and test the driver

```bash
make -C driver
```

```bash
make -C driver test
```

`make test` checks the packet decoder against about 9800 packets captured from
a real tablet. It should end with every vector passing.

## 4. Install it permanently

```bash
sudo packaging/install.sh
```

This installs the module through DKMS (so it's rebuilt for new kernels), the
attach helper (`/usr/local/sbin/calcomp-attach`), a systemd service and a udev
rule. The rule starts `calcomp-attach@ttyUSB0` whenever the adapter appears,
at boot or when you plug it in, and keeps ModemManager away from the port.
It also makes sure `serport` loads at boot.

**Check:**

```bash
systemctl status 'calcomp-attach@*'
```

```bash
sudo dmesg | grep -i calcomp
```

You should see the firmware, product and size lines, e.g.
`CalComp DrawingBoard III, 18000x12000 counts, on ttyUSB0/serio0`. The pointer
now follows the pen or puck over the **whole tablet**. Tip, lower and upper
pen buttons are left, middle and right click; on the puck, buttons 0, 1 and 3
are left, middle and right.

```bash
sudo evtest
```

Pick "CalComp DrawingBoard III" to see the raw events. The full parameter list
(button maps, timeouts, tablet mode) is in [driver/README.md](../driver/README.md).

**Why a mouse and not a tablet:** by default the driver reports an absolute
mouse, like a virtual machine's "USB tablet". Under KDE, a pen tablet's clicks
were ignored by window controls and many buttons; a mouse's clicks work
everywhere, including drawing in GIMP. This tablet has no pressure or tilt, so
nothing is lost. `tablet_mode=1` gives a real pen tablet if you want to
experiment.

Uninstall: `sudo packaging/uninstall.sh`.

## 5. Check the sensing grid for faulty strips

Thirty years on, a wire in the sensing grid can fail. Along such a strip the
pointer sticks at one height and then jumps, in every application, across the
full width. Scan for them:

```bash
sudo python3 tools/scan_bands.py --screen 2992x1253
```

Use your own screen size in pixels. It guides you through six slow sweeps and
reports any bad strips, plus a suggested clean area with the same shape as the
screen. The test tablet has two strips, at y ≈ 600–950 and ≈ 6800–8400 counts
from the top.

## 6. Choose how the tablet maps to the screen

### Option A: just a drawing area (driver only)

The driver can map any rectangle of the tablet onto the whole screen. Try it
live with the numbers `scan_bands.py` suggested:

```bash
echo 2200 | sudo tee /sys/module/calcomp_drv/parameters/area_x0
```

Do the same for `area_x1`, `area_y0` and `area_y1`. Then make it permanent in
`/etc/modprobe.d/calcomp-drawingboard.conf`:

```
options calcomp_drv area_x0=2200 area_x1=15800 area_y0=1000 area_y1=6700
```

Outside the area the pointer sticks to the screen edge. That's all you need
if you don't want menu buttons.

### Option B: drawing area plus menu buttons (`menud`)

`menud` is a small per-user daemon. It maps the drawing area onto the screen
and turns the rest of the board into buttons that send key combos or run
commands. It replaces the driver's area mapping, so the driver must report
the whole tablet. Full details are in [menud/README.md](../menud/README.md); the
short version follows.

1. **Root part** (once): a udev rule, so your desktop user can read the tablet
   and create virtual input devices, plus the driver's `area_*` set to 0:

   ```bash
   sudo menud/install-root.sh
   ```

2. **User part:** installs `calcomp-menud` and a systemd user service, and
   copies the example layout to `~/.config/calcomp/areas.toml`:

   ```bash
   menud/install.sh
   ```

   ```bash
   systemctl --user enable --now calcomp-menud
   ```

3. **Test the setup** without touching your desktop. A fake tablet is
   checked with evtest:

   ```bash
   python3 menud/tests/live_test.py
   ```

4. **Edit the layout:** `~/.config/calcomp/areas.toml`. The daemon reloads it
   when you save. Coordinates are tablet counts (1000 per inch, origin
   top-left). Starting points:
   - `menud/areas.example.toml`: a drawing area plus 7 large buttons with icons.
   - `menud/layouts/grid-4x22.toml`: a 4 × 22 grid of 5/8" buttons filling the
     bottom of a 34180. Only the edit commands are filled in; the rest are
     labelled `(row,col)` with no action.

   A button has `keys = "ctrl+z"` or `command = "konsole"`. It can also have
   `label`, `image`, `buttons` (which tool buttons trigger it) and
   `visible = false` (hide it). Check your edits with:

   ```bash
   ~/.local/bin/calcomp-menud check
   ```

   The journal shows each button as it fires:

   ```bash
   journalctl --user -u calcomp-menud -f
   ```

5. **Measure where things are:** stop the daemon and run `measure`. It shows
   the coordinates under the tool, and each button press marks a point; two
   marks print a rectangle ready to paste.

   ```bash
   systemctl --user stop calcomp-menud
   ```

   ```bash
   ~/.local/bin/calcomp-menud measure
   ```

6. **Print a template** at 1:1 to lay under the tablet's clear cover:

   ```bash
   ~/.local/bin/calcomp-menud overlay -o ~/calcomp-overlay.pdf
   ```

   ```bash
   ~/.local/bin/calcomp-menud overlay --full -o ~/calcomp-overlay-full.pdf
   ```

   The first tiles the button area onto Letter pages. Page breaks never cut a
   button, and each sheet is its dashed frame. The second makes one 18" × 12"
   sheet of the whole tablet, with faulty strips hatched, for a large-format
   printer. Print at **100% / actual size** and check the 1-inch bar with a
   ruler.

7. **Align the template:** with the daemon stopped and `measure` running, put
   the puck's crosshair on the centre of an alignment cross. Compare the
   reading with the numbers printed by the cross:
   - x reads too high: the sheet is too far right, so slide it left.
   - y reads too high: the sheet is too low, so slide it up.

   Line up a second cross far from the first, pivoting around the first, then
   tape the sheet down. Within ±40 counts is plenty, since the gap between
   buttons is 125 counts. Keep the pen upright, or use the puck, for
   alignment. Then start the daemon again:

   ```bash
   systemctl --user start calcomp-menud
   ```

## 7. If something's wrong

| Symptom | What to check |
|---|---|
| No device, `systemctl status 'calcomp-attach@*'` failing | `dmesg`; is `serport` loaded; is the adapter `/dev/ttyUSB0`? |
| Device exists, pointer doesn't move | power light blinking: wake or re-battery the tool. "no valid size reply" in dmesg: restore the Default setup (step 1) and replug |
| Garbage or nothing after replugging the adapter | the driver resets the tablet on attach; if it persists, power-cycle the tablet |
| Strokes break up | raise `prox_timeout_ms` (default 150) live in `/sys/module/calcomp_drv/parameters/`; `tools/gaps.py` measures your adapter's gaps (driver detached) |
| Pointer sticks at one height | a faulty strip (step 5): keep the drawing area off it |
| Gaps and extra clicks only with the pen | a worn cordless pen (battery contacts); compare with the puck |
| menud: `waiting for the tablet: Permission denied` | the udev rule isn't installed, or you're not on the active seat: `sudo menud/install-root.sh` |
| menud: menus don't react, pointer covers only part of the board | the driver's `area_*` isn't 0: `calcomp-menud check` warns about it |
| Button fires twice | raise `debounce_ms` in areas.toml |

## 8. What's where

| Path | |
|---|---|
| `driver/` | the kernel driver, its tests and DKMS config ([README](../driver/README.md)) |
| `packaging/` | install/uninstall scripts, systemd service, udev rule |
| `tools/` | attach helper, byte monitor, capture recorder, gap meter, strip scanner |
| `menud/` | the menu-button daemon ([README](../menud/README.md)) |
| `docs/TABLET.md` | the tablet's specifications, protocol and commands |
| `captures/` | real-tablet captures used by the tests and tools ([README](../captures/README.md)) |
| `NOTES.md` | the development log: every finding, with the evidence |
