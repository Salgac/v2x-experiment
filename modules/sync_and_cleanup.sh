#!/usr/bin/env bash
#
# sync_and_cleanup.sh — transfer all logs to the lab server via rsync, and
# ONLY delete local copies once the transfer is verified. Safe to re-run:
# already-synced files won't be re-sent, and nothing is deleted unless a
# second, independent check confirms the remote side matches.
#
# If the server isn't reachable right now (no network at this exact
# moment), this exits without deleting anything -- logs stay in place to
# be retried on the next opportunity, rather than being lost.
#
# Configure via environment variables, or edit the defaults below.
#
set -u

PROJECT_DIR="${PROJECT_DIR:-$HOME/v2x-experiment}"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

SERVER="${SYNC_SERVER:?Set SYNC_SERVER in .env (lab server hostname or IP)}"
REMOTE_USER="${SYNC_USER:-v2x}"
REMOTE_PATH="${SYNC_REMOTE_PATH:-/data/v2x-experiment}/$(hostname)"
SSH_KEY="${SYNC_SSH_KEY:-$HOME/.ssh/id_ed25519_labserver}"
LOG_DIR="${SYNC_LOG_DIR:-$PROJECT_DIR/logs}"

# Sync's own operational log lives OUTSIDE the directory being synced/wiped
# -- otherwise we'd be deleting the record of the deletion mid-run.
SYNC_HISTORY_DIR="${SYNC_HISTORY_DIR:-$PROJECT_DIR/sync_history}"
mkdir -p "$SYNC_HISTORY_DIR"
SYNC_LOG="$SYNC_HISTORY_DIR/sync_$(date -u +%Y%m%d_%H%M%S).log"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$SYNC_LOG"; }

log "Starting sync: $LOG_DIR -> $REMOTE_USER@$SERVER:$REMOTE_PATH"

# Reachability check first -- fail fast rather than let rsync hang if
# there's genuinely no network path right now (bus out of WiFi/cell range).
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes \
        "$REMOTE_USER@$SERVER" true 2>>"$SYNC_LOG"; then
    log "Cannot reach $SERVER right now -- leaving logs in place for next attempt."
    exit 1
fi

ssh -i "$SSH_KEY" "$REMOTE_USER@$SERVER" "mkdir -p '$REMOTE_PATH'" 2>>"$SYNC_LOG"

log "Running rsync..."
if rsync -av --checksum -e "ssh -i $SSH_KEY" "$LOG_DIR"/ "$REMOTE_USER@$SERVER:$REMOTE_PATH/" \
        >>"$SYNC_LOG" 2>&1; then
    log "rsync completed."
else
    log "rsync FAILED -- not deleting anything. See $SYNC_LOG for details."
    exit 1
fi

# Second, independent check: a checksum dry-run with zero pending
# differences is real confirmation the remote side matches, not just
# "rsync didn't error."
log "Verifying remote matches local before deleting..."
pending="$(rsync -a --checksum --dry-run -e "ssh -i $SSH_KEY" \
    "$LOG_DIR"/ "$REMOTE_USER@$SERVER:$REMOTE_PATH/" 2>>"$SYNC_LOG" | grep -c '^>' || true)"

if [ "${pending:-1}" -ne 0 ]; then
    log "Verification found differences ($pending) -- NOT deleting. See $SYNC_LOG."
    exit 1
fi

log "Verified. Deleting local log files (directory structure kept)..."
find "$LOG_DIR" -type f -delete
log "Done."
exit 0