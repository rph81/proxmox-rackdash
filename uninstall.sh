#!/usr/bin/env bash
#
# Remove rackdash. The config is kept unless --purge is given.
#
#   ./uninstall.sh            remove the services and program files
#   ./uninstall.sh --purge    also delete /etc/rackdash and the service user
#
set -euo pipefail

PREFIX=/opt/rackdash
CONFIG_DIR=/etc/rackdash
UNIT=/etc/systemd/system/rackdash.service
KIOSK_UNIT=/etc/systemd/system/rackdash-kiosk.service
RUN_USER=rackdash
PURGE=0

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

[[ "${1:-}" == "--purge" ]] && PURGE=1
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

for unit in rackdash-kiosk rackdash; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${unit}.service"; then
    info "stopping $unit"
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
  fi
done

info "removing units"
rm -f "$UNIT" "$KIOSK_UNIT"
systemctl daemon-reload

info "removing $PREFIX"
rm -rf "$PREFIX"

if [[ $PURGE -eq 1 ]]; then
  info "removing $CONFIG_DIR (this deletes the stored API token)"
  rm -rf "$CONFIG_DIR"
  if id -u "$RUN_USER" >/dev/null 2>&1; then
    info "removing the $RUN_USER user"
    userdel "$RUN_USER" 2>/dev/null || true
  fi
else
  info "keeping your config at $CONFIG_DIR (use --purge to delete it)"
fi

echo
echo "Done. The screen will go blank at the next reboot; nothing else on this"
echo "Pi was changed."
