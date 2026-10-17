#!/usr/bin/env python3
r"""Live byte monitor for the CalComp DrawingBoard III, for troubleshooting.

Prints every byte the tablet sends as it arrives: complete binary packets are
decoded on one line each, anything else is shown as hex + text. Single-key
commands (no Enter needed):

    i  send the init sequence (same as capture.py)
    p  send a prompt '?' (asks for one position report)
    v  ask for max coordinates (ESC%VS) - a quick "is it alive?" check
    :  type a raw command to send (escapes: \e = ESC, \r, \n, \xNN), e.g. \e%IR\r
    c  clear the screen
    q  quit

    python3 tools/monitor.py                     # /dev/ttyUSB0, 9600 baud, no init
    python3 tools/monitor.py --init              # send the init sequence first
    python3 tools/monitor.py --simulate          # fake tablet, for trying it out
"""

import argparse
import os
import pty
import select
import sys
import termios
import threading
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture as c  # noqa: E402  (shared serial setup, decode, init sequence)


def parse_raw(text):
    """'\\e%IR\\r' -> b'\\x1b%IR\\r'. Supports \\e, \\r, \\n, \\t, \\\\ and \\xNN."""
    out, i = bytearray(), 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "x" and i + 3 < len(text) + 1:
                out.append(int(text[i + 2:i + 4], 16)); i += 4; continue
            out += {"e": b"\x1b", "r": b"\r", "n": b"\n", "t": b"\t", "\\": b"\\"}.get(nxt, nxt.encode())
            i += 2
            continue
        out += ch.encode()
        i += 1
    return bytes(out)


def show_packet(p, n):
    d = c.decode(p)
    prox = "out" if d["b4_bit5"] else "IN "
    print(f"#{n:<6} {d['hex']}  x={d['x']:6} y={d['y']:6} btn={d['btn_field']:2} "
          f"16btn={int(d['flag16'])} prox(b4.5)={prox} b1={d['b1_bits']} b4={d['b4_bits']}")


def show_other(b):
    print(f"  other {len(b):3} bytes: {b.hex(' ')}   {c.printable(b)!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600, choices=sorted(c.BAUDS))
    ap.add_argument("--init", action="store_true", help="send the init sequence at start")
    ap.add_argument("--simulate", action="store_true", help="use a fake tablet on a pty")
    args = ap.parse_args()

    stop = threading.Event()
    if args.simulate:
        master, slave = pty.openpty()
        tty.setraw(master)
        threading.Thread(target=c.fake_tablet, args=(master, stop), daemon=True).start()
        port_path = os.ttyname(slave)
    else:
        port_path = args.port
    try:
        fd = c.open_port(port_path, args.baud)
    except OSError as e:
        sys.exit(f"can't open {port_path}: {e}")

    def init():
        print("--- sending init sequence ---")
        for name, cmd, wait in c.INIT_SEQUENCE + c.QUERIES + c.FINAL_SEQUENCE:
            os.write(fd, cmd)
            time.sleep(wait if wait < 1 else 0.5)   # replies show up in the live stream
        print("--- init sent ---")

    print(f"Monitoring {port_path} at {args.baud} 8N1.  keys: i=init p=prompt v=max-coords :=raw command c=clear q=quit")
    print("Move/press the pen or puck and watch the tablet's power light (steady = tool sensed).")
    if args.init:
        init()

    interactive = sys.stdin.isatty()
    old = termios.tcgetattr(sys.stdin) if interactive else None
    if interactive:
        tty.setcbreak(sys.stdin)
    buf = bytearray()
    n = 0
    last_rx = time.monotonic()
    quiet_shown = False
    try:
        while True:
            watch = [fd, sys.stdin] if interactive else [fd]
            r, _, _ = select.select(watch, [], [], 0.2)
            if fd in r:
                try:
                    buf += os.read(fd, 4096)
                except BlockingIOError:
                    pass
                last_rx = time.monotonic()
                quiet_shown = False
            # emit complete packets / other bytes; keep a possible partial packet
            while buf:
                if buf[0] & 0x80:
                    if len(buf) < 6:
                        if time.monotonic() - last_rx > 0.3:   # stale partial packet
                            show_other(bytes(buf)); buf.clear()
                        break
                    chunk = bytes(buf[:6])
                    if any(b & 0x80 for b in chunk[1:]):
                        cut = next(i for i in range(1, 6) if chunk[i] & 0x80)
                        show_other(bytes(buf[:cut])); del buf[:cut]
                        continue
                    n += 1
                    show_packet(chunk, n); del buf[:6]
                else:
                    cut = next((i for i, b in enumerate(buf) if b & 0x80), len(buf))
                    if cut == len(buf) and time.monotonic() - last_rx < 0.3:
                        break        # text may still be arriving; wait for the rest
                    show_other(bytes(buf[:cut])); del buf[:cut]
            if not quiet_shown and time.monotonic() - last_rx > 5:
                print(f"  ... nothing received for 5 s ({n} packets so far)")
                quiet_shown = True
            if interactive and sys.stdin in r:
                k = sys.stdin.read(1).lower()
                if k == "q":
                    break
                elif k == "i":
                    init()
                elif k == "p":
                    print("--- sent '?' ---"); os.write(fd, b"?")
                elif k == "v":
                    print("--- sent ESC%VS ---"); os.write(fd, b"\x1b%VS\r")
                elif k == ":":
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)   # line input
                    try:
                        text = input("raw command> ")
                    except EOFError:
                        text = ""
                    tty.setcbreak(sys.stdin)
                    if text:
                        try:
                            raw = parse_raw(text)
                        except ValueError as e:
                            print(f"--- bad escape: {e} ---")
                        else:
                            print(f"--- sent {raw!r} ---")
                            os.write(fd, raw)
                elif k == "c":
                    print("\033[2J\033[H", end="")
    except KeyboardInterrupt:
        pass
    finally:
        if interactive:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        stop.set()
        os.close(fd)
        print(f"\n{n} packets received.")


if __name__ == "__main__":
    main()
