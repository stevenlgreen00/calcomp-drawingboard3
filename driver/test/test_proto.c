// SPDX-License-Identifier: GPL-2.0
/*
 * Userspace test for calcomp_proto.h: decodes every packet from the real
 * tablet captures (see make_vectors.py) and checks tool, buttons and
 * coordinates against what the capture step asked for.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned char u8;
#include "calcomp_proto.h"

#define MAX_BOX_STEPS 16

struct box_step {
	char name[64];
	int hits, total;
};

static struct box_step box_steps[MAX_BOX_STEPS];
static int n_box_steps;

static struct box_step *box_step(const char *name)
{
	int i;

	for (i = 0; i < n_box_steps; i++)
		if (!strcmp(box_steps[i].name, name))
			return &box_steps[i];
	if (n_box_steps == MAX_BOX_STEPS) {
		fprintf(stderr, "too many corner steps\n");
		exit(2);
	}
	snprintf(box_steps[n_box_steps].name, sizeof(box_steps[0].name), "%s", name);
	return &box_steps[n_box_steps++];
}

/* "1|2|3" -> is mask one of them? */
static int mask_allowed(const char *list, unsigned int mask)
{
	char buf[64], *tok, *save;

	snprintf(buf, sizeof(buf), "%s", list);
	for (tok = strtok_r(buf, "|", &save); tok; tok = strtok_r(NULL, "|", &save))
		if ((unsigned int)atoi(tok) == mask)
			return 1;
	return 0;
}

int main(int argc, char **argv)
{
	char hex[16], btn[32], step[64];
	int tool, x, y, xlo, xhi, ylo, yhi;
	int n = 0, fail = 0, i, max_x, max_y;
	FILE *f;

	if (argc != 2) {
		fprintf(stderr, "usage: %s vectors.txt\n", argv[0]);
		return 2;
	}
	f = fopen(argv[1], "r");
	if (!f) {
		perror(argv[1]);
		return 2;
	}

	while (fscanf(f, "%15s %d %31s %d %d %d %d %d %d %63s",
		      hex, &tool, btn, &x, &y, &xlo, &xhi, &ylo, &yhi, step) == 10) {
		struct calcomp_packet p;
		u8 pkt[CALCOMP_PACKET_LEN];
		int ok = 1;

		for (i = 0; i < CALCOMP_PACKET_LEN; i++) {
			unsigned int b;

			sscanf(hex + 2 * i, "%2x", &b);
			pkt[i] = b;
		}
		calcomp_decode_packet(pkt, &p);
		n++;

		if ((int)p.tool != tool)
			ok = 0;
		else if (tool == CALCOMP_TOOL_PUCK && p.puck_button != atoi(btn))
			ok = 0;
		else if (tool == CALCOMP_TOOL_PEN && !mask_allowed(btn, p.pen_buttons))
			ok = 0;
		else if (tool == CALCOMP_TOOL_UNKNOWN && (p.pen_buttons || p.puck_button != -1))
			ok = 0;
		if (p.x != x || p.y != y)
			ok = 0;
		if (p.x < 0 || p.x > 17999 || p.y < 0 || p.y > 11999)
			ok = 0;

		/* corner steps: some packets must land in the corner (the user moved there) */
		if (xlo != 0 || xhi != 17999 || ylo != 0 || yhi != 11999) {
			struct box_step *b = box_step(step);

			b->total++;
			if (p.x >= xlo && p.x <= xhi && p.y >= ylo && p.y <= yhi)
				b->hits++;
		}

		if (!ok) {
			fail++;
			if (fail <= 20)
				printf("FAIL %s %s: want tool %d btn %s x %d y %d, got tool %d pen %u puck %d x %d y %d\n",
				       step, hex, tool, btn, x, y, p.tool, p.pen_buttons,
				       p.puck_button, p.x, p.y);
		}
	}
	fclose(f);

	for (i = 0; i < n_box_steps; i++) {
		printf("corner %-14s %4d of %4d packets in the corner\n", box_steps[i].name,
		       box_steps[i].hits, box_steps[i].total);
		if (!box_steps[i].hits)
			fail++;
	}

	/* ESC%VS reply from the real tablet: 18000 x 12000 */
	{
		const u8 vs[] = { 0x81, 0x0c, 0x50, 0x20, 0x5d, 0x60 };
		const u8 bad[] = { 0x81, 0x0c, 0x90, 0x20, 0x5d, 0x60 };

		if (!calcomp_decode_max(vs, &max_x, &max_y) || max_x != 18000 || max_y != 12000) {
			printf("FAIL ESC%%VS decode: got %d x %d\n", max_x, max_y);
			fail++;
		}
		if (calcomp_decode_max(bad, &max_x, &max_y)) {
			printf("FAIL ESC%%VS reply with a sync byte inside was accepted\n");
			fail++;
		}
	}

	printf("%d packets, %d failures\n", n, fail);
	return fail ? 1 : (n ? 0 : 1);
}
