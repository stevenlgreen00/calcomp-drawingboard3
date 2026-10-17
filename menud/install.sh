#!/bin/bash
# Install calcomp-menud for the current user (no root needed):
#   ~/.local/lib/calcomp-menud/           the program, README, example layout
#   ~/.local/bin/calcomp-menud            command (run / measure / overlay / check)
#   ~/.config/systemd/user/calcomp-menud.service
#   ~/.config/calcomp/areas.toml          only if you don't have one yet
#
#   menud/install.sh            # install / update, then: systemctl --user enable --now calcomp-menud
#   menud/install.sh --remove   # stop and remove (keeps your areas.toml)
# The root part (udev rule, driver area) is install-root.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HOME/.local/lib/calcomp-menud"
BIN="$HOME/.local/bin/calcomp-menud"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/calcomp-menud.service"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/calcomp/areas.toml"

if [[ $EUID -eq 0 ]]; then
	echo "run this as yourself, not root (the root part is install-root.sh)" >&2
	exit 1
fi

if [[ "${1:-}" == "--remove" ]]; then
	systemctl --user disable --now calcomp-menud.service 2>/dev/null || true
	rm -f "$UNIT" "$BIN"
	rm -rf "$LIB"
	systemctl --user daemon-reload
	echo "removed (kept $CFG)"
	exit 0
fi

install -d "$LIB" "$(dirname "$BIN")" "$(dirname "$UNIT")" "$(dirname "$CFG")"
install -m 755 "$HERE/calcomp_menud.py" "$LIB/"
install -m 644 "$HERE/keycodes.py" "$HERE/README.md" "$HERE/areas.example.toml" \
	"$HERE/71-calcomp-menud.rules" "$LIB/"
ln -sf "$LIB/calcomp_menud.py" "$BIN"
install -m 644 "$HERE/calcomp-menud.service" "$UNIT"
if [[ ! -e $CFG ]]; then
	install -m 644 "$HERE/areas.example.toml" "$CFG"
	echo "created $CFG from the example"
fi
systemctl --user daemon-reload
if systemctl --user -q is-active calcomp-menud.service; then
	systemctl --user restart calcomp-menud.service
	echo "restarted calcomp-menud"
fi
"$BIN" -c "$CFG" check || true
echo
echo "start it now and at every login:  systemctl --user enable --now calcomp-menud"
