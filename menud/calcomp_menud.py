#!/usr/bin/env python3
"""Menu/macro areas for the CalComp DrawingBoard III (calcomp_drv).

Grabs the driver's absolute-mouse device and re-creates it as a virtual one:
- inside the drawing area, motion and buttons pass through, rescaled so the
  drawing area covers the whole screen;
- inside a menu rectangle the pointer stays put, and a button press runs the
  menu's action (a key combo on a virtual keyboard, or a shell command);
- anywhere else motion and buttons are dropped.

Coordinates are tablet counts (1000 per inch, origin top-left), as the driver
reports them with area_x0/y0/x1/y1 = 0. See README.md.

Subcommands:
  run       the daemon (default)
  measure   print the tablet coordinates under the tool
  overlay   draw the menu layout as a 1:1 SVG (or PDF) to print
  check     validate the config and print the layout

Standard library only (Python 3.11+): evdev and uinput are driven with ioctls.
"""

import argparse
import base64
import errno
import fcntl
import os
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from keycodes import KEYS  # noqa: E402

DEVICE_NAME = "CalComp DrawingBoard III"
OUT_NAME = "CalComp DrawingBoard III (mapped)"
KBD_NAME = "CalComp DrawingBoard III (menu keys)"
PARAM_DIR = "/sys/module/calcomp_drv/parameters"
AREA_PARAMS = ("area_x0", "area_y0", "area_x1", "area_y1")
DEFAULT_CONFIG = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "calcomp", "areas.toml")

# --- evdev / uinput constants ----------------------------------------------

EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0x00, 0x01, 0x03, 0x04
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_X, ABS_Y = 0x00, 0x01
BUS_VIRTUAL = 0x06

EVENT = struct.Struct("llHHi")          # struct input_event (64-bit time_t)
ABSINFO = struct.Struct("6i")           # value min max fuzz flat resolution


def _ioc(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


_IOW, _IOR = 1, 2
EVIOCGNAME = lambda n: _ioc(_IOR, "E", 0x06, n)          # noqa: E731
EVIOCGKEY = lambda n: _ioc(_IOR, "E", 0x18, n)           # noqa: E731
EVIOCGBIT = lambda ev, n: _ioc(_IOR, "E", 0x20 + ev, n)  # noqa: E731
EVIOCGABS = lambda a: _ioc(_IOR, "E", 0x40 + a, ABSINFO.size)  # noqa: E731
EVIOCGRAB = _ioc(_IOW, "E", 0x90, 4)
UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)
UI_DEV_SETUP = _ioc(_IOW, "U", 3, 92)    # struct uinput_setup
UI_ABS_SETUP = _ioc(_IOW, "U", 4, 28)    # struct uinput_abs_setup
UI_SET_EVBIT = _ioc(_IOW, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_IOW, "U", 101, 4)
UI_SET_ABSBIT = _ioc(_IOW, "U", 103, 4)
UI_GET_SYSNAME = lambda n: _ioc(_IOR, "U", 44, n)        # noqa: E731

# The driver's mouse-mode buttons. No BTN_TOOL_*, BTN_TOUCH or BTN_STYLUS*:
# with those, udev and libinput would treat the virtual device as a tablet.
BUTTONS = {
    "left": 0x110, "right": 0x111, "middle": 0x112, "side": 0x113,
    "extra": 0x114, "forward": 0x115, "back": 0x116, "task": 0x117,
    **{f"btn{i}": 0x100 + i for i in range(8)},
}
BUTTON_NAMES = {v: k for k, v in BUTTONS.items()}

SYSTEM_KEYS = ("power", "power2", "sleep", "wakeup", "suspend", "rfkill", "wlan",
               "bluetooth", "wwan", "uwb", "wimax", "sysrq")

KEY_ALIASES = {
    "ctrl": "leftctrl", "control": "leftctrl", "shift": "leftshift",
    "alt": "leftalt", "altgr": "rightalt", "super": "leftmeta",
    "meta": "leftmeta", "win": "leftmeta", "logo": "leftmeta",
    "esc": "esc", "escape": "esc", "return": "enter", "del": "delete",
    "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
    "bksp": "backspace", "dot": "dot", "period": "dot", "plus": "kpplus",
    "-": "minus", "=": "equal", ",": "comma", ".": "dot", "/": "slash",
    ";": "semicolon", "'": "apostrophe", "[": "leftbrace", "]": "rightbrace",
    "\\": "backslash", "`": "grave", "print": "sysrq", "printscreen": "sysrq",
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- config ------------------------------------------------------------------

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".svg": "image/svg+xml", ".svgz": "image/svg+xml"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Rect:
    x0: int
    y0: int
    x1: int     # exclusive
    y1: int     # exclusive

    def contains(self, x, y):
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1

    def overlaps(self, o):
        return (self.x0 < o.x1 and o.x0 < self.x1 and
                self.y0 < o.y1 and o.y0 < self.y1)

    def __str__(self):
        return f"x {self.x0}-{self.x1}, y {self.y0}-{self.y1}"


@dataclass
class Menu:
    name: str
    rect: Rect
    keys: list = field(default_factory=list)    # list of combos (lists of codes)
    keys_text: str = ""
    command: str = ""
    buttons: frozenset = None                   # None = any button
    label: str = ""
    image: str = ""                             # overlay picture (absolute path)

    def describe(self):
        if self.keys:
            return f"keys {self.keys_text}"
        if self.command:
            return f"command {self.command!r}"
        return "no action"


@dataclass
class Config:
    device: str
    drawing: Rect
    menus: list
    faulty: list
    hidden: list = field(default_factory=list)  # menus with visible = false
    tablet_w: int = 18000
    tablet_h: int = 12000
    debounce_ms: int = 250
    path: str = ""


def parse_combo(text):
    codes = []
    for part in text.lower().replace(" ", "").split("+"):
        if not part:
            raise ConfigError(f"empty key name in {text!r} (the + key is 'plus')")
        name = KEY_ALIASES.get(part, part)
        if name not in KEYS:
            raise ConfigError(f"unknown key {part!r} in {text!r}")
        codes.append(KEYS[name])
    return codes


def _rect(tbl, where):
    try:
        r = Rect(*(int(tbl[k]) for k in ("x0", "y0", "x1", "y1")))
    except KeyError as e:
        raise ConfigError(f"{where}: missing {e.args[0]}") from None
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: x0/y0/x1/y1 must be integers") from None
    if r.x1 <= r.x0 or r.y1 <= r.y0:
        raise ConfigError(f"{where}: needs x0 < x1 and y0 < y1 ({r})")
    return r


def load_config(path):
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return build_config(data, path)


def build_config(data, path=""):
    tab = data.get("tablet", {})
    cfg = Config(
        device=str(tab.get("device", DEVICE_NAME)),
        drawing=_rect(data.get("drawing") or {}, "[drawing]"),
        menus=[], faulty=[],
        tablet_w=int(tab.get("width", 18000)),
        tablet_h=int(tab.get("height", 12000)),
        debounce_ms=int(data.get("debounce_ms", 250)),
        path=path)
    for i, f in enumerate(data.get("faulty", [])):
        cfg.faulty.append(_rect(f, f"[[faulty]] #{i + 1}"))
    for i, m in enumerate(data.get("menu", [])):
        name = str(m.get("name") or f"menu{i + 1}")
        where = f"[[menu]] {name!r}"
        image = os.path.expanduser(str(m.get("image", "")))
        if image and not os.path.isabs(image):
            image = os.path.join(os.path.dirname(os.path.abspath(path or ".")) if path
                                 else os.getcwd(), image)
        if image and os.path.splitext(image)[1].lower() not in IMAGE_TYPES:
            raise ConfigError(f"{where}: image must be one of "
                              f"{', '.join(sorted(IMAGE_TYPES))}: {image}")
        # with an image, the label is only printed if given
        menu = Menu(name=name, rect=_rect(m, where), image=image,
                    label=str(m.get("label", "" if image else name)))
        keys, cmd = m.get("keys"), m.get("command")
        if keys is not None and cmd is not None:
            raise ConfigError(f"{where}: give keys or command, not both")
        if keys is not None:
            seq = [keys] if isinstance(keys, str) else list(keys)
            try:
                menu.keys = [parse_combo(str(k)) for k in seq]
            except ConfigError as e:
                raise ConfigError(f"{where}: {e}") from None
            menu.keys_text = ", ".join(seq)
        elif cmd is not None:
            menu.command = str(cmd)
        if "buttons" in m:
            bad = [b for b in m["buttons"] if b not in BUTTONS]
            if bad:
                raise ConfigError(f"{where}: unknown button(s) {bad}; "
                                  f"use {', '.join(BUTTONS)}")
            menu.buttons = frozenset(BUTTONS[b] for b in m["buttons"])
        visible = m.get("visible", True)
        if not isinstance(visible, bool):
            raise ConfigError(f"{where}: visible must be true or false")
        # hidden: not printed and not active (its area is dead space), and
        # not checked for overlaps, so it can be parked anywhere
        (cfg.menus if visible else cfg.hidden).append(menu)

    whole = Rect(0, 0, cfg.tablet_w, cfg.tablet_h)
    for name, r in [("[drawing]", cfg.drawing)] + [(repr(m.name), m.rect) for m in cfg.menus]:
        if not (whole.contains(r.x0, r.y0) and r.x1 <= cfg.tablet_w and r.y1 <= cfg.tablet_h):
            raise ConfigError(f"{name} ({r}) is outside the tablet "
                              f"(0-{cfg.tablet_w} x 0-{cfg.tablet_h})")
    for i, m in enumerate(cfg.menus):
        if m.rect.overlaps(cfg.drawing):
            raise ConfigError(f"menu {m.name!r} overlaps the drawing area")
        for o in cfg.menus[:i]:
            if m.rect.overlaps(o.rect):
                raise ConfigError(f"menus {o.name!r} and {m.name!r} overlap")
    return cfg


def config_warnings(cfg):
    out = []
    for m in cfg.menus:
        if m.image and not os.path.isfile(m.image):
            out.append(f"menu {m.name!r}: image not found: {m.image}")
    for f in cfg.faulty:
        if f.overlaps(cfg.drawing):
            out.append(f"the drawing area overlaps faulty strip {f}")
        for m in cfg.menus:
            if m.rect.overlaps(f):
                out.append(f"menu {m.name!r} overlaps faulty strip {f}")
    return out


# --- devices -------------------------------------------------------------------

def find_device(name):
    """/dev/input/eventN of the input device called `name`, or None."""
    try:
        text = open("/proc/bus/input/devices").read()
    except OSError:
        return None
    for block in text.split("\n\n"):
        if f'N: Name="{name}"' not in block:
            continue
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                for h in line.split("=", 1)[1].split():
                    if h.startswith("event"):
                        return f"/dev/input/{h}"
    return None


def driver_area():
    """The driver's live area_* parameters, or None if it isn't loaded."""
    try:
        return {p: int(open(os.path.join(PARAM_DIR, p)).read()) for p in AREA_PARAMS}
    except (OSError, ValueError):
        return None


def driver_warnings():
    out = []
    area = driver_area()
    if area and any(area.values()):
        out.append("calcomp_drv maps an active area (" +
                   ", ".join(f"{k}={v}" for k, v in area.items()) +
                   "): menus outside it can't be reached and coordinates are "
                   "rescaled. Set all four to 0 (see README).")
    try:
        if open(os.path.join(PARAM_DIR, "tablet_mode")).read().strip() == "Y":
            out.append("calcomp_drv is in tablet_mode; this daemon expects the "
                       "default absolute-mouse mode")
    except OSError:
        pass
    return out


class Source:
    """The driver's evdev device."""

    def __init__(self, path, grab=True):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.grabbed = False
        try:
            buf = bytearray(256)
            n = fcntl.ioctl(self.fd, EVIOCGNAME(len(buf)), buf)
            self.name = bytes(buf[:max(n - 1, 0)]).decode(errors="replace")
            self.absinfo = {a: self.get_abs(a) for a in (ABS_X, ABS_Y)}
            if grab:
                fcntl.ioctl(self.fd, EVIOCGRAB, 1)
                self.grabbed = True
        except OSError:
            os.close(self.fd)
            raise
        self._buf = b""

    def get_abs(self, axis):
        buf = bytearray(ABSINFO.size)
        fcntl.ioctl(self.fd, EVIOCGABS(axis), buf)
        return ABSINFO.unpack(buf)

    def pressed(self):
        """Codes of the buttons currently down (after SYN_DROPPED)."""
        buf = bytearray(0x300 // 8)
        fcntl.ioctl(self.fd, EVIOCGKEY(len(buf)), buf)
        return {c for c in BUTTON_NAMES if buf[c // 8] >> (c % 8) & 1}

    def read(self):
        """Yields (type, code, value); raises OSError(ENODEV) when it's gone."""
        try:
            data = os.read(self.fd, EVENT.size * 64)
        except BlockingIOError:
            return
        if not data:
            raise OSError(errno.ENODEV, "device closed")
        data = self._buf + data
        n = len(data) - len(data) % EVENT.size
        self._buf = data[n:]
        for off in range(0, n, EVENT.size):
            _, _, t, c, v = EVENT.unpack_from(data, off)
            yield t, c, v

    def close(self):
        if self.fd is not None:
            if self.grabbed:
                try:
                    fcntl.ioctl(self.fd, EVIOCGRAB, 0)
                except OSError:
                    pass
            os.close(self.fd)
            self.fd = None


class UInput:
    """A uinput device. `absaxes` = {code: (min, max, resolution)}."""

    def __init__(self, name, keys, absaxes=None, product=0xc1):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for k in sorted(keys):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
            for code, (lo, hi, res) in (absaxes or {}).items():
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                fcntl.ioctl(self.fd, UI_ABS_SETUP,
                            struct.pack("Hxx6i", code, 0, lo, hi, 0, 0, res))
            fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack(
                "HHHH80sI", BUS_VIRTUAL, 0, product, 1, name.encode()[:79], 0))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        self.name = name

    def sysname(self):
        buf = bytearray(64)
        fcntl.ioctl(self.fd, UI_GET_SYSNAME(len(buf)), buf)
        return bytes(buf).split(b"\0", 1)[0].decode()

    def devnode(self):
        d = f"/sys/devices/virtual/input/{self.sysname()}"
        for _ in range(50):
            try:
                ev = [e for e in os.listdir(d) if e.startswith("event")]
            except OSError:
                ev = []
            if ev:
                return f"/dev/input/{ev[0]}"
            time.sleep(0.02)
        return None

    def emit(self, t, c, v):
        os.write(self.fd, EVENT.pack(0, 0, t, c, v))

    def syn(self):
        self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None


# --- the mapping ------------------------------------------------------------

class Actions:
    """Runs menu actions: key combos on `keyboard`, commands in a shell."""

    def __init__(self, keyboard, key_gap=0.008):
        self.keyboard = keyboard
        self.key_gap = key_gap
        self.children = []

    def run(self, menu):
        log(f"menu {menu.name!r}: {menu.describe()}")
        if menu.keys:
            self.keys(menu.keys)
        elif menu.command:
            self.command(menu.command)

    def keys(self, combos):
        kb = self.keyboard
        for combo in combos:
            for c in combo:
                kb.emit(EV_KEY, c, 1)
                kb.syn()
            time.sleep(self.key_gap)
            for c in reversed(combo):
                kb.emit(EV_KEY, c, 0)
                kb.syn()
            time.sleep(self.key_gap)

    def command(self, cmd):
        try:
            self.children.append(subprocess.Popen(
                cmd, shell=True, stdin=subprocess.DEVNULL, start_new_session=True))
        except OSError as e:
            log(f"  failed: {e}")

    def reap(self):
        for p in self.children[:]:
            rc = p.poll()
            if rc is not None:
                self.children.remove(p)
                if rc:
                    log(f"  command {p.args!r} exited with {rc}")


class Mapper:
    """Turns the driver's events into pointer events and menu actions.

    Feed it source events with handle(); it writes to `mouse` (an object with
    emit/syn, the output range being 0..out_max) and calls actions.run(menu).
    """

    def __init__(self, cfg, mouse, actions, out_max=(17999, 11999), clock=time.monotonic):
        self.cfg = cfg
        self.mouse = mouse
        self.actions = actions
        self.out_max = out_max
        self.clock = clock
        self.x = self.y = None
        self.changes = []           # button (code, value) in this frame
        self.down = set()           # buttons we have pressed on the output
        self.swallowed = set()      # source buttons held but not passed on
        self.last_out = None
        self.last_fire = {}
        self.dropped = False
        self.region = None

    def set_config(self, cfg):
        self.cfg = cfg
        self.last_fire.clear()
        self.last_out = None        # re-send the position in the new mapping

    def locate(self, x, y):
        """'drawing', a Menu, or None."""
        if x is None or y is None:
            return None
        if self.cfg.drawing.contains(x, y):
            return "drawing"
        for m in self.cfg.menus:
            if m.rect.contains(x, y):
                return m
        return None

    def scaled(self):
        d = self.cfg.drawing
        out = []
        for v, lo, hi, mx in ((self.x, d.x0, d.x1 - 1, self.out_max[0]),
                              (self.y, d.y0, d.y1 - 1, self.out_max[1])):
            v = min(max(v, lo), hi)
            out.append(round((v - lo) * mx / max(hi - lo, 1)))
        return tuple(out)

    def handle(self, t, c, v):
        if self.dropped:            # skip to the end of the broken frame
            if t == EV_SYN and c == SYN_REPORT:
                self.dropped = False
                self.resync()
            return
        if t == EV_ABS:
            if c == ABS_X:
                self.x = v
            elif c == ABS_Y:
                self.y = v
        elif t == EV_KEY and c in BUTTON_NAMES and v in (0, 1):
            self.changes.append((c, v))
        elif t == EV_SYN and c == SYN_REPORT:
            self.frame()
        elif t == EV_SYN and c == SYN_DROPPED:
            self.dropped = True
            self.changes.clear()

    resync_source = None            # set to a callable -> (x, y, pressed)

    def resync(self):
        if not self.resync_source:
            return
        self.x, self.y, pressed = self.resync_source()
        held = self.down | self.swallowed
        self.changes = ([(c, 0) for c in held - pressed] +
                        [(c, 1) for c in pressed - held])
        self.frame()

    def frame(self):
        region = self.locate(self.x, self.y)
        self.region = region
        dirty = False
        # While a passed-on button is held (a drag), stay in the drawing area,
        # clamped to its edge, so the stroke isn't cut off.
        if (region == "drawing" or self.down) and self.x is not None and self.y is not None:
            pos = self.scaled()
            if pos != self.last_out:
                if self.last_out is None or pos[0] != self.last_out[0]:
                    self.mouse.emit(EV_ABS, ABS_X, pos[0])
                if self.last_out is None or pos[1] != self.last_out[1]:
                    self.mouse.emit(EV_ABS, ABS_Y, pos[1])
                self.last_out = pos
                dirty = True
        for c, v in self.changes:
            if v:
                if region == "drawing" or self.down:
                    if self.last_out is None:
                        self.swallowed.add(c)
                        continue
                    self.mouse.emit(EV_KEY, c, 1)
                    self.down.add(c)
                    dirty = True
                else:
                    self.swallowed.add(c)
                    if isinstance(region, Menu):
                        self.fire(region, c)
            else:
                if c in self.down:
                    self.mouse.emit(EV_KEY, c, 0)
                    self.down.discard(c)
                    dirty = True
                self.swallowed.discard(c)
        self.changes.clear()
        if dirty:
            self.mouse.syn()

    def fire(self, menu, button):
        if menu.buttons is not None and button not in menu.buttons:
            return
        now = self.clock()
        if now - self.last_fire.get(menu.name, -1e9) < self.cfg.debounce_ms / 1000:
            return
        self.last_fire[menu.name] = now
        self.actions.run(menu)

    def release_all(self):
        if self.down:
            for c in self.down:
                self.mouse.emit(EV_KEY, c, 0)
            self.mouse.syn()
        self.down.clear()
        self.swallowed.clear()
        self.changes.clear()


# --- run -----------------------------------------------------------------------

class Daemon:
    def __init__(self, args):
        self.args = args
        self.cfg_path = args.config
        self.cfg = load_config(self.cfg_path)
        self.cfg_mtime = self._mtime()
        for w in config_warnings(self.cfg):
            log(f"warning: {w}")
        self.reload_wanted = False
        self.stop = False

    def _mtime(self):
        try:
            return os.stat(self.cfg_path).st_mtime_ns
        except OSError:
            return None

    def maybe_reload(self, mapper):
        m = self._mtime()
        if not self.reload_wanted and (m is None or m == self.cfg_mtime):
            return
        self.reload_wanted = False
        self.cfg_mtime = m
        try:
            cfg = load_config(self.cfg_path)
        except ConfigError as e:
            log(f"config not reloaded: {e}")
            return
        for w in config_warnings(cfg):
            log(f"warning: {w}")
        if cfg.device != self.cfg.device:
            log("note: [tablet] device changes need a restart")
        self.cfg = cfg
        mapper.set_config(cfg)
        log(f"config reloaded: {len(cfg.menus)} menu(s)"
            + (f" (+{len(cfg.hidden)} hidden)" if cfg.hidden else "")
            + f", drawing {cfg.drawing}")

    def run(self):
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, "reload_wanted", True))
        for s in (signal.SIGTERM, signal.SIGINT):
            signal.signal(s, lambda *_: setattr(self, "stop", True))

        for w in driver_warnings():
            log(f"warning: {w}")
        out_max = (self.args.out_max_x, self.args.out_max_y)
        d = self.cfg.drawing
        # units/mm so the virtual device's size is the drawing area's size
        res = (max(1, round(out_max[0] * 39.37 / (d.x1 - d.x0))),
               max(1, round(out_max[1] * 39.37 / (d.y1 - d.y0))))
        out_name, kbd_name = OUT_NAME, KBD_NAME
        if self.args.test_names:    # libinput ignores these (see the udev rule)
            out_name, kbd_name = (n.replace(DEVICE_NAME, DEVICE_NAME + " test")
                                  for n in (OUT_NAME, KBD_NAME))
        mouse = UInput(out_name, BUTTON_NAMES, {ABS_X: (0, out_max[0], res[0]),
                                                ABS_Y: (0, out_max[1], res[1])})
        # Every key a menu might use after a reload, except the system keys that
        # make logind/rfkill watch the device, unless the layout uses them.
        menu_keys = {c for m in self.cfg.menus for k in m.keys for c in k}
        system = {KEYS[k] for k in SYSTEM_KEYS if k in KEYS}
        keyboard = UInput(kbd_name, (set(KEYS.values()) - system) | menu_keys,
                          product=0xc2)
        log(f"virtual devices: {mouse.devnode()} ({out_name}), "
            f"{keyboard.devnode()} ({kbd_name})")
        actions = Actions(keyboard)
        mapper = Mapper(self.cfg, mouse, actions, out_max)
        src = None
        waiting_logged = False
        try:
            while not self.stop:
                if src is None:
                    path = self.args.device or find_device(self.cfg.device)
                    try:
                        if not path:
                            raise FileNotFoundError(f"no {self.cfg.device!r} device")
                        src = Source(path)
                        mapper.resync_source = lambda s=src: (
                            s.get_abs(ABS_X)[0], s.get_abs(ABS_Y)[0], s.pressed())
                        x, y = src.absinfo[ABS_X], src.absinfo[ABS_Y]
                        log(f"grabbed {path} ({src.name}), x 0-{x[2]}, y 0-{y[2]}")
                        if (x[2] + 1, y[2] + 1) != (self.cfg.tablet_w, self.cfg.tablet_h):
                            log(f"warning: device is {x[2] + 1}x{y[2] + 1} counts, config "
                                f"says {self.cfg.tablet_w}x{self.cfg.tablet_h}")
                        mapper.x, mapper.y = x[0], y[0]
                        waiting_logged = False
                    except OSError as e:
                        if not waiting_logged:
                            log(f"waiting for the tablet: {e}")
                            if isinstance(e, PermissionError):
                                log("  (no access: install the udev rule, see README)")
                            waiting_logged = True
                        src = None
                fds = [src.fd] if src else []
                try:
                    r, _, _ = select.select(fds, [], [], 1.0 if src else 2.0)
                except InterruptedError:
                    r = []
                if r:
                    try:
                        for ev in src.read():
                            mapper.handle(*ev)
                    except OSError as e:
                        log(f"tablet gone: {e}")
                        mapper.release_all()
                        src.close()
                        src = None
                self.maybe_reload(mapper)
                actions.reap()
        finally:
            mapper.release_all()
            if src:
                src.close()
            mouse.close()
            keyboard.close()
        log("stopped")


# --- measure ---------------------------------------------------------------

def cmd_measure(args):
    cfg = None
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log(f"(no layout names: {e})")
    for w in driver_warnings():
        log(f"warning: {w}")
    path = args.device or find_device(cfg.device if cfg else DEVICE_NAME)
    if not path:
        sys.exit(f"no {DEVICE_NAME!r} input device: is the tablet attached?")
    try:
        src = Source(path, grab=True)
    except PermissionError:
        sys.exit(f"{path}: permission denied (install the udev rule, see README)")
    except OSError as e:
        if e.errno == errno.EBUSY:
            sys.exit("the tablet is grabbed by another program, probably the daemon:\n"
                     "  systemctl --user stop calcomp-menud")
        raise
    print(f"{path}: {src.name}. Move the tool; press a button to mark a point.\n"
          "Two marks print a rectangle for areas.toml. Ctrl-C to quit.\n"
          "The pointer doesn't move while this runs.")
    x = y = None
    marks = []
    held = set()

    def where():
        if cfg is None:
            return ""
        for f in cfg.faulty:
            if f.contains(x, y):
                return " FAULTY STRIP"
        if cfg.drawing.contains(x, y):
            return " drawing"
        for m in cfg.menus:
            if m.rect.contains(x, y):
                return f" menu {m.name!r}"
        return ""

    try:
        while True:
            select.select([src.fd], [], [])
            for t, c, v in src.read():
                if t == EV_ABS and c == ABS_X:
                    x = v
                elif t == EV_ABS and c == ABS_Y:
                    y = v
                elif t == EV_KEY and v == 1:
                    held.add(c)
                    if x is not None and y is not None:
                        marks.append((x, y))
                        print(f"\nmark {len(marks)}: x {x}, y {y} "
                              f"({BUTTON_NAMES.get(c, hex(c))}){where()}")
                        if len(marks) % 2 == 0:
                            (ax, ay), (bx, by) = marks[-2:]
                            print(f"  x0 = {min(ax, bx)}, y0 = {min(ay, by)}, "
                                  f"x1 = {max(ax, bx)}, y1 = {max(ay, by)}   "
                                  f'({abs(ax - bx) / 1000:.2f}" x {abs(ay - by) / 1000:.2f}")')
                elif t == EV_KEY and v == 0:
                    held.discard(c)
                elif t == EV_SYN and c == SYN_REPORT and x is not None and y is not None:
                    sys.stdout.write(f"\rx {x:5d}  y {y:5d}   "
                                     f'({x / 1000:5.2f}" {y / 1000:5.2f}" from top-left)'
                                     f"{where():<28}")
                    sys.stdout.flush()
    except KeyboardInterrupt:
        print()
    finally:
        src.close()


# --- overlay ---------------------------------------------------------------

PAGES = {"letter": (11000, 8500), "a4": (11693, 8268), "a3": (16535, 11693),
         "tabloid": (17000, 11000)}


def svg_layout(cfg, frame=None):
    """The layout's SVG elements, in tablet counts (1 unit = 0.001 inch).
    With a `frame` (Rect), only the buttons wholly inside it are drawn, and the
    rest (outline, strips, drawing area) is clipped to it."""
    e = ["<g clip-path='url(#frame)'>"] if frame else []
    W, H = cfg.tablet_w, cfg.tablet_h
    font = "font-family='DejaVu Sans, Helvetica, Arial, sans-serif'"
    e.append(f"<rect x='0' y='0' width='{W}' height='{H}' fill='none' "
             "stroke='#999' stroke-width='10' stroke-dasharray='120 80'/>")
    for f in cfg.faulty:
        e.append(f"<rect x='{f.x0}' y='{f.y0}' width='{f.x1 - f.x0}' height='{f.y1 - f.y0}' "
                 "fill='url(#hatch)' stroke='#c33' stroke-width='8'/>")
        e.append(f"<text x='{(f.x0 + f.x1) // 2}' y='{(f.y0 + f.y1) // 2 + 50}' {font} "
                 "font-size='140' fill='#c33' text-anchor='middle'>faulty strip — "
                 f"don't use (x {f.x0}-{f.x1}, y {f.y0}-{f.y1})</text>")
    d = cfg.drawing
    e.append(f"<rect x='{d.x0}' y='{d.y0}' width='{d.x1 - d.x0}' height='{d.y1 - d.y0}' "
             "fill='none' stroke='#000' stroke-width='16'/>")
    e.append(f"<text x='{(d.x0 + d.x1) // 2}' y='{(d.y0 + d.y1) // 2}' {font} "
             "font-size='300' fill='#bbb' text-anchor='middle'>drawing area</text>")
    e.append(f"<text x='{(d.x0 + d.x1) // 2}' y='{(d.y0 + d.y1) // 2 + 260}' {font} "
             f"font-size='150' fill='#bbb' text-anchor='middle'>{escape(str(d))}</text>")
    if frame:
        e.append("</g>")
    for m in cfg.menus:
        r = m.rect
        if frame and not (frame.x0 <= r.x0 and r.x1 <= frame.x1 and
                          frame.y0 <= r.y0 and r.y1 <= frame.y1):
            continue
        w, h = r.x1 - r.x0, r.y1 - r.y0
        e.append(f"<rect x='{r.x0}' y='{r.y0}' width='{w}' height='{h}' rx='40' "
                 "fill='#f4f4f4' stroke='#000' stroke-width='12'/>")
        lines = m.label.split("\n") if m.label else []
        longest = max((len(x) for x in lines), default=1)
        size = max(60, min(220, h // 4, int(w / max(longest, 1) * 1.6)))
        if m.image:
            # picture on top, label (smaller) below it, the action at the bottom
            size = min(size, 130)
            pad = max(60, min(w, h) // 12)
            text_h = len(lines) * size * 12 // 10
            ih = h - 2 * pad - 90 - text_h
            e.append(f"<image x='{r.x0 + pad}' y='{r.y0 + pad}' width='{w - 2 * pad}' "
                     f"height='{max(ih, 1)}' preserveAspectRatio='xMidYMid meet' "
                     f"xlink:href='{image_uri(m.image)}'/>")
            y0 = r.y0 + pad + ih + size
        else:
            y0 = (r.y0 + r.y1) // 2 - (len(lines) - 1) * size * 6 // 10 + size // 3
        for i, line in enumerate(lines):
            e.append(f"<text x='{(r.x0 + r.x1) // 2}' y='{y0 + i * size * 12 // 10}' {font} "
                     f"font-size='{size}' text-anchor='middle'>{escape(line)}</text>")
        sub = m.keys_text if m.keys else m.command
        if not sub:
            continue
        sub = sub if len(sub) <= w // 50 else sub[:max(w // 50 - 1, 1)] + "…"
        e.append(f"<text x='{(r.x0 + r.x1) // 2}' y='{r.y1 - 60}' {font} font-size='70' "
                 f"fill='#666' text-anchor='middle'>{escape(sub)}</text>")
    return e


_image_cache = {}


def image_uri(path):
    """The image file as a data: URI, so the SVG/PDF stands alone."""
    if path not in _image_cache:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise ConfigError(f"image {path}: {e.strerror}") from None
        ext = os.path.splitext(path)[1].lower()
        if ext == ".svgz":
            import gzip
            data = gzip.decompress(data)
        _image_cache[path] = (f"data:{IMAGE_TYPES[ext]};base64,"
                              + base64.b64encode(data).decode())
    return _image_cache[path]


def svg_doc(cfg, view, size_in, extra=(), frame=None):
    vx, vy, vw, vh = view
    clip = (f"<clipPath id='frame'><rect x='{frame.x0}' y='{frame.y0}' "
            f"width='{frame.x1 - frame.x0}' height='{frame.y1 - frame.y0}'/></clipPath>"
            if frame else "")
    defs = (f"<defs>{clip}<pattern id='hatch' width='150' height='150' "
            "patternUnits='userSpaceOnUse' patternTransform='rotate(45)'>"
            "<line x1='0' y1='0' x2='0' y2='150' stroke='#e99' stroke-width='30'/>"
            "</pattern></defs>")
    body = "\n".join(svg_layout(cfg, frame) + list(extra))
    return ("<?xml version='1.0' encoding='UTF-8'?>\n"
            "<svg xmlns='http://www.w3.org/2000/svg' "
            f"xmlns:xlink='http://www.w3.org/1999/xlink' width='{size_in[0]}in' "
            f"height='{size_in[1]}in' viewBox='{vx} {vy} {vw} {vh}'>\n"
            f"<rect x='{vx}' y='{vy}' width='{vw}' height='{vh}' fill='#fff'/>\n"
            f"{defs}\n{body}\n</svg>\n")


FONT = "font-family='DejaVu Sans, sans-serif'"


def text_w(text, size):
    return int(len(text) * size * 0.62) + 20       # generous DejaVu Sans estimate


class Placer:
    """Finds free spots on a sheet for alignment marks and the scale bar:
    inside the frame, clear of the buttons and of the faulty strips (where
    `measure` would read wrong numbers)."""

    PAD = 40
    STEP = 50

    def __init__(self, frame, obstacles):
        self.frame = frame
        self.taken = [Rect(r.x0 - self.PAD, r.y0 - self.PAD, r.x1 + self.PAD, r.y1 + self.PAD)
                      for r in obstacles if r.overlaps(frame)]
        f, st = frame, self.STEP
        self.grid = [(x, y) for x in range(f.x0 + 20, f.x1 - 19, st)
                     for y in range(f.y0 + 20, f.y1 - 19, st)]

    def free(self, boxes):
        f = self.frame
        for b in boxes:
            if b.x0 < f.x0 + 20 or b.y0 < f.y0 + 20 or b.x1 > f.x1 - 20 or b.y1 > f.y1 - 20:
                return False
            if any(b.overlaps(t) for t in self.taken):
                return False
        return True

    def place(self, start, layouts):
        """layouts(x, y) -> [(boxes, svg), ...] in order of preference; the
        first free one nearest to `start` wins. Returns its svg, or None."""
        sx, sy = start
        for x, y in sorted(self.grid, key=lambda p: (p[0] - sx) ** 2 + (p[1] - sy) ** 2):
            for boxes, svg in layouts(x, y):
                if self.free(boxes):
                    self.taken += [Rect(b.x0 - self.PAD, b.y0 - self.PAD,
                                        b.x1 + self.PAD, b.y1 + self.PAD) for b in boxes]
                    return svg
        return None


def crosshair_layouts(inward_right):
    """An alignment mark labelled with its tablet counts; the label goes
    beside it (towards the inside of the sheet first), above or below."""
    R, SIZE = 150, 75

    def layouts(x, y):
        label = f"x {x}, y {y}"
        w = text_w(label, SIZE)
        mark = (f"<path d='M{x - R} {y}H{x + R}M{x} {y - R}V{y + R}' stroke='#000' "
                f"stroke-width='6'/><circle cx='{x}' cy='{y}' r='60' fill='none' "
                "stroke='#000' stroke-width='6'/>")
        cross = Rect(x - R, y - R, x + R, y + R)
        right = (Rect(x + R + 30, y - 50, x + R + 30 + w, y + 50),
                 f"<text x='{x + R + 30}' y='{y + 27}' {FONT} font-size='{SIZE}'>{label}</text>")
        left = (Rect(x - R - 30 - w, y - 50, x - R - 30, y + 50),
                f"<text x='{x - R - 30}' y='{y + 27}' {FONT} font-size='{SIZE}' "
                f"text-anchor='end'>{label}</text>")
        above = (Rect(x - w // 2, y - R - 130, x + w // 2, y - R - 20),
                 f"<text x='{x}' y='{y - R - 45}' {FONT} font-size='{SIZE}' "
                 f"text-anchor='middle'>{label}</text>")
        below = (Rect(x - w // 2, y + R + 20, x + w // 2, y + R + 130),
                 f"<text x='{x}' y='{y + R + 100}' {FONT} font-size='{SIZE}' "
                 f"text-anchor='middle'>{label}</text>")
        sides = (right, left) if inward_right else (left, right)
        return [([cross, box], mark + svg) for box, svg in (sides[0], above, below, sides[1])]
    return layouts


def ruler_layouts(x, y):
    label = "1 inch: check the print scale"
    w = text_w(label, 75)
    return [([Rect(x, y - 60, x + 1100 + w, y + 40)],
             f"<path d='M{x} {y}h1000' stroke='#000' stroke-width='12'/>"
             f"<text x='{x + 1100}' y='{y + 27}' {FONT} font-size='75'>{label}</text>")]


def sheet_marks(cfg, frame, note=None):
    """Four alignment marks (one near each corner), the 1-inch bar and an
    optional note, placed where they don't cover anything."""
    obstacles = [m.rect for m in cfg.menus] + list(cfg.faulty) + [cfg.drawing]
    p = Placer(frame, obstacles)
    f = frame
    out = []
    for (sx, sy), inward_right in (((f.x0, f.y0), True), ((f.x1, f.y0), False),
                                   ((f.x0, f.y1), True), ((f.x1, f.y1), False)):
        svg = p.place((sx, sy), crosshair_layouts(inward_right))
        if svg:
            out.append(svg)
        else:
            log(f"note: no free spot for an alignment mark near x {sx}, y {sy}")
    svg = p.place((f.x0 + 1000, f.y1), ruler_layouts)
    if svg:
        out.append(svg)
    else:
        log("note: no free spot for the 1-inch bar")
    if note:
        w = text_w(note, 75)
        svg = p.place((f.x1 - w // 2, f.y1), lambda x, y: [(
            [Rect(x - w // 2, y - 60, x + w // 2, y + 40)],
            f"<text x='{x}' y='{y + 27}' {FONT} font-size='75' "
            f"text-anchor='middle'>{escape(note)}</text>")])
        if svg:
            out.append(svg)
    return out


def tile_starts(lo, hi, size, spans, word):
    """Where the pages start along one axis, so that every button (span
    a..b) lies wholly on some page: a page ends before the first button it
    would cut, and the next page starts just before that button."""
    starts = [lo]
    while starts[-1] + size < hi:
        end = starts[-1] + size
        cut = [a for a, b in spans if b > end]
        if not cut:
            break
        nxt = max(lo, min(cut) - 400)
        if nxt <= starts[-1]:
            raise ConfigError(f"a button is too {word} for the page; use a bigger "
                              "--page or a smaller --margin")
        starts.append(nxt)
    return starts


def cmd_overlay(args):
    if args.full:
        args.region, args.page = "tablet", "none"
    cfg = load_config(args.config)
    for w in config_warnings(cfg):
        log(f"warning: {w}")
    if args.region == "menus" and cfg.menus:
        rs = [m.rect for m in cfg.menus]
        m = 800     # room for the alignment marks around the buttons
        bx0 = max(0, min(r.x0 for r in rs) - m)
        by0 = max(0, min(r.y0 for r in rs) - m)
        bx1 = min(cfg.tablet_w, max(r.x1 for r in rs) + m)
        by1 = min(cfg.tablet_h, max(r.y1 for r in rs) + m)
    else:
        bx0, by0, bx1, by1 = 0, 0, cfg.tablet_w, cfg.tablet_h
    # Alignment marks are labelled with tablet counts: put the tool on one
    # and `measure` should print those numbers.
    base, ext = os.path.splitext(args.output)
    pdf = args.pdf or ext.lower() == ".pdf"

    if args.page == "none":
        note = None
        if (bx0, by0, bx1, by1) == (0, 0, cfg.tablet_w, cfg.tablet_h):
            note = (f"whole active area, {cfg.tablet_w / 1000:g} x {cfg.tablet_h / 1000:g} "
                    "in: the sheet edge is the active-area edge (x 0, y 0 = top-left)")
        frame = Rect(bx0, by0, bx1, by1)
        pages = [svg_doc(cfg, (bx0, by0, bx1 - bx0, by1 - by0),
                         ((bx1 - bx0) / 1000, (by1 - by0) / 1000),
                         sheet_marks(cfg, frame, note))]
    else:
        pw, ph = PAGES[args.page]
        if args.portrait:
            pw, ph = ph, pw
        margin = int(args.margin * 1000)
        tw, th = pw - 2 * margin, ph - 2 * margin
        rects = [m.rect for m in cfg.menus]
        xs = tile_starts(bx0, bx1, tw, [(r.x0, r.x1) for r in rects], "wide")
        ys = tile_starts(by0, by1, th, [(r.y0, r.y1) for r in rects], "tall")
        pages = []
        for j, ty in enumerate(ys):
            for i, tx in enumerate(xs):
                vx, vy = tx - margin, ty - margin
                fw, fh = min(tw, bx1 - tx), min(th, by1 - ty)
                frame = Rect(tx, ty, tx + fw, ty + fh)
                # each sheet gets its own alignment marks and scale bar, inside
                # its frame
                page_marks = sheet_marks(cfg, frame)
                tag = (f"<text x='{vx + margin}' y='{vy + margin - 80}' "
                       "font-family='DejaVu Sans, sans-serif' font-size='90'>"
                       f"{escape(os.path.basename(base))}: page {j * len(xs) + i + 1}/"
                       f"{len(xs) * len(ys)} (row {j + 1}, column {i + 1}) — "
                       f"tablet x {tx}-{tx + fw}, y {ty}-{ty + fh}; the sheet is the "
                       "dashed frame</text>"
                       f"<rect x='{tx}' y='{ty}' width='{fw}' height='{fh}' fill='none' "
                       "stroke='#09c' stroke-width='5' stroke-dasharray='60 60'/>")
                pages.append(svg_doc(cfg, (vx, vy, pw, ph), (pw / 1000, ph / 1000),
                                     page_marks + [tag], frame))
    files = []
    for n, svg in enumerate(pages):
        name = f"{base}.svg" if len(pages) == 1 else f"{base}-{n + 1}.svg"
        with open(name, "w") as f:
            f.write(svg)
        files.append(name)
    if pdf:
        tool = shutil.which("rsvg-convert")
        if not tool:
            sys.exit("PDF needs rsvg-convert (apt install librsvg2-bin); SVGs written: "
                     + " ".join(files))
        subprocess.run([tool, "-f", "pdf", "-o", f"{base}.pdf", *files], check=True)
        if not args.keep_svg:
            for f in files:
                os.remove(f)
            files = []
        files.append(f"{base}.pdf")
    print("wrote " + " ".join(files) + f" ({len(pages)} page(s), 1:1 — print at 100%, "
          "no 'fit to page')")


# --- check -------------------------------------------------------------------

def cmd_check(args):
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        sys.exit(f"error: {e}")
    print(f"{args.config}: OK")
    print(f"  tablet  {cfg.tablet_w}x{cfg.tablet_h} counts, device {cfg.device!r}")
    print(f"  drawing {cfg.drawing}")
    for f in cfg.faulty:
        print(f"  faulty  {f}")
    for m in cfg.menus:
        btn = "any button" if m.buttons is None else "buttons " + ", ".join(
            sorted(BUTTON_NAMES[b] for b in m.buttons))
        print(f"  menu    {m.name!r:16} {m.rect}  {m.describe()}  ({btn})")
        if m.image:
            print(f"          image {m.image}")
    for m in cfg.hidden:
        print(f"  hidden  {m.name!r:16} {m.rect}  {m.describe()}")
    for w in config_warnings(cfg) + driver_warnings():
        print(f"warning: {w}")
    dev = find_device(cfg.device)
    print(f"  device  {dev or 'not found'}", end="")
    if dev:
        print("" if os.access(dev, os.R_OK) else "  (NOT readable: install the udev rule)")
    else:
        print()
    print(f"  uinput  {'writable' if os.access('/dev/uinput', os.W_OK) else 'NOT writable'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                    help=f"layout file (default {DEFAULT_CONFIG})")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="run the daemon")
    p.add_argument("--device", help="evdev node (default: find it by name)")
    p.add_argument("--out-max-x", type=int, default=17999, help=argparse.SUPPRESS)
    p.add_argument("--out-max-y", type=int, default=11999, help=argparse.SUPPRESS)
    p.add_argument("--test-names", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("measure", help="print the tablet coordinates under the tool")
    p.add_argument("--device", help="evdev node (default: find it by name)")
    p = sub.add_parser("overlay", help="draw the layout as a printable 1:1 SVG/PDF")
    p.add_argument("-o", "--output", default="calcomp-overlay.pdf",
                   help="output file; .pdf makes a PDF, .svg SVG(s) (default %(default)s)")
    p.add_argument("--pdf", action="store_true", help="also make a PDF from the SVGs")
    p.add_argument("--keep-svg", action="store_true", help="keep the SVGs next to the PDF")
    p.add_argument("--page", choices=sorted(PAGES) + ["none"], default="letter",
                   help="tile onto pages of this size, landscape (default letter); "
                        "none = one sheet the size of the region")
    p.add_argument("--portrait", action="store_true")
    p.add_argument("--margin", type=float, default=0.5, help="page margin, inches")
    p.add_argument("--region", choices=["menus", "tablet"], default="menus",
                   help="menus = just around the menu buttons (default); tablet = all")
    p.add_argument("--full", action="store_true",
                   help="one sheet the size of the whole tablet (18x12 in): drawing "
                        "area, faulty strips and buttons (= --region tablet --page none)")
    sub.add_parser("check", help="validate the config and show the layout")
    args = ap.parse_args(argv)
    cmd = args.cmd or "run"
    if cmd == "run":
        if not hasattr(args, "device"):
            args = ap.parse_args(["-c", args.config, "run"])
        try:
            Daemon(args).run()
        except ConfigError as e:
            sys.exit(f"error: {e}")
    elif cmd == "measure":
        cmd_measure(args)
    elif cmd == "overlay":
        try:
            cmd_overlay(args)
        except ConfigError as e:
            sys.exit(f"error: {e}")
    elif cmd == "check":
        cmd_check(args)


if __name__ == "__main__":
    main()
