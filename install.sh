#!/usr/bin/env bash
#
# install.sh — one-time setup: installs both systemd services so logging
# (main.py) and the UPS power-loss monitor start automatically on every
# boot, survive crashes (auto-restart), and don't need a login session.
#
# Run this AFTER everything already works manually:
#   - main.py runs fine standalone
#   - ups_monitor.py --test showed correct charge/discharge readings
#   - .env is filled in with real values
#   - modules/ups/enable_autoboot.py has already been run once (separately
#     -- NOT part of this script, since it has its own one-time
#     power-cycle requirement, see its own docstring)
#
# Usage:
#   sudo ./install.sh
#
set -e

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_USER="${SUDO_USER:-$(whoami)}"

echo "Project directory: $PROJECT_DIR"
echo "Installing for user: $REAL_USER"
echo

# --- sanity checks before installing anything -------------------------------
[ -f "$PROJECT_DIR/.env" ] || {
    echo "No .env found at $PROJECT_DIR/.env -- copy .env.example to .env"
    echo "and fill in real values first."
    exit 1
}
[ -f "$PROJECT_DIR/main.py" ] || { echo "main.py not found in $PROJECT_DIR"; exit 1; }
[ -f "$PROJECT_DIR/v2x-experiment.service.example" ] || { echo "Missing v2x-experiment.service.example"; exit 1; }
[ -f "$PROJECT_DIR/ups-monitor.service.example" ] || { echo "Missing ups-monitor.service.example"; exit 1; }

chmod +x "$PROJECT_DIR/modules/ups/ups_monitor.py" \
         "$PROJECT_DIR/modules/shutdown_sequence.sh" \
         "$PROJECT_DIR/modules/sync_and_cleanup.sh" 2>/dev/null || true

# --- generate the two systemd units with real paths/user substituted -------
echo "Writing /etc/systemd/system/v2x-experiment.service..."
sed -e "s#/home/dominik/v2x-experiment#$PROJECT_DIR#g" \
    -e "s#User=dominik#User=$REAL_USER#g" \
    "$PROJECT_DIR/v2x-experiment.service.example" > /etc/systemd/system/v2x-experiment.service

echo "Writing /etc/systemd/system/ups-monitor.service..."
sed -e "s#/home/dominik/v2x-experiment#$PROJECT_DIR#g" \
    "$PROJECT_DIR/ups-monitor.service.example" > /etc/systemd/system/ups-monitor.service

systemctl daemon-reload
systemctl enable v2x-experiment
systemctl enable ups-monitor

echo
echo "=================================================================="
echo "Done. Both services are enabled and will start automatically on"
echo "every future boot."
echo
echo "To start them right now instead of waiting for a reboot:"
echo "    sudo systemctl start v2x-experiment ups-monitor"
echo
echo "Check on them any time with:"
echo "    systemctl status v2x-experiment ups-monitor"
echo "    journalctl -u v2x-experiment -u ups-monitor -f"
echo
echo "To recreate everything cleanly (e.g. after moving the project"
echo "directory), just re-run this script."
echo "=================================================================="