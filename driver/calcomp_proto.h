/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright (C) 2026 Steven Green
 *
 * CalComp DrawingBoard III binary protocol (output format 23, the tablet's
 * Default setup). Pure decoding, no kernel dependencies, so the same code can
 * be unit-tested in userspace against captured packets (see test/).
 *
 * Layout of the 6-byte packet (GTCO "DrawingBoard III Commands", format 23,
 * confirmed on a model 34180, see NOTES.md):
 *
 *   B1: 1 | C4 | C3 C2 C1 C0 | X15 X14   cursor code C4..C0:
 *                                        C4 = 1: puck button down, C3..C0 = number 0-15
 *                                        C4 = 0: C2..C0 = pen upper/lower/tip
 *   B2: 0 | X13..X7
 *   B3: 0 | X6..X0
 *   B4: 0 | 0 | PR | X17 X16 | Y16 Y15 Y14   PR = 1: out of proximity
 *   B5: 0 | Y13..Y7
 *   B6: 0 | Y6..Y0
 *
 * Only the first byte has bit 7 set, which is how packets are framed. PR is
 * only ever set if "send data out of proximity" is on, which it isn't in the
 * Default setup: the tablet simply stops sending when the tool leaves.
 */
#ifndef _CALCOMP_PROTO_H
#define _CALCOMP_PROTO_H

#define CALCOMP_PACKET_LEN	6
#define CALCOMP_SYNC		0x80
#define CALCOMP_PUCK_BUTTONS	16

#define CALCOMP_PEN_TIP		0x01
#define CALCOMP_PEN_LOWER	0x02
#define CALCOMP_PEN_UPPER	0x04

enum calcomp_tool {
	CALCOMP_TOOL_UNKNOWN,	/* no button down: pen and puck look the same */
	CALCOMP_TOOL_PEN,
	CALCOMP_TOOL_PUCK,
};

struct calcomp_packet {
	int x, y;			/* raw tablet coordinates, origin bottom-left */
	enum calcomp_tool tool;
	unsigned int pen_buttons;	/* CALCOMP_PEN_* bitmask */
	int puck_button;		/* 0-15, or -1 if none */
};

static inline void calcomp_decode_packet(const u8 *p, struct calcomp_packet *r)
{
	r->x = (((p[3] >> 3) & 0x03) << 16) | ((p[0] & 0x03) << 14) | (p[1] << 7) | p[2];
	r->y = ((p[3] & 0x07) << 14) | (p[4] << 7) | p[5];
	r->pen_buttons = 0;
	r->puck_button = -1;

	if (p[0] & 0x40) {
		r->tool = CALCOMP_TOOL_PUCK;
		r->puck_button = (p[0] >> 2) & 0x0f;
	} else if (p[0] & 0x1c) {
		r->tool = CALCOMP_TOOL_PEN;
		r->pen_buttons = (p[0] >> 2) & 0x07;
	} else {
		r->tool = CALCOMP_TOOL_UNKNOWN;
	}
}

/*
 * Reply to ESC%VS (maximum coordinates). Same 7-bit packing as a packet.
 * Returns 1 if the reply looks valid.
 */
static inline int calcomp_decode_max(const u8 *p, int *max_x, int *max_y)
{
	int i;

	if (!(p[0] & CALCOMP_SYNC))
		return 0;
	for (i = 1; i < CALCOMP_PACKET_LEN; i++)
		if (p[i] & CALCOMP_SYNC)
			return 0;

	*max_x = (((p[3] >> 3) & 0x03) << 16) | ((p[0] & 0x03) << 14) | (p[1] << 7) | p[2];
	*max_y = ((p[3] & 0x07) << 14) | (p[4] << 7) | p[5];

	/* smallest tablet is 12" at >= 200 lpi; fields are at most 18 bits */
	return *max_x >= 1000 && *max_y >= 1000 &&
	       *max_x <= 0x3ffff && *max_y <= 0x3ffff;
}

#endif /* _CALCOMP_PROTO_H */
