#!/usr/bin/env bash
#
# Install rackdash on a Raspberry Pi (or any Debian/Ubuntu host).
#
#   ./install.sh --pve https://192.168.1.10:8006 \
#                --token-id 'root@pam!rackdash' --token-secret <uuid> \
#                --fanctl http://192.168.1.10:8899 --kiosk
#
#   --kiosk        also install the full-screen browser service on tty1
#   --port 8080    listen on a different port
#   --node pve     pin a node name (default: the first one the API reports)
#
set -euo pipefail

PREFIX=/opt/rackdash
CONFIG_DIR=/etc/rackdash
CONFIG=$CONFIG_DIR/config.json
UNIT=/etc/systemd/system/rackdash.service
KIOSK_UNIT=/etc/systemd/system/rackdash-kiosk.service
SERVICE=rackdash
RUN_USER=rackdash
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT=8080
PVE_HOST=""
PVE_NODE=""
TOKEN_ID=""
TOKEN_SECRET=""
FANCTL_URL=""
WITH_KIOSK=0
KIOSK_USER=""

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pve|--pve-host)  PVE_HOST="${2:?--pve needs a URL}"; shift 2 ;;
    --node)            PVE_NODE="${2:?--node needs a name}"; shift 2 ;;
    --token-id)        TOKEN_ID="${2:?--token-id needs a value}"; shift 2 ;;
    --token-secret)    TOKEN_SECRET="${2:?--token-secret needs a value}"; shift 2 ;;
    --fanctl)          FANCTL_URL="${2:?--fanctl needs a URL}"; shift 2 ;;
    --port)            PORT="${2:?--port needs a value}"; shift 2 ;;
    --kiosk)           WITH_KIOSK=1; shift ;;
    --kiosk-user)      KIOSK_USER="${2:?--kiosk-user needs a name}"; WITH_KIOSK=1; shift 2 ;;
    -h|--help)         sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run as root (sudo ./install.sh ...)"

# ---------------------------------------------------------------- prerequisites

command -v python3 >/dev/null || die "python3 is required (apt install python3)"
python3 - <<'PY' || die "python 3.9 or newer is required"
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
info "python: $(python3 --version)"
command -v systemctl >/dev/null || die "systemd is required"

# ------------------------------------------------------------------- service user

if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  info "creating the $RUN_USER system user"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
fi

# vcgencmd is how the GPU load figure is read on a Pi, and it needs the video
# group. Harmless everywhere else; the reader just reports no GPU source.
if getent group video >/dev/null 2>&1; then
  usermod -aG video "$RUN_USER" 2>/dev/null || warn "could not add $RUN_USER to the video group"
fi

# ------------------------------------------------------------------------ files

info "installing to $PREFIX"
install -d -m 0755 "$PREFIX"
rm -rf "$PREFIX/rackdash" "$PREFIX/web" "$PREFIX/tools"
cp -r "$SOURCE_DIR/rackdash" "$SOURCE_DIR/web" "$SOURCE_DIR/tools" "$PREFIX/"
cp "$SOURCE_DIR/README.md" "$PREFIX/" 2>/dev/null || true
find "$PREFIX" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

install -d -m 0750 -o "$RUN_USER" -g "$RUN_USER" "$CONFIG_DIR"

# ----------------------------------------------------------------------- config
#
# The token is written here and nowhere else: it is deliberately not settable
# through the web UI, so anything that reaches the dashboard port cannot point
# the daemon at another server or read the secret back.

info "writing $CONFIG"
PYTHONPATH="$PREFIX" python3 - "$CONFIG" <<PY
import sys
sys.path.insert(0, "$PREFIX")
from rackdash import config as c

path = sys.argv[1]
try:
    cfg = c.load(path)
except RuntimeError:
    cfg = c.default_config()

for key, value in (("host", "$PVE_HOST"), ("node", "$PVE_NODE"),
                   ("token_id", "$TOKEN_ID"), ("token_secret", "$TOKEN_SECRET")):
    if value:
        cfg["proxmox"][key] = value
if "$FANCTL_URL":
    cfg["fanctl"]["url"] = "$FANCTL_URL"
cfg["http"]["port"] = int("$PORT")
c.save(path, cfg)
print("  proxmox:", cfg["proxmox"]["host"] or "(not set)")
print("  fanctl: ", cfg["fanctl"]["url"] or "(not set)")
PY

chown "$RUN_USER:$RUN_USER" "$CONFIG"
chmod 0600 "$CONFIG"

# ---------------------------------------------------------------------- service

info "installing the systemd unit"
install -m 0644 "$SOURCE_DIR/systemd/rackdash.service" "$UNIT"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 2

if ! systemctl is-active --quiet "$SERVICE"; then
  warn "the service did not start; recent log:"
  journalctl -u "$SERVICE" -n 25 --no-pager || true
  exit 1
fi
info "service is running"

# ------------------------------------------------------------------------ kiosk

if [[ $WITH_KIOSK -eq 1 ]]; then
  if [[ -z "$KIOSK_USER" ]]; then
    # The console user owns the screen; prefer the classic Pi account, else the
    # first ordinary login account on the box.
    for candidate in pi "$(logname 2>/dev/null || true)" \
                     "$(getent passwd 1000 | cut -d: -f1)"; do
      if [[ -n "$candidate" ]] && id -u "$candidate" >/dev/null 2>&1; then
        KIOSK_USER="$candidate"; break
      fi
    done
  fi
  [[ -n "$KIOSK_USER" ]] || die "could not work out the desktop user; pass --kiosk-user"

  BROWSER=""
  for candidate in chromium-browser chromium; do
    if command -v "$candidate" >/dev/null; then BROWSER="$(command -v "$candidate")"; break; fi
  done

  if ! command -v cage >/dev/null || [[ -z "$BROWSER" ]]; then
    warn "kiosk needs 'cage' and 'chromium'. Install them and re-run:"
    warn "  apt install -y cage chromium-browser"
  else
    info "installing the kiosk service for user $KIOSK_USER"
    KIOSK_UID="$(id -u "$KIOSK_USER")"
    sed -e "s|__KIOSK_USER__|$KIOSK_USER|g" \
        -e "s|__KIOSK_UID__|$KIOSK_UID|g" \
        -e "s|__BROWSER__|$BROWSER|g" \
        -e "s|__PORT__|$PORT|g" \
        "$SOURCE_DIR/systemd/rackdash-kiosk.service" > "$KIOSK_UNIT"
    chmod 0644 "$KIOSK_UNIT"
    systemctl daemon-reload
    systemctl enable rackdash-kiosk >/dev/null
    systemctl restart rackdash-kiosk || warn "kiosk did not start; see journalctl -u rackdash-kiosk"
    info "kiosk enabled on tty1"
  fi
fi

# ------------------------------------------------------------------------- done

ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$ADDR" ]] || ADDR=127.0.0.1
echo
echo "  Screen:  http://127.0.0.1:${PORT}/   (on this Pi)"
echo "  Config:  $CONFIG"
echo "  Logs:    journalctl -u $SERVICE -f"
echo "  Check:   sudo -u $RUN_USER python3 -m rackdash --config $CONFIG --check"
echo
# Ask the saved config, not the flags: re-running the installer to pick up new
# code passes no flags, and warning "no token configured" at someone whose
# token is sitting in the config file is simply wrong.
HAVE_TOKEN=$(python3 -c '
import json, sys
try:
    pve = (json.load(open(sys.argv[1])) or {}).get("proxmox") or {}
    print(1 if pve.get("host") and pve.get("token_secret") else 0)
except Exception:
    print(0)
' "$CONFIG" 2>/dev/null || echo 0)

if [[ "$HAVE_TOKEN" != "1" ]]; then
  warn "No Proxmox token configured yet. On the Proxmox host run:"
  echo "    pveum user token add root@pam rackdash --privsep 0"
  echo "  then re-run this installer with --pve/--token-id/--token-secret."
else
  echo "  The dashboard binds 127.0.0.1 by default. To view it from another"
  echo "  machine, set http.bind to 0.0.0.0 in the config and restart."
  echo "  This Pi is ${ADDR}."
fi
