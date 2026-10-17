"""Tests for calcomp_menud's config and mapping (no devices needed).

    python3 -m unittest discover -s menud/tests
"""

import os
import sys
import tomllib
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import calcomp_menud as m  # noqa: E402

HERE = os.path.dirname(__file__)
EXAMPLE = os.path.join(HERE, "..", "areas.example.toml")
L, R, MID = m.BUTTONS["left"], m.BUTTONS["right"], m.BUTTONS["middle"]


class FakeOut:
    def __init__(self):
        self.events = []

    def emit(self, t, c, v):
        self.events.append((t, c, v))

    def syn(self):
        self.events.append("syn")

    def take(self):
        e, self.events = self.events, []
        return e


class FakeActions:
    def __init__(self):
        self.ran = []

    def run(self, menu):
        self.ran.append(menu.name)


def example_dict():
    with open(EXAMPLE, "rb") as f:
        return tomllib.load(f)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def setup(cfg=None):
    cfg = cfg or m.load_config(EXAMPLE)
    out, act, clock = FakeOut(), FakeActions(), Clock()
    mp = m.Mapper(cfg, out, act, (17999, 11999), clock)
    return mp, out, act, clock


def frame(mp, x=None, y=None, **buttons):
    if x is not None:
        mp.handle(m.EV_ABS, m.ABS_X, x)
    if y is not None:
        mp.handle(m.EV_ABS, m.ABS_Y, y)
    for name, v in buttons.items():
        mp.handle(m.EV_KEY, m.BUTTONS[name], v)
    mp.handle(m.EV_SYN, m.SYN_REPORT, 0)


class ConfigTests(unittest.TestCase):
    def test_example_loads(self):
        cfg = m.load_config(EXAMPLE)
        self.assertEqual(cfg.drawing, m.Rect(2200, 1000, 15800, 6700))
        self.assertGreaterEqual(len(cfg.menus), 2)
        self.assertTrue(any(x.keys for x in cfg.menus))
        self.assertTrue(any(x.command for x in cfg.menus))
        self.assertEqual(m.config_warnings(cfg), [])
        for x in cfg.menus:   # all in the bottom region, clear of the strips
            self.assertGreaterEqual(x.rect.y0, 8500)

    def test_combo(self):
        self.assertEqual(m.parse_combo("ctrl+shift+z"), [29, 42, 44])
        self.assertEqual(m.parse_combo("Super + F5"), [125, 63])
        with self.assertRaises(m.ConfigError):
            m.parse_combo("ctrl+nosuchkey")

    def base(self):
        return example_dict()

    def test_errors(self):
        d = self.base()
        d["menu"][0]["command"] = "x"           # keys and command
        with self.assertRaisesRegex(m.ConfigError, "not both"):
            m.build_config(d)
        d = self.base()
        del d["menu"][0]["keys"]                # no action: a placeholder
        self.assertEqual(m.build_config(d).menus[0].describe(), "no action")
        d = self.base()
        d["menu"][1].update(x0=1000, y0=9000)   # overlaps menu 0
        with self.assertRaisesRegex(m.ConfigError, "overlap"):
            m.build_config(d)
        d = self.base()
        d["menu"][0].update(y0=6000)            # into the drawing area
        with self.assertRaisesRegex(m.ConfigError, "drawing"):
            m.build_config(d)
        d = self.base()
        d["menu"][0]["buttons"] = ["thumb"]
        with self.assertRaisesRegex(m.ConfigError, "unknown button"):
            m.build_config(d)
        d = self.base()
        d["menu"][0]["y1"] = 12500
        with self.assertRaisesRegex(m.ConfigError, "outside"):
            m.build_config(d)

    def test_hidden(self):
        d = self.base()
        d["menu"][1]["visible"] = False
        d["menu"][1].update(x0=1000, y0=9000)   # overlaps menu 0: fine when hidden
        cfg = m.build_config(d)
        self.assertNotIn("redo", [x.name for x in cfg.menus])
        self.assertEqual([x.name for x in cfg.hidden], ["redo"])
        d["menu"][1]["visible"] = "no"
        with self.assertRaisesRegex(m.ConfigError, "true or false"):
            m.build_config(d)

    def test_faulty_warning(self):
        d = self.base()
        d["menu"][0].update(y0=8000, y1=8600)
        self.assertTrue(m.config_warnings(m.build_config(d)))


class MapperTests(unittest.TestCase):
    def test_drawing_area_scaling(self):
        mp, out, _, _ = setup()
        frame(mp, 2200, 1000)
        self.assertEqual(out.take(), [(m.EV_ABS, m.ABS_X, 0), (m.EV_ABS, m.ABS_Y, 0), "syn"])
        frame(mp, 15799, 6699)
        self.assertEqual(out.take(), [(m.EV_ABS, m.ABS_X, 17999),
                                      (m.EV_ABS, m.ABS_Y, 11999), "syn"])
        frame(mp, 9000)                          # only x changes
        self.assertEqual(out.take(), [(m.EV_ABS, m.ABS_X, round(6800 * 17999 / 13599)), "syn"])

    def test_buttons_pass_in_drawing(self):
        mp, out, act, _ = setup()
        frame(mp, 5000, 3000, left=1)
        ev = out.take()
        self.assertIn((m.EV_KEY, L, 1), ev)
        self.assertLess(ev.index((m.EV_ABS, m.ABS_X, mp.last_out[0])), ev.index((m.EV_KEY, L, 1)))
        frame(mp, left=0)
        self.assertEqual(out.take(), [(m.EV_KEY, L, 0), "syn"])
        self.assertEqual(act.ran, [])

    def test_menu_fires_and_pointer_stays(self):
        mp, out, act, clock = setup()
        frame(mp, 5000, 3000)
        out.take()
        frame(mp, 1500, 9500)                    # into "undo": no motion
        self.assertEqual(out.take(), [])
        frame(mp, 1510, 9510, left=1)
        self.assertEqual(act.ran, ["undo"])
        frame(mp, left=0)
        self.assertEqual(out.take(), [])         # press and release swallowed
        clock.t += 0.1
        frame(mp, right=1)                       # bounce: within debounce
        frame(mp, right=0)
        self.assertEqual(act.ran, ["undo"])
        clock.t += 0.3
        frame(mp, 14600, 9100, middle=1)         # "terminal"
        frame(mp, middle=0)
        self.assertEqual(act.ran, ["undo", "terminal"])
        self.assertEqual(out.take(), [])

    def test_dead_space_drops_everything(self):
        mp, out, act, _ = setup()
        frame(mp, 5000, 3000)
        out.take()
        for y in (700, 7500, 11800):             # faulty strips, unused space
            frame(mp, 9000, y, left=1)
            frame(mp, left=0)
        self.assertEqual(out.take(), [])
        self.assertEqual(act.ran, [])

    def test_press_outside_then_move_in_stays_swallowed(self):
        mp, out, _, _ = setup()
        frame(mp, 9000, 11000, left=1)           # pressed in dead space
        frame(mp, 5000, 3000)                    # dragged into the drawing area
        self.assertNotIn((m.EV_KEY, L, 1), out.take())
        frame(mp, left=0)
        self.assertEqual(out.take(), [])         # no stray release

    def test_drag_out_of_drawing_clamps(self):
        mp, out, act, _ = setup()
        frame(mp, 5000, 3000, left=1)
        out.take()
        frame(mp, 1500, 9500)                    # drag down into "undo"
        self.assertEqual(out.take(), [(m.EV_ABS, m.ABS_X, 0),
                                      (m.EV_ABS, m.ABS_Y, 11999), "syn"])
        frame(mp, right=1)                       # extra press during drag passes
        self.assertEqual(out.take(), [(m.EV_KEY, R, 1), "syn"])
        frame(mp, left=0, right=0)
        self.assertEqual(out.take(), [(m.EV_KEY, L, 0), (m.EV_KEY, R, 0), "syn"])
        self.assertEqual(act.ran, [])
        frame(mp, 1510, 9510)                    # now motion in the menu stops
        self.assertEqual(out.take(), [])

    def test_hidden_is_dead_space(self):
        d = example_dict()
        d["menu"][0]["visible"] = False             # "undo"
        mp, out, act, _ = setup(m.build_config(d))
        frame(mp, 1500, 9500, left=1)
        frame(mp, left=0)
        self.assertEqual((act.ran, out.take()), ([], []))
        doc = m.svg_doc(mp.cfg, (0, 0, 18000, 12000), (18, 12))
        self.assertNotIn(">Undo<", doc)

    def test_buttons_filter(self):
        d = example_dict()
        d["menu"][0]["buttons"] = ["left"]
        mp, _, act, clock = setup(m.build_config(d))
        frame(mp, 1500, 9500, right=1)
        frame(mp, right=0)
        self.assertEqual(act.ran, [])
        frame(mp, left=1)
        self.assertEqual(act.ran, ["undo"])

    def test_syn_dropped_resync(self):
        mp, out, _, _ = setup()
        frame(mp, 5000, 3000, left=1)
        out.take()
        mp.resync_source = lambda: (6000, 3000, set())   # left was released
        mp.handle(m.EV_SYN, m.SYN_DROPPED, 0)
        mp.handle(m.EV_ABS, m.ABS_X, 1)                 # junk, ignored
        mp.handle(m.EV_SYN, m.SYN_REPORT, 0)
        ev = out.take()
        self.assertIn((m.EV_KEY, L, 0), ev)
        self.assertIn((m.EV_ABS, m.ABS_X, round(3800 * 17999 / 13599)), ev)
        self.assertFalse(mp.down)

    def test_release_all(self):
        mp, out, _, _ = setup()
        frame(mp, 5000, 3000, left=1, middle=1)
        out.take()
        mp.release_all()
        ev = out.take()
        self.assertIn((m.EV_KEY, L, 0), ev)
        self.assertIn((m.EV_KEY, MID, 0), ev)

    def test_reload_resends_position(self):
        mp, out, _, _ = setup()
        frame(mp, 5000, 3000)
        out.take()
        d = example_dict()
        d["drawing"] = {"x0": 3000, "y0": 1000, "x1": 15000, "y1": 6000}
        mp.set_config(m.build_config(d))
        frame(mp)
        self.assertEqual(out.take()[:2], [(m.EV_ABS, m.ABS_X, round(2000 * 17999 / 11999)),
                                          (m.EV_ABS, m.ABS_Y, round(2000 * 11999 / 4999))])


class OverlayTests(unittest.TestCase):
    def test_marks_avoid_buttons_and_strips(self):
        import re
        cfg = m.load_config(EXAMPLE)
        frame = m.Rect(0, 8000, 10000, 12000)
        out = m.sheet_marks(cfg, frame)
        centres = [tuple(map(int, c)) for svg in out
                   for c in re.findall(r"<circle cx='(\d+)' cy='(\d+)'", svg)]
        self.assertEqual(len(centres), 4)
        for x, y in centres:
            self.assertTrue(frame.contains(x, y))
            for r in [x.rect for x in cfg.menus] + cfg.faulty:
                self.assertFalse(m.Rect(x - 150, y - 150, x + 150, y + 150).overlaps(r))

    def test_tiles_never_cut_a_button(self):
        # 22 buttons of 625 with 125 gaps from x 812, pages 10000 wide
        spans = [(812 + i * 750, 1437 + i * 750) for i in range(22)]
        starts = m.tile_starts(12, 17987, 10000, spans, "wide")
        self.assertEqual(len(starts), 2)
        for a, b in spans:
            self.assertTrue(any(s <= a and b <= s + 10000 for s in starts), (a, b))
        with self.assertRaisesRegex(m.ConfigError, "too wide"):
            m.tile_starts(0, 18000, 1000, [(500, 1700)], "wide")


    def test_image(self):
        import tempfile
        import xml.dom.minidom
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "dot.svg"), "w") as f:
                f.write("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'>"
                        "<circle cx='.5' cy='.5' r='.4'/></svg>")
            d = example_dict()
            for x in d["menu"]:
                x.pop("image", None)
            d["menu"][0]["image"] = "dot.svg"           # relative to the config
            d["menu"][0].pop("label", None)
            d["menu"][1]["image"] = "missing.png"
            cfg = m.build_config(d, os.path.join(tmp, "areas.toml"))
            self.assertEqual(cfg.menus[0].image, os.path.join(tmp, "dot.svg"))
            self.assertEqual(cfg.menus[0].label, "")    # no label unless given
            self.assertTrue(any("missing.png" in w for w in m.config_warnings(cfg)))
            cfg.menus[1].image = ""
            doc = m.svg_doc(cfg, (0, 0, 18000, 12000), (18, 12))
            root = xml.dom.minidom.parseString(doc).documentElement
            imgs = root.getElementsByTagName("image")
            self.assertEqual(len(imgs), 1)
            self.assertTrue(imgs[0].getAttribute("xlink:href").startswith(
                "data:image/svg+xml;base64,"))
            d["menu"][0]["image"] = "dot.gif"
            with self.assertRaisesRegex(m.ConfigError, "image must be"):
                m.build_config(d, os.path.join(tmp, "areas.toml"))

    def test_svg_parses(self):
        import xml.dom.minidom
        cfg = m.load_config(EXAMPLE)
        doc = m.svg_doc(cfg, (0, 0, 18000, 12000), (18, 12))
        root = xml.dom.minidom.parseString(doc).documentElement
        self.assertEqual(root.getAttribute("width"), "18in")
        texts = [t.firstChild.data for t in root.getElementsByTagName("text") if t.firstChild]
        self.assertIn("Undo", texts)


if __name__ == "__main__":
    unittest.main()
