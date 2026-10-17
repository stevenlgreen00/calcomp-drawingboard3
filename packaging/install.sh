#!/bin/bash
# Install the CalComp DrawingBoard III driver so it works automatically:
#   - calcomp_drv.ko via DKMS (rebuilt for every new kernel)
#   - the attach helper as /usr/local/sbin/calcomp-attach
#   - a systemd service that udev starts when the USB-serial adapter appears
#
#   sudo packaging/install.sh            # needs: apt install dkms linux-headers-$(uname -r)
#   sudo packaging/install.sh --no-dkms  # plain install; must be re-run after kernel updates
set -euo pipefail

VERSION=0.1
NAME=calcomp_drv
HERE="$(cd "$(dirname "$0")" && pwd)"
TOP="$(dirname "$HERE")"
SRC="/usr/src/$NAME-$VERSION"

use_dkms=1
if [[ "${1:-}" == "--no-dkms" ]]; then
	use_dkms=0
fi

if [[ $EUID -ne 0 ]]; then
	echo "run as root: sudo $0 $*" >&2
	exit 1
fi
if [[ $use_dkms -eq 1 ]] && ! command -v dkms >/dev/null; then
	echo "dkms is not installed. Install it (sudo apt install dkms) or use --no-dkms." >&2
	exit 1
fi
if [[ ! -d /lib/modules/$(uname -r)/build ]]; then
	echo "kernel headers missing: sudo apt install linux-headers-$(uname -r)" >&2
	exit 1
fi

echo "== stopping any running attach and unloading the old module"
systemctl stop 'calcomp-attach@*.service' 2>/dev/null || true
if pkill -f 'calcomp_attach.py|calcomp-attach' 2>/dev/null; then
	sleep 1
fi
# (not "lsmod | grep -q": with pipefail, grep exiting early makes the check fail)
if [[ -d /sys/module/$NAME ]]; then
	rmmod "$NAME"
fi

echo "== installing the kernel module"
if [[ $use_dkms -eq 1 ]]; then
	if [[ -n "$(dkms status -m "$NAME" -v "$VERSION" 2>/dev/null)" ]]; then
		dkms remove -m "$NAME" -v "$VERSION" --all
	fi
	rm -rf "$SRC"
	install -d "$SRC"
	install -m 644 "$TOP/driver/calcomp_drv.c" "$TOP/driver/calcomp_proto.h" \
		"$TOP/driver/Kbuild" "$TOP/driver/dkms.conf" "$SRC/"
	dkms add -m "$NAME" -v "$VERSION"
	dkms install -m "$NAME" -v "$VERSION"
else
	make -C "$TOP/driver" modules
	make -C "$TOP/driver" install
	echo "   NOTE: without DKMS, re-run this script after every kernel update."
fi

echo "== installing the attach helper and service"
install -m 755 "$TOP/tools/calcomp_attach.py" /usr/local/sbin/calcomp-attach
install -m 644 "$HERE/calcomp-attach@.service" /etc/systemd/system/
install -m 644 "$HERE/99-calcomp-drawingboard.rules" /etc/udev/rules.d/
echo serport > /etc/modules-load.d/calcomp-drawingboard.conf

systemctl daemon-reload
udevadm control --reload

echo "== starting it for adapters that are already plugged in"
modprobe serport
modprobe "$NAME"
for dev in /sys/class/tty/ttyUSB*; do
	if [[ -e $dev ]]; then
		udevadm trigger --action=add "$dev"
	fi
done
# setup takes ~6 s (reset, ~10 commands, ID and size queries)
for _ in $(seq 15); do
	grep -q "CalComp DrawingBoard" /proc/bus/input/devices && break
	sleep 1
done

echo
systemctl --no-pager --lines=0 status 'calcomp-attach@*.service' || true
echo
if grep -q "CalComp DrawingBoard" /proc/bus/input/devices; then
	echo "OK: the tablet is attached:"
	grep -A5 "CalComp DrawingBoard" /proc/bus/input/devices | grep -E "Phys|Handlers"
else
	echo "The tablet is not attached yet. Is the adapter plugged in? Check:"
	echo "  journalctl -u 'calcomp-attach@*' -b ; dmesg | grep -i calcomp"
fi
