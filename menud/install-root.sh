#!/bin/bash
# The root part of the calcomp-menud setup (the daemon itself runs as you):
#   - udev rule: the desktop user may read the tablet and use /dev/uinput
#   - calcomp_drv reports the whole tablet: area_* = 0, now and at boot
#
#   sudo menud/install-root.sh            # install
#   sudo menud/install-root.sh --remove   # remove the rule, put the area back
#
# The area_* options in /etc/modprobe.d/calcomp-drawingboard.conf are
# commented out (not deleted), with a backup next to it; --remove restores them.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RULE=/etc/udev/rules.d/71-calcomp-menud.rules
CONF=/etc/modprobe.d/calcomp-drawingboard.conf
PARAMS=/sys/module/calcomp_drv/parameters
TAG="# calcomp-menud:"

if [[ $EUID -ne 0 ]]; then
	echo "run as root: sudo $0 $*" >&2
	exit 1
fi

retrigger() {
	udevadm control --reload
	udevadm trigger --action=change --subsystem-match=input
	udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
	udevadm settle
}

if [[ "${1:-}" == "--remove" ]]; then
	rm -f "$RULE"
	retrigger
	if [[ -f $CONF ]] && grep -q "^$TAG " "$CONF"; then
		sed -i "s/^$TAG //" "$CONF"
		echo "restored the area options in $CONF:"
		grep -v '^#' "$CONF" || true
		# apply them live too
		for kv in $(grep -o 'area_[xy][01]=[0-9]*' "$CONF"); do
			[[ -d $PARAMS ]] && echo "${kv#*=}" > "$PARAMS/${kv%%=*}"
		done
	fi
	echo "removed $RULE"
	exit 0
fi

echo "== udev rule $RULE"
install -m 644 "$HERE/71-calcomp-menud.rules" "$RULE"
udevadm verify "$RULE" 2>/dev/null || true
retrigger

echo "== calcomp_drv: report the whole tablet (area_* = 0)"
if [[ -f $CONF ]] && grep -Eq '^[^#]*area_[xy][01]=' "$CONF"; then
	cp -p "$CONF" "$CONF.bak"
	# Split the area options off their line and comment that line out, so
	# other options (if any) stay active.
	awk -v tag="$TAG" '
		/^[[:space:]]*options[[:space:]]+calcomp_drv/ && /area_[xy][01]=/ {
			area = "options calcomp_drv"; rest = "options calcomp_drv"; nrest = 0
			for (i = 3; i <= NF; i++) {
				if ($i ~ /^area_[xy][01]=/) area = area " " $i
				else { rest = rest " " $i; nrest++ }
			}
			print tag " " area
			if (nrest) print rest
			next
		}
		{ print }' "$CONF.bak" > "$CONF"
	echo "   $CONF (backup: $CONF.bak):"
	sed 's/^/     /' "$CONF"
else
	echo "   no area options in $CONF"
fi
if [[ -d $PARAMS ]]; then
	for p in area_x0 area_y0 area_x1 area_y1; do
		echo 0 > "$PARAMS/$p"
	done
	echo "   live: $(cd "$PARAMS" && grep . area_*| tr '\n' ' ')"
else
	echo "   calcomp_drv not loaded; it will start with the whole tablet"
fi

echo "== access check"
ev=$(awk -v RS= '/N: Name="CalComp DrawingBoard III"/' /proc/bus/input/devices |
	grep -o 'event[0-9]*' | head -1 || true)
if [[ -n $ev ]]; then
	getfacl -p "/dev/input/$ev" 2>/dev/null | grep '^user:' || echo "   (no user ACL on /dev/input/$ev: log in on the desktop seat)"
fi
getfacl -p /dev/uinput 2>/dev/null | grep '^user:' || true
echo
echo "Done. Without the daemon the pointer now maps the WHOLE tablet, faulty"
echo "strips included; 'sudo $0 --remove' puts the driver's area back."
