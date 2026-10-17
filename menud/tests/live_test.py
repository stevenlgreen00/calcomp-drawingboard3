#!/usr/bin/env python3
"""End-to-end test of calcomp-menud with a fake tablet, read back with evtest.

Needs the udev rule (71-calcomp-menud.rules) installed: it makes the
"CalComp DrawingBoard III test ..." devices readable and hides them from
libinput, so nothing here reaches the desktop. The real tablet is untouched.

    python3 menud/tests/live_test.py
"""

import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import calcomp_menud as m  # noqa: E402

DAEMON = os.path.join(HERE, "..", "calcomp_menud.py")


def wait_for(pred, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    sys.exit(f"FAIL: timed out waiting for {what}")


def main():
    tmp = tempfile.mkdtemp(prefix="calcomp-live-")
    fired = os.path.join(tmp, "fired")
    cfg = os.path.join(tmp, "areas.toml")
    with open(cfg, "w") as f:
        f.write(f"""
[drawing]
x0 = 2200
y0 = 1000
x1 = 15800
y1 = 6700

[[menu]]
name = "keytest"
keys = "shift+f13"
x0 = 1000
y0 = 9000
x1 = 2250
y1 = 10250

[[menu]]
name = "cmdtest"
command = "echo fired > {fired}"
x0 = 14500
y0 = 9000
x1 = 15750
y1 = 10250
""")

    src = m.UInput("CalComp DrawingBoard III test source", m.BUTTON_NAMES,
                   {m.ABS_X: (0, 17999, 39), m.ABS_Y: (0, 11999, 39)}, product=0xcf)
    src_node = src.devnode()
    wait_for(lambda: os.access(src_node, os.R_OK), what=f"read access to {src_node} "
             "(is 71-calcomp-menud.rules installed?)")
    print(f"fake tablet: {src_node}")

    log = open(os.path.join(tmp, "daemon.log"), "w+")
    d = subprocess.Popen([sys.executable, DAEMON, "-c", cfg, "run", "--device", src_node,
                          "--test-names"], stderr=log)
    procs = []
    try:
        def devices():
            log.seek(0)
            mo = re.search(r"virtual devices: (\S+) .*?, (\S+) ", log.read())
            return mo and mo.groups()
        mouse_node, kbd_node = wait_for(devices, what="the daemon's devices")
        wait_for(lambda: os.access(mouse_node, os.R_OK) and os.access(kbd_node, os.R_OK),
                 what="read access to the virtual devices")
        wait_for(lambda: "grabbed" in open(log.name).read(), what="the grab")
        print(f"mapped: {mouse_node}, keys: {kbd_node}")

        outs = {}
        for name, node in (("mouse", mouse_node), ("kbd", kbd_node)):
            path = os.path.join(tmp, f"evtest-{name}.log")
            procs.append(subprocess.Popen(["stdbuf", "-oL", "evtest", node], stdout=open(path, "w"),
                                          stderr=subprocess.STDOUT))
            outs[name] = path
        wait_for(lambda: all("Testing ..." in open(p).read() for p in outs.values()),
                 what="evtest")

        # the grab must keep other readers out
        try:
            m.Source(src_node, grab=True).close()
            sys.exit("FAIL: source not grabbed")
        except OSError as e:
            assert e.errno == 16, e

        def move(x, y, **btn):
            src.emit(m.EV_ABS, m.ABS_X, x)
            src.emit(m.EV_ABS, m.ABS_Y, y)
            for b, v in btn.items():
                src.emit(m.EV_KEY, m.BUTTONS[b], v)
            src.syn()
            time.sleep(0.05)

        move(15799, 6699)                   # drawing bottom-right -> max
        move(2200, 1000)                    # top-left -> 0,0 (the device starts
                                            # at 0,0, so this must come second)
        move(9000, 3850, left=1)            # click in the middle
        move(9000, 3850, left=0)
        move(1500, 9500)                    # keytest: pointer must not move
        move(1600, 9600, left=1)
        move(1600, 9600, left=0)
        move(14600, 9100, right=1)          # cmdtest, with the right button
        move(14600, 9100, right=0)
        move(9000, 11500, left=1)           # dead space: nothing
        move(9000, 11500, left=0)
        move(9000, 700, middle=1)           # faulty strip: nothing
        move(9000, 700, middle=0)
        move(9000, 3850)                    # back in the drawing area
        wait_for(lambda: os.path.exists(fired), what="the command")
        time.sleep(0.3)
    finally:
        for p in procs:
            p.terminate()
        d.terminate()
        d.wait(5)
        src.close()

    mouse = open(outs["mouse"]).read()
    kbd = open(outs["kbd"]).read()
    ev = re.findall(r"type \d+ \((EV_\w+)\), code \d+ \((\w+)\), value (-?\d+)", mouse.split("Testing ...")[1])
    kev = re.findall(r"code \d+ \((KEY_\w+)\), value (\d)", kbd.split("Testing ...")[1])
    print("mapped device events:", ev)
    print("keyboard events:", kev)
    expect = [("EV_ABS", "ABS_X", "17999"), ("EV_ABS", "ABS_Y", "11999"),
              ("EV_ABS", "ABS_X", "0"), ("EV_ABS", "ABS_Y", "0"),
              ("EV_ABS", "ABS_X", str(round(6800 * 17999 / 13599))),
              ("EV_ABS", "ABS_Y", str(round(2850 * 11999 / 5699))),
              ("EV_KEY", "BTN_LEFT", "1"), ("EV_KEY", "BTN_LEFT", "0")]
    got = [e for e in ev if e[0] != "EV_SYN"]
    ok = True
    if got != expect:
        print(f"FAIL: mapped device\n  expected {expect}\n  got      {got}")
        ok = False
    if kev != [("KEY_LEFTSHIFT", "1"), ("KEY_F13", "1"), ("KEY_F13", "0"),
               ("KEY_LEFTSHIFT", "0")]:
        print("FAIL: keyboard")
        ok = False
    if open(fired).read().strip() != "fired":
        print("FAIL: command")
        ok = False
    print(open(log.name).read())
    print("PASS" if ok else "FAILED", f"(logs in {tmp})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
