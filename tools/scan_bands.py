#!/usr/bin/env python3
"""Find faulty strips in a DrawingBoard III's sensing grid and suggest an area.

Old tablets can have a damaged grid wire (or its channel): along that strip the
reported position sticks and then jumps, so a range of positions is never
reported. This tool walks you through slow sweeps across the tablet, finds such
strips, and suggests an active area (the calcomp_drv area_* parameters) that
avoids them, optionally matching your screen's shape.

It reads the driver's input device while the driver stays attached. During the
scan it sets the active area to the whole tablet and restores it afterwards.

    sudo python3 tools/scan_bands.py                      # guided scan
    sudo python3 tools/scan_bands.py --screen 2992x1253   # also fit the screen shape
    python3 tools/scan_bands.py --replay captures/evtest-sweep.log --axis y

Needs root (to read /dev/input and set the area parameters). Standard library only.
"""

import argparse
import fcntl
import os
import re
import select
import struct
import sys
import time

DEVICE_NAME = "CalComp DrawingBoard III"
PARAM_DIR = "/sys/module/calcomp_drv/parameters"
AREA_PARAMS = ("area_x0", "area_y0", "area_x1", "area_y1")
COUNTS_PER_INCH = 1000

EV_ABS = 3
ABS_X, ABS_Y = 0, 1
EVENT = struct.Struct("llHHi")                  # struct input_event (native time_t)
ABSINFO = struct.Struct("6i")                   # struct input_absinfo


def eviocgabs(code):
    """_IOR('E', 0x40 + code, struct input_absinfo)"""
    return (2 << 30) | (ABSINFO.size << 16) | (ord("E") << 8) | (0x40 + code)


BIN = 50            # analysis resolution in counts (0.05")
MIN_SWEEP_SPAN = 0.5   # a sweep must cover this fraction of the axis to count
MARGIN = 100        # keep this far from a bad strip when suggesting the area
MERGE_GAP = 200     # bands closer than this (0.2") are treated as one

SWEEPS = {
    "y": [("left", "near the LEFT edge"), ("middle", "down the MIDDLE"), ("right", "near the RIGHT edge")],
    "x": [("top", "near the TOP edge"), ("middle", "across the MIDDLE"), ("bottom", "near the BOTTOM edge")],
}


# ---------------------------------------------------------------- analysis

def find_bands(sweeps, axis_max):
    """sweeps: list of lists of positions along one axis (one list per sweep).
    Returns [(lo, hi, stuck_at)] in counts: ranges skipped by at least two of
    the sweeps that crossed them (or by the only sweep, if just one crossed)."""
    nbins = axis_max // BIN + 1
    crossed = [0] * nbins       # sweeps whose span includes the bin
    missed = [0] * nbins        # ...of those, how many never reported it
    density = [0] * nbins
    for pos in sweeps:
        if not pos:
            continue
        lo, hi = min(pos), max(pos)
        if hi - lo < MIN_SWEEP_SPAN * axis_max:
            continue
        seen = set(p // BIN for p in pos)
        for p in pos:
            density[p // BIN] += 1
        # skip the bins at the sweep's ends: it may simply have started there
        for b in range(lo // BIN + 1, hi // BIN):
            crossed[b] += 1
            if b not in seen:
                missed[b] += 1

    bad = [crossed[b] > 0 and missed[b] >= min(2, crossed[b]) for b in range(nbins)]
    nonzero = sorted(d for d in density if d)
    median = nonzero[len(nonzero) // 2] if nonzero else 0

    bands = []
    b = 0
    while b < nbins:
        if not bad[b]:
            b += 1
            continue
        start = b
        while b + 1 < nbins and bad[b + 1]:
            b += 1
        end = b
        # the value it sticks at piles up just next to the gap
        stuck = None
        for nb in (start - 1, end + 1):
            if 0 <= nb < nbins and density[nb] > 2 * max(median, 1):
                if stuck is None or density[nb] > density[stuck]:
                    stuck = nb
        lo, hi = start * BIN, (end + 1) * BIN - 1
        if stuck is not None:
            lo, hi = min(lo, stuck * BIN), max(hi, (stuck + 1) * BIN - 1)
        bands.append((lo, hi, stuck * BIN + BIN // 2 if stuck is not None else None))
        b += 1

    # The gap can show on both sides of the stuck value, and the jump after it
    # leaves a sparse tail: bands closer than MERGE_GAP are one fault.
    merged = []
    for lo, hi, stuck in sorted(bands):
        if merged and lo - merged[-1][1] <= MERGE_GAP:
            plo, phi, pstuck = merged[-1]
            merged[-1] = (plo, max(phi, hi), pstuck if pstuck is not None else stuck)
        else:
            merged.append((lo, hi, stuck))
    return merged


def clean_intervals(bands, axis_max):
    """The parts of 0..axis_max-1 that keep MARGIN away from every band."""
    out, start = [], 0
    for lo, hi, _ in sorted(bands):
        end = lo - MARGIN
        if end > start:
            out.append((start, end))
        start = max(start, hi + MARGIN)
    if axis_max - 1 > start:
        out.append((start, axis_max - 1))
    return out


def suggest_area(xbands, ybands, max_x, max_y, screen=None):
    """Largest clean rectangle; cut (centred) to the screen's aspect if given."""
    xs = clean_intervals(xbands, max_x)
    ys = clean_intervals(ybands, max_y)
    if not xs or not ys:
        return None
    x0, x1 = max(xs, key=lambda i: i[1] - i[0])
    y0, y1 = max(ys, key=lambda i: i[1] - i[0])
    if screen:
        aspect = screen[0] / screen[1]
        w, h = x1 - x0, y1 - y0
        if w / h > aspect:          # too wide: narrow it
            nw = round(h * aspect)
            x0 += (w - nw) // 2
            x1 = x0 + nw
        else:                       # too tall: shorten it
            nh = round(w / aspect)
            y0 += (h - nh) // 2
            y1 = y0 + nh
    return x0, y0, x1, y1


def inches(c):
    return f'{c / COUNTS_PER_INCH:.2f}"'


def report(xbands, ybands, max_x, max_y, screen, axes):
    print()
    for name, bands, axis in (("horizontal", ybands, "y"), ("vertical", xbands, "x")):
        if axis not in axes:
            continue
        if not bands:
            print(f"No faulty {name} strips found (along {axis}).")
            continue
        print(f"Faulty {name} strips (along {axis}, counts from the "
              f"{'top' if axis == 'y' else 'left'}):")
        for lo, hi, stuck in bands:
            s = f", sticks at about {stuck}" if stuck is not None else ""
            print(f"  {axis} {lo:5d}-{hi:5d}  ({inches(lo)}-{inches(hi)}){s}")
    area = suggest_area(xbands if "x" in axes else [], ybands if "y" in axes else [],
                        max_x, max_y, screen)
    if not area:
        print("\nNo clean area left to suggest.")
        return
    x0, y0, x1, y1 = area
    print(f"\nSuggested active area: x {x0}-{x1}, y {y0}-{y1}  "
          f"({inches(x1 - x0)} x {inches(y1 - y0)}"
          f"{', fitted to ' + str(screen[0]) + 'x' + str(screen[1]) if screen else ''})")
    print("\nTry it live:")
    for name, v in zip(AREA_PARAMS, (x0, y0, x1, y1)):
        print(f"  echo {v} | sudo tee {PARAM_DIR}/{name}")
    print("\nMake it permanent:")
    print(f"  echo 'options calcomp_drv area_x0={x0} area_x1={x1} area_y0={y0} area_y1={y1}'"
          f" | sudo tee /etc/modprobe.d/calcomp-drawingboard.conf")


# ---------------------------------------------------------------- replay

def replay(path, axis):
    """Positions from an evtest log, split into sweeps at pauses or lifts."""
    ev = re.compile(r"Event: time (\d+\.\d+), type \d+ \(EV_ABS\), code \d+ \((ABS_[XY])\), value (-?\d+)")
    syn = re.compile(r"Event: time (\d+\.\d+), -+ SYN_REPORT")
    want = "ABS_Y" if axis == "y" else "ABS_X"
    sweeps, cur, last_t, last_v = [], [], None, None
    for line in open(path):
        m = ev.match(line)
        if m and m[2] == want:
            t, v = float(m[1]), int(m[3])
            # a new sweep after a pause or a big jump back (returning to the start)
            if last_t is not None and (t - last_t > 0.5 or abs(v - last_v) > 3000):
                sweeps.append(cur)
                cur = []
            cur.append(v)
            last_t, last_v = t, v
    sweeps.append(cur)
    return [s for s in sweeps if s]


# ---------------------------------------------------------------- live scan

def find_event_device():
    text = open("/proc/bus/input/devices").read()
    for block in text.split("\n\n"):
        if f'N: Name="{DEVICE_NAME}"' in block:
            m = re.search(r"\bevent(\d+)\b", block)
            if m:
                return f"/dev/input/event{m[1]}"
    return None


def read_params():
    try:
        return {p: open(os.path.join(PARAM_DIR, p)).read().strip() for p in AREA_PARAMS}
    except OSError:
        return None


def write_params(values):
    for p, v in values.items():
        with open(os.path.join(PARAM_DIR, p), "w") as f:
            f.write(str(v))


def record_sweep(fd, axis):
    """Positions along `axis` until Enter is pressed."""
    code = ABS_Y if axis == "y" else ABS_X
    pos = []
    while True:
        r, _, _ = select.select([fd, sys.stdin], [], [])
        if sys.stdin in r:
            sys.stdin.readline()
            return pos
        data = os.read(fd, EVENT.size * 64)
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, etype, ecode, value = EVENT.unpack_from(data, i)
            if etype == EV_ABS and ecode == code:
                pos.append(value)


def live(device, axes, screen):
    if os.geteuid() != 0:
        sys.exit("run as root (sudo), to read the input device and set the area parameters")
    device = device or find_event_device()
    if not device:
        sys.exit(f'no "{DEVICE_NAME}" input device: is the tablet attached?')
    saved = read_params()
    if saved is None:
        sys.exit(f"can't read {PARAM_DIR}: is calcomp_drv loaded?")

    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    max_x = ABSINFO.unpack(fcntl.ioctl(fd, eviocgabs(ABS_X), bytes(ABSINFO.size)))[2] + 1
    max_y = ABSINFO.unpack(fcntl.ioctl(fd, eviocgabs(ABS_Y), bytes(ABSINFO.size)))[2] + 1
    print(f"Tablet {max_x}x{max_y} counts on {device}.")
    write_params({p: 0 for p in AREA_PARAMS})       # whole tablet while scanning
    print("Active area temporarily set to the whole tablet; it will be restored.\n")
    print("Use the puck (or the pen) and keep it on the surface, buttons up.\n"
          "Each sweep: press Enter, then move SLOWLY and steadily from one edge to the\n"
          "other (about 5-10 seconds), then press Enter again.\n")
    results = {"x": [], "y": []}
    try:
        for axis in axes:
            direction = "from the TOP edge to the BOTTOM edge" if axis == "y" else \
                        "from the LEFT edge to the RIGHT edge"
            for _, where in SWEEPS[axis]:
                while True:
                    input(f"Sweep {where}, {direction}. Press Enter to start...")
                    while select.select([fd], [], [], 0)[0]:     # drop events from before
                        os.read(fd, 1 << 16)
                    print("  recording... press Enter when you reach the other edge")
                    pos = record_sweep(fd, axis)
                    span = (max(pos) - min(pos)) if pos else 0
                    full = max_y if axis == "y" else max_x
                    if span >= MIN_SWEEP_SPAN * full:
                        print(f"  ok: {len(pos)} positions, {inches(span)} covered\n")
                        results[axis].append(pos)
                        break
                    print(f"  only {inches(span)} covered; please sweep from edge to edge again\n")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        write_params(saved)
        print(f"Active area restored ({', '.join(f'{k}={v}' for k, v in saved.items())}).")
        os.close(fd)

    xb = find_bands(results["x"], max_x) if results["x"] else []
    yb = find_bands(results["y"], max_y) if results["y"] else []
    report(xb, yb, max_x, max_y, screen, [a for a in axes if results[a]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--screen", help="your screen size in pixels, e.g. 2992x1253, to fit its shape")
    ap.add_argument("--axis", choices=["x", "y", "both"], default="both",
                    help="which strips to look for: y = horizontal strips (vertical sweeps), "
                         "x = vertical strips (horizontal sweeps)")
    ap.add_argument("--device", help="input device (default: find it by name)")
    ap.add_argument("--replay", metavar="EVTEST_LOG", help="analyse a saved evtest log instead of scanning")
    ap.add_argument("--max", default="18000x12000", help="tablet size in counts for --replay")
    args = ap.parse_args()

    screen = None
    if args.screen:
        m = re.fullmatch(r"(\d+)[xX](\d+)", args.screen)
        if not m:
            sys.exit("--screen must look like 2992x1253")
        screen = (int(m[1]), int(m[2]))
    axes = ["y", "x"] if args.axis == "both" else [args.axis]

    if args.replay:
        mx, my = (int(v) for v in args.max.lower().split("x"))
        xb = find_bands(replay(args.replay, "x"), mx) if "x" in axes else []
        yb = find_bands(replay(args.replay, "y"), my) if "y" in axes else []
        report(xb, yb, mx, my, screen, axes)
    else:
        live(args.device, axes, screen)


if __name__ == "__main__":
    main()
