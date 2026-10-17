#!/usr/bin/env python3
"""Turn the real tablet captures into test vectors for test_proto.c.

The expected values come from what each capture step asked the user to do
(e.g. "press puck button A"), not from the decoder under test. X/Y come from
capture.py's independent Python decode.

Output, one packet per line:
    <12 hex digits> <tool> <buttons> <x> <y> <x_lo> <x_hi> <y_lo> <y_hi> <step>
x_lo..y_hi: where the packet must lie (the whole tablet, or a corner for corner steps)
tool:    0 unknown (no button down), 1 pen, 2 puck
buttons: pen: allowed bitmasks joined by '|'; puck: button number; '-' none
"""

import json
import os
import sys

# (file, steps to skip) - see NOTES.md: 200836's puck_btn_8 was a mis-press, redone in 201619
CAPTURES = [
    ("20261016-200836.json", {"puck_btn_8"}),
    ("20261016-201619.json", set()),
    ("20261016-204600.json", set()),
]

PEN_MASKS = {
    "pen_tip": "1", "pen_barrel_lower": "2", "pen_barrel_upper": "4",
    "pen_tip_plus_lower": "1|2|3", "pen_tip_plus_upper": "1|4|5",
}

# (x_lo, x_hi, y_lo, y_hi) in raw tablet coordinates (origin bottom-left)
CORNERS = {
    "pen_corner_bl": (0, 1000, 0, 1000),
    "pen_corner_br": (17000, 17999, 0, 1000),
    "pen_corner_tr": (17000, 17999, 11000, 11999),
    "pen_corner_tl": (0, 17999, 11000, 11999),   # user moved along the top edge
}


def main():
    cap_dir = sys.argv[1]
    n = 0
    for fname, skip in CAPTURES:
        with open(os.path.join(cap_dir, fname)) as f:
            log = json.load(f)
        for step in log["steps"]:
            sid = step["id"]
            if sid in skip:
                continue
            for p in step["packets"]:
                raw = bytes.fromhex(p["hex"])
                pressed = bool(raw[0] & 0x7c)       # bits 1..0 are X15..X14, not buttons
                if not pressed:
                    tool, btn = 0, "-"
                elif sid.startswith("puck_btn_"):
                    tool, btn = 2, str(int(sid[-1], 16))
                elif sid == "puck_two_buttons":
                    tool, btn = 2, "5"      # 4+5 held together reports only 5
                elif sid in PEN_MASKS:
                    tool, btn = 1, PEN_MASKS[sid]
                else:
                    sys.exit(f"{fname} {sid}: unexpected button packet {p['hex']}")
                box = CORNERS.get(sid, (0, 17999, 0, 11999))
                print(f"{raw.hex()} {tool} {btn} {p['x']} {p['y']} "
                      f"{box[0]} {box[1]} {box[2]} {box[3]} {sid}")
                n += 1
    print(f"# {n} packets", file=sys.stderr)


if __name__ == "__main__":
    main()
