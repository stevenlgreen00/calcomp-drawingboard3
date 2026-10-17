// SPDX-License-Identifier: GPL-2.0
/*
 * CalComp DrawingBoard III serial tablet driver
 *
 * Copyright (C) 2026 Steven Green
 *
 * A serio driver: the serial port is handed to the kernel by an attach helper
 * (tools/calcomp_attach.py, the equivalent of inputattach), which puts the tty
 * into the serport line discipline and tags it as SERIO_RS232 with protocol
 * SERIO_CALCOMP. This driver then binds to that serio port, sets the tablet up
 * and reports an input device: by default an absolute-position mouse (like a
 * virtual machine's "USB tablet"), or with tablet_mode=1 a pen tablet.
 *
 * The tablet is reset to its Default setup (binary format 23, 9600 8N1, run
 * mode) on attach. Commands are from GTCO's "DrawingBoard III Commands"
 * reference (summarised in docs/TABLET.md); the protocol details are in
 * calcomp_proto.h and NOTES.md. In run mode the tablet streams continuously
 * while a tool is in range; proximity-out is detected by the stream stopping,
 * since out-of-proximity reporting is off.
 */

#include <linux/completion.h>
#include <linux/delay.h>
#include <linux/input.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/serio.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/timer.h>

#include "calcomp_proto.h"

#define DRIVER_DESC	"CalComp DrawingBoard III serial tablet driver"

/* Not a mainline ID; mainline currently ends at 0x43. Must match the attach helper. */
#define SERIO_CALCOMP	0xc0

#define CALCOMP_DEFAULT_MAX_X	18000	/* model 34180: 18" x 12" at 1000 lpi */
#define CALCOMP_DEFAULT_MAX_Y	12000
#define CALCOMP_RES_PER_MM	39	/* 1000 lpi = 39.37 units/mm */
#define CALCOMP_REPLY_TIMEOUT_MS 1000
#define CALCOMP_REPLY_MAX	48

/*
 * Sent as MSC_SERIAL with every packet. Unlike repeated key/abs values, MSC
 * events are never filtered by the input core, so userspace keeps seeing
 * frames while a tool is held perfectly still. Without them libinput assumes
 * the tool left after 50 ms of silence and forces proximity out/in over and
 * over (captures/libinput.log). Distinct serials also make the pen and puck
 * separate tools in userspace.
 */
#define CALCOMP_SERIAL_PEN	1
#define CALCOMP_SERIAL_PUCK	2

/* timer_container_of() replaced from_timer() in 6.16 */
#ifndef timer_container_of
#define timer_container_of(var, t, field) from_timer(var, t, field)
#endif

/*
 * The tablet sends a packet every 8 ms, but the USB-serial adapter delivers
 * them in bursts: measured gaps between deliveries are typically 16-40 ms,
 * with occasional 60-120 ms dropouts (captures/gaps-20261017-024933.json).
 * 50 ms caused constant proximity flicker; 150 ms clears it.
 */
static unsigned int prox_timeout_ms = 150;
module_param(prox_timeout_ms, uint, 0644);
MODULE_PARM_DESC(prox_timeout_ms,
		 "Report the tool out of proximity after this many ms without a packet (default 150)");

static bool reset_on_attach = true;
module_param(reset_on_attach, bool, 0444);
MODULE_PARM_DESC(reset_on_attach,
		 "Recall the tablet's Default setup (bank 1) before configuring it (default Y)");

static bool send_init = true;
module_param(send_init, bool, 0444);
MODULE_PARM_DESC(send_init, "Send the setup commands when attaching (default Y)");

static unsigned int cmd_delay_ms = 250;
module_param(cmd_delay_ms, uint, 0644);
MODULE_PARM_DESC(cmd_delay_ms, "Pause after each setup command, in ms (default 250)");

static unsigned int max_x;
module_param(max_x, uint, 0444);
MODULE_PARM_DESC(max_x, "Override the tablet width in counts (default: ask the tablet, else 18000)");

static unsigned int max_y;
module_param(max_y, uint, 0444);
MODULE_PARM_DESC(max_y, "Override the tablet height in counts (default: ask the tablet, else 12000)");

/*
 * Active area: the rectangle of the tablet (in counts, origin top-left, as
 * reported) that is stretched over the full output range, i.e. the whole
 * screen. Positions outside it are clamped to its edge. Lets a part of the
 * tablet match the screen's shape, or avoid a faulty strip of the sensing
 * grid (the user's 34180 has one at y ~6800-7600, see NOTES.md).
 * 0 for area_x1/area_y1 = the tablet's far edge. Live-changeable.
 */
static unsigned int area_x0, area_y0, area_x1, area_y1;
module_param(area_x0, uint, 0644);
MODULE_PARM_DESC(area_x0, "Active area left edge in counts (default 0)");
module_param(area_y0, uint, 0644);
MODULE_PARM_DESC(area_y0, "Active area top edge in counts (default 0)");
module_param(area_x1, uint, 0644);
MODULE_PARM_DESC(area_x1, "Active area right edge in counts (default 0 = tablet width)");
module_param(area_y1, uint, 0644);
MODULE_PARM_DESC(area_y1, "Active area bottom edge in counts (default 0 = tablet height)");

/*
 * Default: an absolute-position mouse. The tablet has no pressure or tilt, and
 * KDE handles real mouse buttons everywhere, whereas its tablet handling left
 * window controls and app buttons ignoring left clicks however they were sent
 * (tip, BTN_LEFT tool button, KWin pen-button rebinding). No BTN_TOOL_*,
 * BTN_TOUCH or BTN_STYLUS* may be reported in this mode, or udev/libinput
 * would classify the device as a tablet again.
 */
static bool tablet_mode;
module_param(tablet_mode, bool, 0444);
MODULE_PARM_DESC(tablet_mode,
		 "Report a pen tablet (tools, tip, pen buttons) instead of an absolute mouse (default N)");

/*
 * Puck buttons are labeled 0-9, A-F. 0 (the manual's "pick" button) and 3 are
 * the large buttons on opposite sides of the top row; 1 and 2 are between them.
 *
 * Mouse mode: 0 = left, 1 = middle, 3 = right, like the pen's tip, lower and
 * upper buttons.
 *
 * Tablet mode: the puck reaches the desktop as a pen (see puck_as_mouse), and
 * KDE/Qt only act on a pen's BTN_TOUCH (tip: the only thing that draws),
 * BTN_STYLUS and BTN_STYLUS2 (via the Drawing Tablet "pen button" settings);
 * BTN_RIGHT/BTN_MIDDLE from a pen are ignored. So 0 = BTN_TOUCH,
 * 1 = BTN_STYLUS, 3 = BTN_STYLUS2.
 */
static const int calcomp_mouse_puck_keys[CALCOMP_PUCK_BUTTONS] = {
	BTN_LEFT, BTN_MIDDLE, BTN_SIDE, BTN_RIGHT,
	BTN_EXTRA, BTN_FORWARD, BTN_BACK, BTN_TASK,
	BTN_0, BTN_1, BTN_2, BTN_3, BTN_4, BTN_5, BTN_6, BTN_7,
};
static const int calcomp_tablet_puck_keys[CALCOMP_PUCK_BUTTONS] = {
	BTN_TOUCH, BTN_STYLUS, BTN_SIDE, BTN_STYLUS2,
	BTN_EXTRA, BTN_FORWARD, BTN_BACK, BTN_TASK,
	BTN_0, BTN_1, BTN_2, BTN_3, BTN_4, BTN_5, BTN_6, BTN_7,
};
static int puck_keys[CALCOMP_PUCK_BUTTONS];	/* 0 = the mode's default */
static int n_puck_keys;

/*
 * libinput disables BTN_TOOL_MOUSE/BTN_TOOL_LENS on non-Wacom tablets, so a
 * puck reported that way never comes into proximity there and can't move the
 * pointer (captures/libinput-puck.log). By default the puck is reported as a
 * second pen instead; MSC_SERIAL still keeps it a separate tool.
 */
static bool puck_as_mouse;
module_param(puck_as_mouse, bool, 0444);
MODULE_PARM_DESC(puck_as_mouse,
		 "tablet_mode only: report the puck as BTN_TOOL_MOUSE instead of a second pen (default N; libinput ignores mouse tools on non-Wacom tablets)");
/*
 * KDE's pop-up menus ignore a tablet tip tap (from the pen or the puck), but
 * accept a BTN_LEFT tool button; GIMP only draws on a tip contact (BTN_TOUCH).
 * So a tip press is held back until it's clear what it is: released without
 * moving -> a BTN_LEFT click; moved more than tap_move counts, or held longer
 * than tap_hold_ms -> BTN_TOUCH down (a stroke or a drag).
 */
static bool tap_click = true;
module_param(tap_click, bool, 0644);
MODULE_PARM_DESC(tap_click,
		 "tablet_mode only: turn a tip tap without movement into a BTN_LEFT click, so menus work (default Y)");

static unsigned int tap_move = 40;
module_param(tap_move, uint, 0644);
MODULE_PARM_DESC(tap_move,
		 "Movement in counts (1000/inch) after which a tip press becomes a stroke (default 40, about 1 mm)");

static unsigned int tap_hold_ms = 300;
module_param(tap_hold_ms, uint, 0644);
MODULE_PARM_DESC(tap_hold_ms,
		 "Hold time in ms after which a still tip press becomes a touch/drag (default 300)");

module_param_array(puck_keys, int, &n_puck_keys, 0444);
MODULE_PARM_DESC(puck_keys, "Key codes for puck buttons 0-F (default: mouse LEFT, MIDDLE, SIDE, RIGHT; tablet TOUCH, STYLUS, SIDE, STYLUS2; then EXTRA, FORWARD, BACK, TASK, BTN_0..BTN_7)");

/*
 * The tablet obeys single-byte commands at any time (XON/XOFF, mode and
 * resolution letters), so line noise, e.g. when the USB-serial adapter is
 * plugged in, can leave it stopped or misconfigured; the explicit settings
 * below didn't undo that, only a power cycle did. XON plus recalling the
 * Default bank brings it back to its power-on state.
 */
static const char * const calcomp_reset_cmds[] = {
	"\x11",			/* DC1 (XON): resume after a stray XOFF */
	"\x1b%VR1\r",		/* recall bank 1 (Default) and make it active */
	NULL
};
#define CALCOMP_RESET_DELAY_MS	1000

/*
 * Sent before the ID queries. Incremental run mode with increment 1 keeps the
 * tablet quiet while a tool rests on it, so the query replies don't get mixed
 * with coordinate packets.
 */
static const char * const calcomp_setup_cmds[] = {
	"\x1b%A1\r",		/* enable data output */
	"\x1b%Q\r",		/* clear prompt mode */
	"\x1b%C1N81\r",		/* 9600 8N1 */
	"\x1b%^23\r",		/* binary output format 23 */
	"\x1b%JR1000,0\r",	/* 1000 lpi */
	"\x1b%VA0\r",		/* report buttons, not pressure */
	"\x1b%X1\r",		/* increment 1 ... */
	"\x1b%Y1\r",
	"\x1b%IR\r",		/* ... in incremental run mode: quiet while still */
	NULL
};

/*
 * Sent last: plain run mode streams continuously while a tool is in range,
 * even when it's still. (ESC%IR is *incremental* run mode; with it last a
 * resting tool sent nothing and dropped out of proximity.)
 */
static const char * const calcomp_stream_cmds[] = {
	"\x1b%X0\r",		/* increment 0, in case anything re-enables it */
	"\x1b%Y0\r",
	"\x1b%R\r",		/* run mode */
	NULL
};

struct calcomp {
	struct serio *serio;
	struct input_dev *dev;
	spinlock_t lock;		/* protects everything below */
	struct timer_list prox_timer;

	u8 pkt[CALCOMP_PACKET_LEN];
	unsigned int idx;
	bool ready;			/* input device registered */
	bool in_prox;
	enum calcomp_tool tool;		/* current or last identified tool */

	/* reply capture during setup */
	bool capturing;
	bool capture_text;		/* stop at '\n' instead of after reply_want bytes */
	unsigned int capture_skip;	/* text mode: bytes left of a packet to skip */
	unsigned int reply_want;
	unsigned int reply_len;
	u8 reply[CALCOMP_REPLY_MAX];
	struct completion reply_done;

	int max_x, max_y;
	int keymap[CALCOMP_PUCK_BUTTONS];
	int puck_tool_key;		/* BTN_TOOL_PEN, or BTN_TOOL_MOUSE if puck_as_mouse */

	/* tap_click: state of the tip (pen tip, or puck button mapped to BTN_TOUCH) */
	enum { TIP_UP, TIP_PENDING, TIP_DOWN } tip;
	int tip_x, tip_y;		/* where the pending press started */
	unsigned long tip_since;	/* jiffies when it started */

	/*
	 * A button held while the tool went out of range stays ignored after
	 * it returns, until it has been released once; otherwise putting the
	 * puck down with a finger on a button would click wherever it lands.
	 */
	bool held;			/* a button was down in the last packet */
	bool suppress;			/* ignore buttons until all are released */
	unsigned long resyncs;		/* packets cut short by a new sync byte */
	unsigned long stray;		/* bytes outside any packet */
	char phys[64];
};

static int calcomp_tool_serial(enum calcomp_tool tool)
{
	return tool == CALCOMP_TOOL_PUCK ? CALCOMP_SERIAL_PUCK : CALCOMP_SERIAL_PEN;
}

/*
 * Map a coordinate (0..max-1) through the active area [lo, hi] onto 0..max-1.
 * An empty or inverted area falls back to the full range.
 */
static int calcomp_map_axis(int v, int max, unsigned int lo, unsigned int hi)
{
	long span;

	if (!hi || hi >= (unsigned int)max)
		hi = max - 1;
	if (lo >= hi)
		return v;
	v = clamp(v, (int)lo, (int)hi);
	span = hi - lo;
	return (int)(((long)(v - lo) * (max - 1) + span / 2) / span);
}

/* Called with cc->lock held. */
static void calcomp_prox_out(struct calcomp *cc)
{
	struct input_dev *dev = cc->dev;
	int i;

	if (!tablet_mode) {
		/* a mouse has no proximity: just release everything */
		input_report_key(dev, BTN_LEFT, 0);
		input_report_key(dev, BTN_MIDDLE, 0);
		input_report_key(dev, BTN_RIGHT, 0);
		for (i = 0; i < CALCOMP_PUCK_BUTTONS; i++)
			input_report_key(dev, cc->keymap[i], 0);
		input_sync(dev);
		cc->in_prox = false;
		return;
	}

	if (cc->tool == CALCOMP_TOOL_PUCK) {
		for (i = 0; i < CALCOMP_PUCK_BUTTONS; i++)
			input_report_key(dev, cc->keymap[i], 0);
		input_report_key(dev, cc->puck_tool_key, 0);
	} else {
		input_report_key(dev, BTN_TOUCH, 0);
		input_report_key(dev, BTN_STYLUS, 0);
		input_report_key(dev, BTN_STYLUS2, 0);
		input_report_key(dev, BTN_TOOL_PEN, 0);
	}
	input_report_key(dev, BTN_LEFT, 0);
	input_event(dev, EV_MSC, MSC_SERIAL, calcomp_tool_serial(cc->tool));
	input_sync(dev);
	cc->in_prox = false;
	cc->tip = TIP_UP;	/* a press pending when the tool left is dropped */
}

/*
 * Report the tip (BTN_TOUCH) for this packet, applying tap_click. Called with
 * cc->lock held, after the packet's position and other buttons are reported
 * and before the final sync. A click is sent as its own press frame followed
 * by the release in the packet's frame.
 */
static void calcomp_report_tip(struct calcomp *cc, bool pressed, int x, int y)
{
	struct input_dev *dev = cc->dev;

	if (!tap_click) {
		input_report_key(dev, BTN_TOUCH, pressed);
		cc->tip = pressed ? TIP_DOWN : TIP_UP;
		return;
	}

	switch (cc->tip) {
	case TIP_UP:
		if (pressed) {
			cc->tip = TIP_PENDING;
			cc->tip_x = x;
			cc->tip_y = y;
			cc->tip_since = jiffies;
		}
		break;
	case TIP_PENDING:
		if (!pressed) {
			/* released without moving: a click */
			input_report_key(dev, BTN_LEFT, 1);
			input_event(dev, EV_MSC, MSC_SERIAL, calcomp_tool_serial(cc->tool));
			input_sync(dev);
			input_report_key(dev, BTN_LEFT, 0);
			cc->tip = TIP_UP;
		} else if (abs(x - cc->tip_x) > tap_move || abs(y - cc->tip_y) > tap_move ||
			   time_after_eq(jiffies, cc->tip_since + msecs_to_jiffies(tap_hold_ms))) {
			/* moved or held: a stroke or drag */
			cc->tip = TIP_DOWN;
		}
		break;
	case TIP_DOWN:
		if (!pressed)
			cc->tip = TIP_UP;
		break;
	}
	input_report_key(dev, BTN_TOUCH, cc->tip == TIP_DOWN);
}

/* Called with cc->lock held, for each complete packet once the device is registered. */
static void calcomp_process_packet(struct calcomp *cc)
{
	struct input_dev *dev = cc->dev;
	struct calcomp_packet p;
	enum calcomp_tool tool;
	int i, pressed_code;
	bool tip;

	calcomp_decode_packet(cc->pkt, &p);

	cc->held = p.pen_buttons || p.puck_button >= 0;
	if (cc->suppress) {
		if (!cc->held) {
			cc->suppress = false;
		} else {
			/* still held from before the tool left: keep the tool, drop the buttons */
			p.pen_buttons = 0;
			p.puck_button = -1;
		}
	}

	/*
	 * A tool is only identified while one of its buttons is down; until
	 * then assume the last one seen. If that guess turns out wrong, take
	 * the old tool out of proximity first so userspace sees a clean switch.
	 */
	tool = p.tool == CALCOMP_TOOL_UNKNOWN ? cc->tool : p.tool;
	if (cc->in_prox && tool != cc->tool)
		calcomp_prox_out(cc);
	cc->tool = tool;
	cc->in_prox = true;

	/* Tablet origin is bottom-left; input devices use top-left. */
	input_report_abs(dev, ABS_X,
			 calcomp_map_axis(clamp(p.x, 0, cc->max_x - 1), cc->max_x,
					  READ_ONCE(area_x0), READ_ONCE(area_x1)));
	input_report_abs(dev, ABS_Y,
			 calcomp_map_axis(cc->max_y - 1 - clamp(p.y, 0, cc->max_y - 1), cc->max_y,
					  READ_ONCE(area_y0), READ_ONCE(area_y1)));

	if (!tablet_mode) {
		if (tool == CALCOMP_TOOL_PUCK) {
			pressed_code = p.puck_button >= 0 ? cc->keymap[p.puck_button] : -1;
			/* release first, then press, so buttons sharing a code don't cancel out */
			for (i = 0; i < CALCOMP_PUCK_BUTTONS; i++)
				if (cc->keymap[i] != pressed_code)
					input_report_key(dev, cc->keymap[i], 0);
			if (pressed_code >= 0)
				input_report_key(dev, pressed_code, 1);
		} else {
			input_report_key(dev, BTN_LEFT, p.pen_buttons & CALCOMP_PEN_TIP);
			input_report_key(dev, BTN_MIDDLE, p.pen_buttons & CALCOMP_PEN_LOWER);
			input_report_key(dev, BTN_RIGHT, p.pen_buttons & CALCOMP_PEN_UPPER);
		}
		input_sync(dev);
		mod_timer(&cc->prox_timer, jiffies + msecs_to_jiffies(prox_timeout_ms));
		return;
	}

	if (tool == CALCOMP_TOOL_PUCK) {
		input_report_key(dev, cc->puck_tool_key, 1);
		pressed_code = p.puck_button >= 0 ? cc->keymap[p.puck_button] : -1;
		/*
		 * Release first, then press, so buttons sharing a code don't
		 * cancel out. A button mapped to BTN_TOUCH is the puck's tip.
		 */
		for (i = 0; i < CALCOMP_PUCK_BUTTONS; i++)
			if (cc->keymap[i] != BTN_TOUCH && cc->keymap[i] != pressed_code)
				input_report_key(dev, cc->keymap[i], 0);
		if (pressed_code >= 0 && pressed_code != BTN_TOUCH)
			input_report_key(dev, pressed_code, 1);
		tip = pressed_code == BTN_TOUCH;
	} else {
		input_report_key(dev, BTN_TOOL_PEN, 1);
		input_report_key(dev, BTN_STYLUS, p.pen_buttons & CALCOMP_PEN_LOWER);
		input_report_key(dev, BTN_STYLUS2, p.pen_buttons & CALCOMP_PEN_UPPER);
		tip = p.pen_buttons & CALCOMP_PEN_TIP;
	}
	calcomp_report_tip(cc, tip, p.x, p.y);
	input_event(dev, EV_MSC, MSC_SERIAL, calcomp_tool_serial(tool));
	input_sync(dev);

	mod_timer(&cc->prox_timer, jiffies + msecs_to_jiffies(prox_timeout_ms));
}

static void calcomp_prox_timer(struct timer_list *t)
{
	struct calcomp *cc = timer_container_of(cc, t, prox_timer);
	unsigned long flags;

	spin_lock_irqsave(&cc->lock, flags);
	if (cc->ready && cc->in_prox) {
		calcomp_prox_out(cc);
		cc->suppress = cc->held;
	}
	spin_unlock_irqrestore(&cc->lock, flags);
}

static irqreturn_t calcomp_interrupt(struct serio *serio, unsigned char data,
				     unsigned int flags)
{
	struct calcomp *cc = serio_get_drvdata(serio);
	unsigned long irqflags;

	spin_lock_irqsave(&cc->lock, irqflags);

	if (cc->capturing) {
		/*
		 * A tool moving in range keeps sending packets while we wait for
		 * a reply. Text replies are 7-bit, so skip any packet (sync byte
		 * plus 5) that arrives in between. A binary reply must start on
		 * a sync byte.
		 */
		if (cc->capture_text) {
			if (data & CALCOMP_SYNC) {
				cc->capture_skip = CALCOMP_PACKET_LEN - 1;
				goto out;
			}
			if (cc->capture_skip) {
				cc->capture_skip--;
				goto out;
			}
		} else if (!cc->reply_len && !(data & CALCOMP_SYNC)) {
			goto out;
		}
		if (cc->reply_len < CALCOMP_REPLY_MAX)
			cc->reply[cc->reply_len++] = data;
		if ((cc->capture_text && data == '\n') ||
		    (!cc->capture_text && cc->reply_len >= cc->reply_want) ||
		    cc->reply_len >= CALCOMP_REPLY_MAX) {
			cc->capturing = false;
			complete(&cc->reply_done);
		}
		goto out;
	}

	/* A garbled byte can't be trusted; drop the packet it belongs to. */
	if (flags & (SERIO_PARITY | SERIO_FRAME)) {
		if (cc->idx)
			cc->resyncs++;
		cc->idx = 0;
		goto out;
	}

	if (data & CALCOMP_SYNC) {
		if (cc->idx)
			cc->resyncs++;
		cc->idx = 0;
	} else if (!cc->idx) {
		cc->stray++;
		goto out;
	}

	cc->pkt[cc->idx++] = data;
	if (cc->idx == CALCOMP_PACKET_LEN) {
		cc->idx = 0;
		if (cc->ready)
			calcomp_process_packet(cc);
	}

out:
	spin_unlock_irqrestore(&cc->lock, irqflags);
	return IRQ_HANDLED;
}

static int calcomp_send(struct calcomp *cc, const char *cmd)
{
	int err;

	for (; *cmd; cmd++) {
		err = serio_write(cc->serio, *cmd);
		if (err)
			return err;
	}
	return 0;
}

static int calcomp_send_list(struct calcomp *cc, const char * const *cmds)
{
	int err;

	for (; *cmds; cmds++) {
		err = calcomp_send(cc, *cmds);
		if (err)
			return err;
		msleep(cmd_delay_ms);
	}
	return 0;
}

/*
 * Send a query and collect the reply: either a text line (up to '\n') or a
 * fixed number of binary bytes. Returns the reply length or a negative error.
 */
static int calcomp_query(struct calcomp *cc, const char *cmd, bool text,
			 unsigned int want, u8 *buf, size_t size)
{
	unsigned long flags;
	size_t len;
	int err;

	reinit_completion(&cc->reply_done);
	spin_lock_irqsave(&cc->lock, flags);
	cc->reply_len = 0;
	cc->capture_skip = 0;
	cc->reply_want = want;
	cc->capture_text = text;
	cc->capturing = true;
	spin_unlock_irqrestore(&cc->lock, flags);

	err = calcomp_send(cc, cmd);
	if (!err && !wait_for_completion_timeout(&cc->reply_done,
				msecs_to_jiffies(CALCOMP_REPLY_TIMEOUT_MS)))
		err = -ETIMEDOUT;

	spin_lock_irqsave(&cc->lock, flags);
	cc->capturing = false;
	len = min_t(size_t, cc->reply_len, size);
	memcpy(buf, cc->reply, len);
	spin_unlock_irqrestore(&cc->lock, flags);

	return err ? err : len;
}

/* Copy a text reply into a printable, NUL-terminated string. */
static void calcomp_reply_to_string(const u8 *buf, int len, char *str, size_t size)
{
	size_t n = 0;
	int i;

	for (i = 0; i < len && n + 1 < size; i++) {
		if (buf[i] == '\r' || buf[i] == '\n')
			break;
		str[n++] = (buf[i] >= 0x20 && buf[i] < 0x7f) ? buf[i] : '?';
	}
	str[n] = '\0';
}

static void calcomp_log_id(struct calcomp *cc, const char *what, const char *cmd)
{
	u8 buf[CALCOMP_REPLY_MAX];
	char str[CALCOMP_REPLY_MAX];
	int len;

	len = calcomp_query(cc, cmd, true, 0, buf, sizeof(buf));
	if (len > 0) {
		calcomp_reply_to_string(buf, len, str, sizeof(str));
		dev_info(&cc->serio->dev, "%s: %s\n", what, str);
	} else {
		dev_info(&cc->serio->dev, "no reply to %s query (%d)\n", what, len);
	}
	msleep(cmd_delay_ms);
}

/*
 * The size reply has the same shape as a coordinate packet, so one from a
 * moving tool can be taken for it. Accept a size only once two consecutive
 * replies agree (with increment 1 a moving tool never repeats a position).
 */
#define CALCOMP_MAX_QUERIES	4

static bool calcomp_query_max(struct calcomp *cc, int *mx, int *my)
{
	u8 buf[CALCOMP_PACKET_LEN];
	int x, y, prev_x = -1, prev_y = -1, i, len;

	for (i = 0; i < CALCOMP_MAX_QUERIES; i++) {
		len = calcomp_query(cc, "\x1b%VS\r", false, CALCOMP_PACKET_LEN,
				    buf, sizeof(buf));
		msleep(cmd_delay_ms);
		if (len != CALCOMP_PACKET_LEN || !calcomp_decode_max(buf, &x, &y)) {
			prev_x = prev_y = -1;
			continue;
		}
		if (x == prev_x && y == prev_y) {
			*mx = x;
			*my = y;
			return true;
		}
		prev_x = x;
		prev_y = y;
	}
	return false;
}

static void calcomp_setup_tablet(struct calcomp *cc)
{
	struct device *dev = &cc->serio->dev;
	int mx, my;

	cc->max_x = max_x ?: CALCOMP_DEFAULT_MAX_X;
	cc->max_y = max_y ?: CALCOMP_DEFAULT_MAX_Y;

	if (!send_init) {
		dev_info(dev, "not sending setup commands; tablet must already be streaming format 23\n");
		return;
	}

	if (reset_on_attach) {
		if (calcomp_send_list(cc, calcomp_reset_cmds))
			dev_warn(dev, "failed to send reset commands\n");
		msleep(CALCOMP_RESET_DELAY_MS);
	}

	if (calcomp_send_list(cc, calcomp_setup_cmds)) {
		dev_warn(dev, "failed to send setup commands\n");
		return;
	}

	calcomp_log_id(cc, "firmware", "\x1b%__V\r");
	calcomp_log_id(cc, "product", "\x1b%__p\r");

	if (!max_x || !max_y) {
		if (calcomp_query_max(cc, &mx, &my)) {
			if (!max_x)
				cc->max_x = mx;
			if (!max_y)
				cc->max_y = my;
		} else {
			dev_warn(dev, "no valid size reply from tablet, using %dx%d\n",
				 cc->max_x, cc->max_y);
		}
	}

	if (calcomp_send_list(cc, calcomp_stream_cmds))
		dev_warn(dev, "failed to send stream commands\n");
}

static int calcomp_connect(struct serio *serio, struct serio_driver *drv)
{
	struct calcomp *cc;
	struct input_dev *input;
	unsigned long flags;
	int i, err;

	cc = kzalloc(sizeof(*cc), GFP_KERNEL);
	input = input_allocate_device();
	if (!cc || !input) {
		err = -ENOMEM;
		goto fail_free;
	}

	cc->serio = serio;
	cc->dev = input;
	cc->tool = CALCOMP_TOOL_PEN;
	spin_lock_init(&cc->lock);
	init_completion(&cc->reply_done);
	timer_setup(&cc->prox_timer, calcomp_prox_timer, 0);

	for (i = 0; i < CALCOMP_PUCK_BUTTONS; i++) {
		const int *def = tablet_mode ? calcomp_tablet_puck_keys : calcomp_mouse_puck_keys;
		int code = puck_keys[i];

		if (!code) {
			code = def[i];
		} else if (code < 0 || code > KEY_MAX ||
			   (!tablet_mode && code >= BTN_DIGI && code <= BTN_TOOL_QUADTAP)) {
			/* BTN_TOOL_*, BTN_TOUCH, BTN_STYLUS* would make the mouse a tablet */
			dev_warn(&serio->dev, "puck_keys[%d]=%d not usable in this mode, using default\n",
				 i, code);
			code = def[i];
		}
		cc->keymap[i] = code;
	}

	snprintf(cc->phys, sizeof(cc->phys), "%s/input0", serio->phys);
	input->name = "CalComp DrawingBoard III";
	input->phys = cc->phys;
	input->id.bustype = BUS_RS232;
	input->id.vendor = 0;
	input->id.product = SERIO_CALCOMP;
	input->id.version = 0x0100;
	input->dev.parent = &serio->dev;

	if (tablet_mode) {
		input_set_capability(input, EV_KEY, BTN_TOOL_PEN);
		cc->puck_tool_key = puck_as_mouse ? BTN_TOOL_MOUSE : BTN_TOOL_PEN;
		input_set_capability(input, EV_KEY, cc->puck_tool_key);
		input_set_capability(input, EV_KEY, BTN_TOUCH);
		input_set_capability(input, EV_KEY, BTN_STYLUS);
		input_set_capability(input, EV_KEY, BTN_STYLUS2);
		input_set_capability(input, EV_KEY, BTN_LEFT);		/* tap_click */
		input_set_capability(input, EV_MSC, MSC_SERIAL);
	} else {
		input_set_capability(input, EV_KEY, BTN_LEFT);
		input_set_capability(input, EV_KEY, BTN_MIDDLE);
		input_set_capability(input, EV_KEY, BTN_RIGHT);
	}
	for (i = 0; i < CALCOMP_PUCK_BUTTONS; i++)
		input_set_capability(input, EV_KEY, cc->keymap[i]);

	serio_set_drvdata(serio, cc);

	err = serio_open(serio, drv);
	if (err)
		goto fail_drvdata;

	calcomp_setup_tablet(cc);

	input_set_abs_params(input, ABS_X, 0, cc->max_x - 1, 0, 0);
	input_set_abs_params(input, ABS_Y, 0, cc->max_y - 1, 0, 0);
	input_abs_set_res(input, ABS_X, CALCOMP_RES_PER_MM);
	input_abs_set_res(input, ABS_Y, CALCOMP_RES_PER_MM);

	err = input_register_device(input);
	if (err)
		goto fail_close;

	spin_lock_irqsave(&cc->lock, flags);
	cc->ready = true;
	spin_unlock_irqrestore(&cc->lock, flags);

	dev_info(&serio->dev, "CalComp DrawingBoard III, %dx%d counts, on %s\n",
		 cc->max_x, cc->max_y, serio->phys);
	return 0;

fail_close:
	serio_close(serio);
fail_drvdata:
	serio_set_drvdata(serio, NULL);
	timer_delete_sync(&cc->prox_timer);
fail_free:
	input_free_device(input);
	kfree(cc);
	return err;
}

static void calcomp_disconnect(struct serio *serio)
{
	struct calcomp *cc = serio_get_drvdata(serio);

	/* No more interrupts after this, so the timer can't be re-armed. */
	serio_close(serio);
	timer_delete_sync(&cc->prox_timer);

	dev_dbg(&serio->dev, "%lu resyncs, %lu stray bytes\n", cc->resyncs, cc->stray);

	input_unregister_device(cc->dev);
	serio_set_drvdata(serio, NULL);
	kfree(cc);
}

static const struct serio_device_id calcomp_serio_ids[] = {
	{
		.type	= SERIO_RS232,
		.proto	= SERIO_CALCOMP,
		.id	= SERIO_ANY,
		.extra	= SERIO_ANY,
	},
	{ 0 }
};
MODULE_DEVICE_TABLE(serio, calcomp_serio_ids);

static struct serio_driver calcomp_drv = {
	.driver		= {
		.name	= "calcomp_drv",
	},
	.description	= DRIVER_DESC,
	.id_table	= calcomp_serio_ids,
	.interrupt	= calcomp_interrupt,
	.connect	= calcomp_connect,
	.disconnect	= calcomp_disconnect,
};

module_serio_driver(calcomp_drv);

MODULE_DESCRIPTION(DRIVER_DESC);
MODULE_LICENSE("GPL");
