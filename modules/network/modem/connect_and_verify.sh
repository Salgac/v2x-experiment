#!/usr/bin/env bash
#
# connect_and_verify.sh — bring up a SIM8200-family modem against a
# commercial NSA network using QMI as the primary control path, and verify
# real connectivity before anything else (the AT-layer logger, main.py,
# etc.) is started against this modem.
#
# What this session established, baked in below:
#   - QMI "mode preference" (lte|5gnr), not just the AT-layer AT+CNMP, is
#     the setting that actually governs SA vs NSA acquisition. This part
#     is confirmed necessary and correct -- keep it.
#   - PLMN selection defaults to "automatic" here. Pin it manually only if
#     a specific SIM is observed drifting onto a neighboring/forbidden
#     PLMN (we saw this once with our test SIM) -- it's not something to
#     apply by default across different carriers on future modems.
#   - APN must be set per-operator/per-SIM -- there's no safe universal
#     default, confirm it for whichever SIM is in a given modem.
#   - is_connected() below does an EXACT status match, not a substring
#     match -- ue_connect.sh (the original private-core script) had a real
#     bug here (grep -qi "connected" also matches "disconnected").
#
# OPEN ISSUE as of this session: even with all of the above correctly set
# and confirmed via read-back, registration has not been achieved with our
# test SIM/modem -- qmicli --nas-get-system-info consistently shows
# LTE service Status: 'none' while 5G SA reaches 'limited' and stops.
# This looks like a firmware-level behavior (the modem structurally not
# attempting an LTE-anchored NSA attach despite being configured to allow
# it) rather than anything fixable via more AT/QMI settings -- see
# TROUBLESHOOTING.md (or the SIMCom support thread) before assuming this
# script alone will succeed on a similarly-behaving modem. It's still the
# right bring-up procedure to run and build on; the remaining gap is a
# vendor/firmware question, not a missing step here.
#
# Designed to be run once per modem, per boot, BEFORE main.py/net_logger.py
# start polling that modem -- this script's job is only to get the modem
# online and confirm it, not to log anything continuously.
#
# Usage (all overridable via env vars, for running against modem 2/3/4
# later once you have distinct AT/QMI/interface paths per modem):
#   sudo ./connect_and_verify.sh
#   sudo QMI_DEV=/dev/cdc-wdm1 IFACE=wwan1 AT_PORT_CANDIDATES="/dev/ttyUSB12 /dev/ttyUSB13" \
#        APN=some.other.apn ./connect_and_verify.sh
#
set -u

# ---------------------------------------------------------------------------
# Config -- override any of these via environment variables
# ---------------------------------------------------------------------------
QMI_DEV="${QMI_DEV:-/dev/cdc-wdm0}"
IFACE="${IFACE:-wwan0}"
AT_PORT_CANDIDATES="${AT_PORT_CANDIDATES:-/dev/ttyUSB5 /dev/ttyUSB4 /dev/ttyUSB3 /dev/ttyUSB2}"
MODE_PREF="${MODE_PREF:-lte|5gnr}"          # what QMI calls this modem's allowed RATs --
                                             # confirmed necessary this session (AT+CNMP alone
                                             # is not enough; this QMI-layer setting is the one
                                             # that actually gates SA vs NSA acquisition)
PLMN="${PLMN:-}"                            # blank = automatic selection (the sensible default
                                             # across different SIMs/carriers on modems 2-4).
                                             # Override per-modem only if a specific SIM is known
                                             # to need pinning (see session notes: our test SIM's
                                             # "automatic" mode was observed drifting onto a
                                             # neighboring forbidden PLMN at one point -- pin
                                             # manually, e.g. PLMN=23101, only if you see that
                                             # same symptom again on a given modem/SIM).
APN="${APN:-internet}"                      # set per-operator -- confirm the correct APN string
                                             # for whichever SIM is in each modem before running
REG_TIMEOUT="${REG_TIMEOUT:-120}"           # seconds to wait for registration after reset
START_RETRIES="${START_RETRIES:-5}"
PING_TARGET="${PING_TARGET:-8.8.8.8}"       # real internet target -- this is a commercial
                                             # connection now, not the private core's UPF gateway
AT_TMP="$(mktemp)"

log()  { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; cleanup; exit 1; }
cleanup() { rm -f "$AT_TMP" 2>/dev/null; }
trap cleanup EXIT

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo $0"
for bin in qmicli ip timeout ping python3; do
    command -v "$bin" >/dev/null 2>&1 || die "Missing required tool: $bin"
done
[ -e "$QMI_DEV" ] || die "QMI device $QMI_DEV not found."

# ---------------------------------------------------------------------------
# Minimal AT helper -- used only for a quick SIM sanity check. Everything
# else in this script is QMI-native.
# ---------------------------------------------------------------------------
AT_PORT=""
find_at_port() {
    for p in $AT_PORT_CANDIDATES; do
        [ -e "$p" ] || continue
        resp="$(python3 -c "
import serial, sys
try:
    ser = serial.Serial('$p', 115200, timeout=1)
    ser.write(b'AT\r\n')
    import time; time.sleep(0.3)
    data = ser.read(200)
    ser.close()
    print(b'OK' in data and 'OK' or '')
except Exception:
    print('')
" 2>/dev/null)"
        if [ "$resp" = "OK" ]; then
            AT_PORT="$p"
            return 0
        fi
    done
    return 1
}

at_query() {
    python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
ser.write(b'$1\r\n')
time.sleep(1)
print(ser.read(300).decode('ascii', errors='replace'))
"
}

# ===========================================================================
# 1. Release channels
# ===========================================================================
log "Releasing modem channels (ModemManager / simcom-cm)..."
systemctl stop ModemManager 2>/dev/null || true
pkill -f simcom-cm 2>/dev/null || true
sleep 1

# ===========================================================================
# 2. Quick SIM sanity check (AT layer, minimal)
# ===========================================================================
log "Locating AT port for a SIM check..."
if find_at_port; then
    log "AT port: $AT_PORT"
    cpin="$(at_query 'AT+CPIN?')"
    if echo "$cpin" | grep -q "READY"; then
        log "SIM ready."
    else
        die "SIM not READY: $cpin -- fix this before continuing (see unlock_sim.py)."
    fi
else
    warn "No AT port responded -- continuing on QMI alone, but worth investigating."
fi

# ===========================================================================
# 3. QMI: set mode preference + manual PLMN lock
# ===========================================================================
log "Setting QMI mode preference='$MODE_PREF', PLMN=${PLMN:-automatic}..."
if [ -n "${PLMN:-}" ]; then
    NET_SEL="manual=${PLMN}"
else
    NET_SEL="automatic"
fi
qmicli -p -d "$QMI_DEV" --nas-set-system-selection-preference="${MODE_PREF}",${NET_SEL} \
    || die "Failed to set system selection preference."

log "Resetting modem to apply (this reboots it)..."
qmicli -d "$QMI_DEV" --dms-set-operating-mode=reset || die "Reset failed."

log "Waiting for the modem to come back..."
sleep 15

# ===========================================================================
# 4. Poll for registration
# ===========================================================================
log "Waiting for registration (up to ${REG_TIMEOUT}s)..."
registered=0
deadline=$(( $(date +%s) + REG_TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    status="$(qmicli -d "$QMI_DEV" --nas-get-serving-system 2>/dev/null)"
    state="$(echo "$status" | grep -oP "Registration state: '\K[^']+")"
    forbidden="$(echo "$status" | grep -oP "Forbidden: '\K[^']+" | head -1)"
    echo "    state=$state forbidden=${forbidden:-?}"

    if [ "$state" = "registered" ]; then
        registered=1
        break
    fi
    if [ "$forbidden" = "yes" ]; then
        warn "Explicitly forbidden on current PLMN attempt -- this is a network-side"
        warn "rejection, not something more waiting will fix. Stopping the wait early."
        break
    fi
    sleep 5
done

if [ "$registered" -ne 1 ]; then
    err "Did not reach 'registered' within ${REG_TIMEOUT}s."
    echo "$status" | sed 's/^/    /'
    die "Not registered -- see status above. Not attempting a data call."
fi
log "Registered."
qmicli -d "$QMI_DEV" --nas-get-signal-info 2>/dev/null | sed 's/^/    /'

# ===========================================================================
# 5. Bring up the data session over QMI (APN passed natively here)
# ===========================================================================
is_connected() {
    # Exact status match -- NOT a substring match (that was the bug in
    # ue_connect.sh: grep -qi "connected" also matches "disconnected").
    qmicli -p -d "$QMI_DEV" --wds-get-packet-service-status 2>/dev/null \
        | grep -qE "Connection status: 'connected'"
}

log "Putting $IFACE into raw-ip mode..."
ip link set "$IFACE" down 2>/dev/null || true
[ -e "/sys/class/net/$IFACE/qmi/raw_ip" ] && echo Y > "/sys/class/net/$IFACE/qmi/raw_ip"

log "Dialing data session (apn=$APN)..."
WDS_CID=""
n=0
while [ "$n" -lt "$START_RETRIES" ]; do
    n=$((n+1))
    out="$(qmicli -p -d "$QMI_DEV" \
            --wds-start-network="ip-type=4,apn=$APN" \
            --client-no-release-cid 2>&1)"
    if echo "$out" | grep -qi "Network started"; then
        WDS_CID="$(echo "$out" | grep -i 'CID:' | grep -oE '[0-9]+' | head -1)"
        log "Network started (CID ${WDS_CID:-unknown})."
        break
    fi
    warn "start-network attempt $n: $(echo "$out" | tr '\n' ' ')"
    sleep 3
done

is_connected || die "QMI session not connected after $START_RETRIES attempts."
log "QMI data session connected."

# ===========================================================================
# 6. Read live settings (QMI-native -- no AT+CGPADDR needed now)
# ===========================================================================
qargs=( -p -d "$QMI_DEV" )
[ -n "${WDS_CID:-}" ] && qargs+=( --client-cid="$WDS_CID" --client-no-release-cid )
settings="$(qmicli "${qargs[@]}" --wds-get-current-settings 2>/dev/null)"

IP="$(echo "$settings" | grep -i 'IPv4 address'      | awk '{print $NF}')"
MASK="$(echo "$settings"| grep -i 'IPv4 subnet mask'     | awk '{print $NF}')"
GW="$(echo "$settings"  | grep -i 'IPv4 gateway address' | awk '{print $NF}')"
DNS="$(echo "$settings" | grep -i 'IPv4 primary DNS'     | awk '{print $NF}' | head -1)"
MTU="$(echo "$settings" | grep -i 'MTU'                  | awk '{print $NF}' | head -1)"

[ -n "${IP:-}" ] && [ "$IP" != "0.0.0.0" ] || die "Could not determine UE IP from QMI settings:\n$settings"

mask2cidr() {
    local o prefix=0
    IFS=. read -r o1 o2 o3 o4 <<< "$1"
    for o in "$o1" "$o2" "$o3" "$o4"; do
        case "$o" in
            255) prefix=$((prefix+8)) ;; 254) prefix=$((prefix+7)) ;;
            252) prefix=$((prefix+6)) ;; 248) prefix=$((prefix+5)) ;;
            240) prefix=$((prefix+4)) ;; 224) prefix=$((prefix+3)) ;;
            192) prefix=$((prefix+2)) ;; 128) prefix=$((prefix+1)) ;;
            0) ;; *) echo 24; return ;;
        esac
    done
    echo "$prefix"
}
PREFIX=24
[ -n "${MASK:-}" ] && PREFIX="$(mask2cidr "$MASK")"

log "Assigned: IP=$IP/$PREFIX  GW=${GW:-none}  DNS=${DNS:-none}  MTU=${MTU:-default}"

log "Applying to $IFACE..."
ip link set "$IFACE" down 2>/dev/null || true
ip addr flush dev "$IFACE" 2>/dev/null || true
ip addr add "$IP/$PREFIX" dev "$IFACE"
[ -n "${MTU:-}" ] && [ "$MTU" -gt 0 ] 2>/dev/null && ip link set "$IFACE" mtu "$MTU"
ip link set "$IFACE" up

if [ -n "${GW:-}" ] && ip route replace default via "$GW" dev "$IFACE" 2>/dev/null; then
    :
else
    ip route replace default dev "$IFACE"
fi
[ -n "${DNS:-}" ] && printf 'nameserver %s\n' "$DNS" > /etc/resolv.conf

# ===========================================================================
# 7. Verify real connectivity
# ===========================================================================
log "Testing real internet connectivity: ping $PING_TARGET via $IFACE..."
if ping -c 3 -W 2 -I "$IFACE" "$PING_TARGET" >/dev/null 2>&1; then
    log "SUCCESS -- $IFACE is up at $IP, reaching $PING_TARGET."
    ip addr show "$IFACE" | sed 's/^/    /'
    exit 0
else
    err "Interface configured ($IP) but ping to $PING_TARGET failed."
    exit 2
fi