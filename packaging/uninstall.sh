#!/bin/bash
# Remove everything packaging/install.sh installed.
#   sudo packaging/uninstall.sh
set -uo pipefail

VERSION=0.1
NAME=calcomp_drv

if [[ $EUID -ne 0 ]]; then
	echo "run as root: sudo $0" >&2
	exit 1
fi

systemctl stop 'calcomp-attach@*.service' 2>/dev/null
rm -f /etc/udev/rules.d/99-calcomp-drawingboard.rules \
	/etc/systemd/system/calcomp-attach@.service \
	/usr/local/sbin/calcomp-attach \
	/etc/modules-load.d/calcomp-drawingboard.conf
systemctl daemon-reload
udevadm control --reload

if [[ -d /sys/module/$NAME ]]; then
	rmmod "$NAME"
fi
if command -v dkms >/dev/null && [[ -n "$(dkms status -m "$NAME" -v "$VERSION" 2>/dev/null)" ]]; then
	dkms remove -m "$NAME" -v "$VERSION" --all
fi
rm -rf "/usr/src/$NAME-$VERSION"
# a --no-dkms install
find /lib/modules -path '*/updates/*' -name "$NAME.ko*" -delete 2>/dev/null
depmod -a

echo "Removed. (serport stays loaded until reboot; it's a standard kernel module.)"
