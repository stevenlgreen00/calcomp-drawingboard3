#!/usr/bin/env python3
"""Measure gaps in the tablet's packet stream, to choose prox_timeout_ms.

Sets the tablet up the same way the driver does (increment off, so it streams
~125 packets/s while a tool is in range), then records for a while and shows
how long the stream went quiet between packets. Gaps are measured between
read() calls that delivered at least one complete packet, which is also when
the driver would see them.

Run it while the driver is NOT attached (stop calcomp_attach first):

    python3 tools/gaps.py --seconds 30
"""

import argparse
import collections
import json
import os
import pty
import select
import sys
import termios
import threading
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture as c  # noqa: E402

# ESC%IR (run mode) turns increment mode back on, so X0/Y0 must come after it.
STREAM_SETUP = [b"\x1b%C1N81\r", b"\x1b%^23\r", b"\x1b%JR1000,0\r", b"\x1b%VA0\r",
                b"\x1b%IR\r", b"\x1b%X0\r", b"\x1b%Y0\r"]
BINS = [0, 10, 20, 30, 40, 50, 60, 80, 100, 150, 200, 300, 500, 1000]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--simulate", action="store_true", help="fake tablet on a pty (for testing this script)")
    ap.add_argument("--out", default=os.path.join(c.CAPTURE_DIR, "gaps-%s.json" % time.strftime("%Y%m%d-%H%M%S")))
    args = ap.parse_args()

    port = args.port
    if args.simulate:
        master, slave = pty.openpty()
        tty.setraw(master)
        threading.Thread(target=c.fake_tablet, args=(master, threading.Event()), daemon=True).start()
        port = os.ttyname(slave)
    try:
        fd = c.open_port(port, 9600)
    except (OSError, termios.error) as e:
        sys.exit(f"can't open {port}: {e}\n(if the driver is attached, stop calcomp_attach first)")
    print("setting up the tablet...")
    for cmd in STREAM_SETUP:
        c.send(fd, cmd, 0.25)

    print(f"recording {args.seconds:g} s: hover the pen, move it, press the tip and buttons, "
          "then the same with the puck. Keep the tools in range except when you mean to lift them.")
    events = []            # (time, packets completed in this read)
    buf = bytearray()
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        now = time.monotonic()
        buf += chunk
        pk, _ = c.split_packets(bytes(buf))
        # keep only an unfinished packet tail in buf
        last_sync = max((i for i, b in enumerate(buf) if b & 0x80), default=-1)
        tail = bytes(buf[last_sync:]) if last_sync >= 0 and len(buf) - last_sync < 6 else b""
        buf = bytearray(tail)
        if pk:
            events.append((now, len(pk)))
    os.close(fd)

    if len(events) < 2:
        sys.exit("no packets received: is a tool in range (power light steady)?")

    gaps = [(b[0] - a[0]) * 1000 for a, b in zip(events, events[1:])]
    total = sum(n for _, n in events)
    span = events[-1][0] - events[0][0]
    print(f"\n{total} packets in {len(events)} reads over {span:.1f} s "
          f"({total / span:.1f} packets/s while streaming, incl. pauses)")
    hist = collections.Counter()
    for g in gaps:
        lo = max(b for b in BINS if b <= g)
        hist[lo] += 1
    print("gap between deliveries   count")
    for lo, hi in zip(BINS, BINS[1:] + [None]):
        label = f"{lo}-{hi} ms" if hi else f">= {lo} ms"
        print(f"  {label:>14}         {hist[lo]}")
    for t in (50, 100, 150, 200, 300):
        print(f"timeout {t:3} ms would end proximity {sum(1 for g in gaps if g > t):4} times")
    with open(args.out, "w") as f:
        json.dump({"gaps_ms": gaps, "packets": total, "seconds": span}, f)
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
