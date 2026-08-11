#!/usr/bin/env bash
#
# shutdown_sequence.sh — the full graceful-shutdown pipeline: stop logging
# cleanly, sync logs to the lab server, delete local copies once verified,
# then power off.
#
# Triggered by modules/ups/ups_monitor.py once it detects the UPS HAT (D)
# has switched from charging to discharging (bus power lost). No separate
# signal is needed after shutdown -h now -- see that line below for why.
#
set -u
PROJECT_DIR="/home/dominik/v2x-experiment"
SHUTDOWN_LOG="$PROJECT_DIR/shutdown_sequence.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "$SHUTDOWN_LOG"; }

log "=== Shutdown sequence triggered ==="

log "Stopping main.py (SIGTERM -- lets it stop every module and close files cleanly)..."
pkill -TERM -f "python3.*main\.py" 2>/dev/null || true

log "Waiting for main.py to exit (up to 30s)..."
exited=0
for i in $(seq 1 30); do
    if ! pgrep -f "python3.*main\.py" >/dev/null; then
        log "main.py exited after ${i}s."
        exited=1
        break
    fi
    sleep 1
done
if [ "$exited" -ne 1 ]; then
    log "main.py still running after 30s -- forcing termination."
    pkill -KILL -f "python3.*main\.py" 2>/dev/null || true
    sleep 2
fi

log "Running sync and cleanup..."
if SYNC_SERVER="${SYNC_SERVER:-}" "$PROJECT_DIR/modules/sync_and_cleanup.sh" >>"$SHUTDOWN_LOG" 2>&1; then
    log "Sync and cleanup completed -- logs are on the server and cleared locally."
else
    log "Sync and cleanup did not complete (no network right now, or verification "
    log "failed) -- logs were left in place on the Pi, to retry on next opportunity."
fi

log "Re-arming UPS boot-on-power-connect (register may not persist across cycles)..."
python3 -c "
from smbus2 import SMBus
try:
    bus = SMBus(1)
    bus.write_byte_data(0x2D, 0x01, 0x55)
    val = bus.read_byte_data(0x2D, 0x01)
    print(f'Register 0x01 set, reads back as 0x{val:02X}')
except Exception as e:
    print(f'Could not re-arm auto-boot: {e}')
" 2>&1 | tee -a "$SHUTDOWN_LOG"

log "Powering off now."
sync
# No extra UPS-specific step needed here for the Waveshare UPS HAT (D):
# its MCU manages power state on its own -- once the Pi halts, the board
# just sits on battery at near-zero draw until either external power
# returns or its own low-voltage cutoff kicks in. `shutdown -h now` is
# the complete, correct final step for this board.
shutdown -h now