#!/usr/bin/env python3
"""Guided raw-data capture for the CalComp DrawingBoard III.

Initializes the tablet with the command sequence used by the old X.Org driver
(xf86-input-calcomp), records the replies to its ID/size queries, then walks
you through a list of actions (hover the pen, press puck button 7, ...) and
records the raw bytes the tablet sends during each one.

Everything is saved to captures/<timestamp>.json, plus a human-readable
<timestamp>.txt summary, for working out the packet format.

Standard library only (no pyserial needed).

    python3 tools/capture.py                      # /dev/ttyUSB0, 9600 baud
    python3 tools/capture.py --port /dev/ttyUSB1
    python3 tools/capture.py --steps pen          # only the pen steps
    python3 tools/capture.py --steps menu         # optional: menu-strip macro keys 1-18
    python3 tools/capture.py --steps config       # optional: Config/Exit, Restore 3 (ASCII), Restore Default
    python3 tools/capture.py --list               # show all steps
    python3 tools/capture.py --simulate           # dry run, no tablet
"""

import argparse
import datetime
import json
import os
import pty
import re
import select
import sys
import termios
import threading
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(os.path.dirname(HERE), "captures")

# Init sequence from xf86Calcomp.c QueryHardware(). (name, command, reply wait s)
INIT_SEQUENCE = [
    ("comm_setup",   b"\x1b%C1N81\r",     0.5),
    ("origin_ul",    b"\x1b%L1\r",        0.5),
    ("binary_fmt",   b"\x1b%^23\r",       0.5),
    ("x_increment",  b"\x1b%X1\r",        0.5),
    ("y_increment",  b"\x1b%Y1\r",        0.5),
    ("res_1000lpi",  b"\x1b%JR1000,0\r",  0.5),
    ("stream_mode",  b"\x1b%IR\r",        0.5),
    ("pressure_off", b"\x1b%VA0\r",       0.5),
]
QUERIES = [
    ("firmware_id", b"\x1b%__V\r", 2.0),
    ("product_id",  b"\x1b%__p\r", 2.0),
    ("max_coords",  b"\x1b%VS\r",  2.0),
]
FINAL_SEQUENCE = [
    ("absolute_mode", b"\x1b%IR\r", 0.5),
]

# (group, id, instruction). Keep each action steady until you press Enter.
STEPS = [
    ("baseline", "idle", "Keep the pen and puck well away from the tablet (nothing should be sent)."),

    ("pen", "pen_hover_center", "Hold the PEN hovering just above the CENTER of the tablet; don't press anything."),
    ("pen", "pen_hover_high", "Slowly raise the PEN until it goes out of range, then bring it back. Repeat a few times."),
    ("pen", "pen_move", "Hover the PEN and move it slowly LEFT to RIGHT, then TOP to BOTTOM."),
    ("pen", "pen_corner_tl", "Hover the PEN at the TOP-LEFT corner of the active area."),
    ("pen", "pen_corner_tr", "Hover the PEN at the TOP-RIGHT corner of the active area."),
    ("pen", "pen_corner_bl", "Hover the PEN at the BOTTOM-LEFT corner of the active area."),
    ("pen", "pen_corner_br", "Hover the PEN at the BOTTOM-RIGHT corner of the active area."),
    ("pen", "pen_tip", "Press the PEN TIP down on the center, hold it, then lift. Repeat a few times."),
    ("pen", "pen_barrel_lower", "Hover the PEN and press the LOWER barrel button (nearest the tip); hold it, then release. Repeat."),
    ("pen", "pen_barrel_upper", "Hover the PEN and press the UPPER barrel button; hold it, then release. Repeat."),
    ("pen", "pen_tip_plus_lower", "Press the PEN TIP down AND hold the LOWER barrel button at the same time."),
    ("pen", "pen_tip_plus_upper", "Press the PEN TIP down AND hold the UPPER barrel button at the same time."),
    ("pen", "pen_leave", "Hover the PEN, then lift it away quickly (proximity-out)."),

    ("puck", "puck_hover", "Put the 16-button PUCK on the tablet center; don't press any button. "
                           "(If it's asleep, wake it with one button press BEFORE pressing Enter.)"),
    ("puck", "puck_move", "Slide the PUCK slowly around the tablet without pressing buttons."),
] + [
    ("puck", f"puck_btn_{label}",
     f"Hold the PUCK still and press button '{label}'; hold it, release, press it again.")
    for label in "0123456789ABCDEF"
] + [
    # Not 1+2: holding those ~3 s changes a cordless puck's RF frequency (manual p.29).
    ("puck", "puck_two_buttons", "Press PUCK buttons '4' and '5' together briefly (under 1 s), then release."),
    ("puck", "puck_leave", "Lift the PUCK straight off the tablet."),

    ("switch", "pen_to_puck", "Hover the PEN, take it away, then put the PUCK down (no buttons)."),
    ("switch", "puck_to_pen", "Have the PUCK on the tablet, take it off, then hover the PEN."),
]


HOVER_AFTER = " Then move the PEN to the CENTER of the drawing area and hover ~3 s before pressing Enter."
CONFIG_SEQ = "tap Config/Exit (1 beep, config light ON), tap Restore {key}, tap Config/Exit (2 beeps, light OFF)"

# Steps where ASCII output is expected, so don't offer to re-init to binary.
# (The ESC commands are 8N1; the tablet is 7E1 here, so they'd be garbled anyway.)
ASCII_EXPECTED = {"config_restore_3", "config_ascii_pen", "config_ascii_puck", "config_ascii_macro"}


def menu_steps(n):
    """Menu-strip steps (manual p.12-21). Two groups, run last:

    menu   - keys 1-n in NORMAL mode, where they are macro keys. Doesn't change settings.
    config - Config/Exit, then Restore 3 (CalComp 2000 ASCII, 7E1, Point mode) and
             Restore Default to come back. Restore changes only the current session;
             Save is never used, so power-cycling always returns to Default.
    """
    steps = [("menu", "menu_sweep",
              "Config light must be OFF. Hover the PEN and move it slowly along the whole menu "
              "strip, Config/Exit to the last Restore key, WITHOUT pressing anything.")]
    for k in range(1, n + 1):
        steps.append(("menu", f"menu_macro_{k}",
                      f"Config light OFF. Tap macro key {k} with the PEN tip." + HOVER_AFTER))
    steps.append(("menu", "menu_macro_1_puck",
                  "Config light OFF. Put the PUCK crosshair on macro key 1 and press puck button '0'. "
                  "Then slide the PUCK to the CENTER and hold it there ~3 s before pressing Enter."))

    steps += [
        ("config", "config_enter",
         "Tap Config/Exit ONCE (1 beep, config light ON). Then hover the PEN at the CENTER and "
         "press the tip a couple of times (shows whether output stops in config mode)."),
        ("config", "config_exit",
         "Tap Config/Exit again (2 beeps, config light OFF)." + HOVER_AFTER),
        ("config", "config_restore_3",
         "Switch to ASCII: " + CONFIG_SEQ.format(key="3") + ". This is Point mode, so then "
         "PRESS the pen TIP on the center 3 times, pausing between presses."),
        ("config", "config_ascii_pen",
         "Still in ASCII mode. Hover the PEN at the center and press, ONE AT A TIME with a pause: "
         "the tip, the lower side button, the upper side button. (Never all three together.)"),
        ("config", "config_ascii_puck",
         "Still in ASCII mode. Put the PUCK at the center and press, one at a time with a pause: "
         "buttons '0', '5', 'A', 'F'."),
        ("config", "config_ascii_macro",
         "Still in ASCII mode, config light OFF. Tap macro key 1 with the PEN tip, then press the "
         "tip on the center once."),
        ("config", "config_restore_default",
         "Switch back: " + CONFIG_SEQ.format(key="Default") + "." + HOVER_AFTER),
        ("config", "config_after_check",
         "Hover the PEN at the CENTER (should be binary again; if not, power-cycle the tablet)."),
    ]
    return steps

BAUDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
         4800: termios.B4800, 2400: termios.B2400, 1200: termios.B1200}


def open_port(path, baud):
    """Open a tty raw: 8N1, no parity, no flow control, non-blocking."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0                                    # iflag
    attrs[1] = 0                                    # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL   # cflag: 8N1, no RTS/CTS
    attrs[3] = 0                                    # lflag
    attrs[4] = attrs[5] = BAUDS[baud]
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def read_for(fd, seconds, stop_on_enter=False, live=False):
    """Read bytes for up to `seconds`. If stop_on_enter, Enter ends it early."""
    data = bytearray()
    end = time.monotonic() + seconds
    watch = [fd, sys.stdin] if stop_on_enter else [fd]
    next_status = 0.0
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        ready, _, _ = select.select(watch, [], [], min(left, 0.2))
        if fd in ready:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = b""
            data += chunk
        if sys.stdin in ready:
            sys.stdin.readline()
            break
        if live and time.monotonic() >= next_status:
            next_status = time.monotonic() + 0.25
            n = len(split_packets(data)[0])
            print(f"\r  recording... {len(data)} bytes, {n} packets, {left:4.1f}s left ",
                  end="", flush=True)
    if live:
        print()
    return bytes(data)


def send(fd, cmd, wait):
    os.write(fd, cmd)
    return read_for(fd, wait)


def split_packets(data):
    """Frame on bit 7 the same way the old driver does. Returns (packets, junk bytes)."""
    packets, cur, junk = [], None, 0
    for b in data:
        if b & 0x80:
            if cur is not None:
                junk += len(cur)          # short packet interrupted by a new sync byte
            cur = [b]
        elif cur is not None:
            cur.append(b)
            if len(cur) == 6:
                packets.append(bytes(cur))
                cur = None
        else:
            junk += 1
    return packets, junk


def non_packet_bytes(data):
    """Bytes left over after removing complete 6-byte packets (e.g. ID-query text
    that arrived mixed in with streaming coordinates)."""
    out, i = bytearray(), 0
    while i < len(data):
        chunk = data[i:i + 6]
        if (len(chunk) == 6 and chunk[0] & 0x80
                and not any(b & 0x80 for b in chunk[1:])):
            i += 6
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def decode(p):
    """Decode one 6-byte packet, keeping both candidate prox bits for comparison."""
    b1, b2, b3, b4, b5, b6 = p
    return {
        "hex": p.hex(" "),
        "b1_bits": f"{b1:08b}",
        "b4_bits": f"{b4:08b}",
        "x": ((b1 & 0x03) << 14) | (b2 << 7) | b3,
        "y": ((b4 & 0x0F) << 14) | (b5 << 7) | b6,
        "btn_field": (b1 & 0x7C) >> 2,      # old driver's 5-bit button value
        "flag16": bool(b1 & 0x40),          # 16-button cursor flag
        "b4_bit5": bool(b4 & 0x20),         # old driver's PROXIMITY_BIT (active-low)
        "b4_bit6": bool(b4 & 0x40),         # what the old driver's comment says
    }


# One ASCII report, e.g. "+05120,+03400,01" (CalComp 2000 ASCII, format 0)
ASCII_LINE = re.compile(rb"^\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*,\s*(\S+)\s*$")


def analyze_ascii(rest):
    """Look for ASCII-mode output in bytes that weren't binary packets.

    Tries the bytes as-is, and with bit 7 masked off: a tablet sending 7-bit
    ASCII (7N1 or 7E1) read at our 8N1 shows up with bit 7 set by the stop or
    parity bit. Returns None if it doesn't look like text lines."""
    if len(rest) < 8:
        return None
    high = any(b & 0x80 for b in rest)
    buf, encoding = (bytes(b & 0x7F for b in rest), "ascii-7bit") if high else (rest, "ascii")
    ok = sum(1 for b in buf if 32 <= b < 127 or b in (9, 10, 13))
    if ok / len(buf) < 0.9 or not any(b in (10, 13) for b in buf):
        return None
    lines = [l.strip() for l in re.split(rb"[\r\n]+", buf) if l.strip()]
    parsed = []
    for l in lines:
        m = ASCII_LINE.match(l)
        if m:
            parsed.append({"x": int(m[1]), "y": int(m[2]), "status": m[3].decode("ascii", "replace")})
    return {"encoding": encoding, "lines": [l.decode("ascii", "replace") for l in lines],
            "parsed": parsed}


def summarize(data):
    packets, junk = split_packets(data)
    dec = [decode(p) for p in packets]
    rest = non_packet_bytes(data)
    text = analyze_ascii(rest)
    if not data:
        fmt = "none"
    elif text:
        fmt = ("binary+" if packets else "") + text["encoding"]
    elif packets and len(rest) <= 0.1 * len(data):
        fmt = "binary"
    else:
        fmt = ("binary+" if packets else "") + "unknown"   # unknown: maybe a different baud rate
    s = {"bytes": len(data), "format": fmt, "packets": len(packets), "junk_bytes": junk}
    if text:
        s["ascii_lines"] = len(text["lines"])
        s["ascii_parsed"] = len(text["parsed"])
        s["ascii_sample"] = text["lines"][:5]
        if text["parsed"]:
            s["ascii_x_range"] = [min(t["x"] for t in text["parsed"]), max(t["x"] for t in text["parsed"])]
            s["ascii_y_range"] = [min(t["y"] for t in text["parsed"]), max(t["y"] for t in text["parsed"])]
            s["ascii_status_values"] = sorted({t["status"] for t in text["parsed"]})
    if dec:
        s["x_range"] = [min(d["x"] for d in dec), max(d["x"] for d in dec)]
        s["y_range"] = [min(d["y"] for d in dec), max(d["y"] for d in dec)]
        s["b1_values"] = sorted({d["b1_bits"] for d in dec})
        s["b4_high_bits"] = sorted({d["b4_bits"][:4] for d in dec})
        s["btn_field_values"] = sorted({d["btn_field"] for d in dec})
        s["flag16_values"] = sorted({d["flag16"] for d in dec})
    return s, dec, text


def printable(b):
    return "".join(chr(c) if 32 <= c < 127 else f"\\x{c:02x}" for c in b)


def fake_tablet(master_fd, stop, fmt="binary"):
    """--simulate: emit packets in the old driver's layout, plus canned query replies.
    fmt: binary, ascii, ascii7 (7-bit ASCII as seen at 8N1), or switch (alternates
    binary / ASCII every 3 s; the binary-format command restarts it in binary)."""
    t = 0
    switch_t0 = time.monotonic()
    while not stop.is_set():
        r, _, _ = select.select([master_fd], [], [], 0.01)
        if r:
            cmd = os.read(master_fd, 64)
            if b"__V" in cmd:
                os.write(master_fd, b"SIMULATED FIRMWARE 1.0\r")
            elif b"__p" in cmd:
                os.write(master_fd, b"SIM DB-III\r")
            elif b"VS" in cmd:
                os.write(master_fd, bytes([0x80, 0x5d, 0x60, 0x00, 0x5d, 0x60]))
            if b"^23" in cmd:
                switch_t0 = time.monotonic()
        t += 1
        x, y = 1000 + (t % 500) * 10, 4000
        btn = (t // 100) % 4
        mode = fmt
        if fmt == "switch":
            mode = "binary" if int((time.monotonic() - switch_t0) / 3) % 2 == 0 else "ascii"
        if mode == "binary":
            b1 = 0x80 | (btn << 2) | ((x >> 14) & 3)
            out = bytes([b1, (x >> 7) & 0x7F, x & 0x7F, (y >> 14) & 0x0F, (y >> 7) & 0x7F, y & 0x7F])
        else:
            out = f"+{x:05d},+{y:05d},{btn:02d}\r\n".encode()
            if mode == "ascii7":
                out = bytes(b | 0x80 for b in out)      # stop bit read as bit 7
        os.write(master_fd, out)
        time.sleep(0.01 if mode == "binary" else 0.03)


def run_init(fd, log, after=None):
    print("Initializing tablet (old xf86-input-calcomp sequence)...")
    if after:
        log["init"].append({"name": f"--- re-init after step {after} ---", "sent": "",
                            "reply_hex": "", "reply_packets": 0, "reply_other_hex": "",
                            "reply_other_text": ""})
    for name, cmd, wait in INIT_SEQUENCE + QUERIES + FINAL_SEQUENCE:
        reply = send(fd, cmd, wait)
        other = non_packet_bytes(reply)
        n_pk = len(split_packets(reply)[0])
        log["init"].append({"name": name, "sent": printable(cmd), "reply_hex": reply.hex(" "),
                            "reply_packets": n_pk, "reply_other_hex": other.hex(" "),
                            "reply_other_text": printable(other)})
        print(f"  {name:14} -> {len(reply):4} bytes ({n_pk} packets)  "
              f"other: {printable(other)[:50]!r}")
    termios.tcflush(fd, termios.TCIFLUSH)


def run_steps(fd, args, steps, log):
    print(f"\n{len(steps)} steps. For each one: get ready, press Enter, do the action, "
          f"press Enter when done (max {args.duration:g}s).\n"
          "At the 'ready' prompt: s = skip, q = quit and save.\n")

    i = 0
    warned_menu = False
    while i < len(steps):
        group, sid, text = steps[i]
        if group in ("menu", "config") and not warned_menu:
            warned_menu = True
            print("\n*** Menu-strip steps next. Config/Restore keys change the tablet's\n"
                  "*** settings. If the output stops or turns to junk afterwards, reset the\n"
                  "*** tablet (power-cycle it) and rerun with --steps config_after_check.\n"
                  "*** NEVER press a SAVE key: it overwrites a stored setup. Power-cycling\n"
                  "*** always returns to the Default setup as long as Save isn't used.\n")
        print(f"[{i + 1}/{len(steps)}] {sid}\n  {text}")
        if not args.non_interactive:
            a = ask("  ready? [Enter/s/q] ")
            if a == "q":
                break
            if a == "s":
                i += 1
                continue
        termios.tcflush(fd, termios.TCIFLUSH)
        data = read_for(fd, args.duration, stop_on_enter=not args.non_interactive,
                        live=not args.non_interactive)
        summary, dec, ascii_out = summarize(data)
        fmt = summary["format"]
        print(f"  format: {fmt}, {summary['packets']} packets, {summary['junk_bytes']} junk bytes"
              + (f", x {summary['x_range']}, y {summary['y_range']}, "
                 f"btn {summary['btn_field_values']}, 16btn {summary['flag16_values']}"
                 if dec else ""))
        if ascii_out:
            print(f"  *** ASCII output detected ({ascii_out['encoding']}): "
                  f"{summary['ascii_lines']} lines, {summary['ascii_parsed']} match +X,+Y,status")
            for line in ascii_out["lines"][:3]:
                print(f"      {line!r}")
        elif summary["junk_bytes"]:
            junk = non_packet_bytes(data)
            print(f"  non-packet bytes: {printable(junk)[:70]!r}")
            if "unknown" in fmt:
                print("  (unrecognized data: if a button changed the baud rate, try --baud 19200 or 38400)")
        if not args.non_interactive and ask("  keep? [Enter=yes / r=redo] ") == "r":
            continue
        note = ""
        if group in ("menu", "config") and not args.non_interactive:
            note = ask_raw("  what did the tablet do? (light, beep, nothing...; Enter to skip) ") or ""
        log["steps"].append({"group": group, "id": sid, "instruction": text, "note": note,
                             "summary": summary, "raw_hex": data.hex(" "), "packets": dec,
                             "ascii": ascii_out})
        i += 1
        if ascii_out and sid in ASCII_EXPECTED:
            print("  (ASCII expected here; config_restore_default switches back)")
        elif ascii_out and not args.non_interactive and i < len(steps):
            if ask("  Tablet is sending ASCII. Re-send init to switch back to binary? [y/N] ") == "y":
                run_init(fd, log, after=sid)
                check = summarize(read_for(fd, 1.5))[0]["format"]
                print(f"  after re-init the tablet is sending: {check}"
                      + ("" if check in ("binary", "none") else
                         "  (still not binary: power-cycle the tablet, then continue)"))


def ask_raw(prompt):
    if sys.stdin.isatty():
        termios.tcflush(sys.stdin, termios.TCIFLUSH)   # drop keys pressed while we were busy
    try:
        return input(prompt).strip()
    except EOFError:
        return None


def ask(prompt):
    a = ask_raw(prompt)
    return "q" if a is None else a.lower()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600, choices=sorted(BAUDS))
    ap.add_argument("--duration", type=float, default=20.0,
                    help="max seconds per step; Enter stops early (default 20)")
    ap.add_argument("--steps", default="",
                    help="comma-separated step ids or groups (baseline,pen,puck,switch; optional: menu,config); default: all except menu,config")
    ap.add_argument("--menu-buttons", type=int, default=18,
                    help="how many numbered keys are on the menu strip (default 18, per the manual)")
    ap.add_argument("--no-init", action="store_true", help="don't send the init/query commands")
    ap.add_argument("--non-interactive", action="store_true",
                    help="no prompts; record each step for --duration seconds")
    ap.add_argument("--list", action="store_true", help="list steps and exit")
    ap.add_argument("--simulate", action="store_true", help="use a fake tablet on a pty")
    ap.add_argument("--simulate-format", default="binary", choices=["binary", "ascii", "ascii7", "switch"],
                    help=argparse.SUPPRESS)
    ap.add_argument("--out", default=CAPTURE_DIR, help="output directory")
    args = ap.parse_args()

    # The menu strip configures the tablet itself; the driver targets the Default
    # binary setup, so menu/config steps run only when asked for by name.
    all_steps = STEPS + menu_steps(args.menu_buttons)
    steps = STEPS
    if args.steps:
        want = {s.strip() for s in args.steps.split(",") if s.strip()}
        steps = [s for s in all_steps if s[0] in want or s[1] in want]
        if not steps:
            sys.exit(f"no steps match {sorted(want)}; use --list")
    if args.list:
        for group, sid, text in steps:
            print(f"{group:8} {sid:22} {text}")
        return

    stop = threading.Event()
    if args.simulate:
        master, slave = pty.openpty()
        tty.setraw(master)
        threading.Thread(target=fake_tablet, args=(master, stop, args.simulate_format), daemon=True).start()
        port_path = os.ttyname(slave)
    else:
        port_path = args.port
    try:
        fd = open_port(port_path, args.baud)
    except OSError as e:
        sys.exit(f"can't open {port_path}: {e}\n(is the adapter plugged in? are you in the 'dialout' group?)")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = {"started": stamp, "port": args.port if not args.simulate else "simulated",
           "baud": args.baud, "simulated": args.simulate, "init": [], "steps": []}

    try:
        if not args.no_init:
            run_init(fd, log)
        run_steps(fd, args, steps, log)
    except KeyboardInterrupt:
        print("\nInterrupted; saving what was recorded so far.")

    stop.set()
    os.close(fd)

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, stamp + ("-sim" if args.simulate else ""))
    with open(base + ".json", "w") as f:
        json.dump(log, f, indent=1)
    with open(base + ".txt", "w") as f:
        f.write(f"CalComp capture {stamp}  port={log['port']} baud={args.baud}\n\n")
        for e in log["init"]:
            f.write(f"init {e['name']:14} sent {e['sent']!r:22} {e['reply_packets']:4} packets, "
                    f"other {e['reply_other_text'][:200]!r}\n")
        for st in log["steps"]:
            s = st["summary"]
            f.write(f"\n== {st['id']} ==\n{st['instruction']}\n")
            if st.get("note"):
                f.write(f"  NOTE: {st['note']}\n")
            f.write("  " + json.dumps(s) + "\n")
            if st.get("ascii"):
                f.write(f"  ASCII lines ({st['ascii']['encoding']}), first 10:\n")
                for line in st["ascii"]["lines"][:10]:
                    f.write(f"    {line!r}\n")
            # first and last few packets, enough to eyeball
            pk = st["packets"]
            if len(pk) > 16:
                pk = pk[:8] + [None] + pk[-8:]
            for d in pk:
                if d is None:
                    f.write("  ...\n")
                    continue
                f.write(f"  {d['hex']}  b1={d['b1_bits']} b4={d['b4_bits']} "
                        f"x={d['x']:6} y={d['y']:6} btn={d['btn_field']:2} 16btn={int(d['flag16'])}\n")
    print(f"\nSaved {base}.json and {base}.txt")


if __name__ == "__main__":
    main()
