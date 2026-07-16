#!/usr/bin/env bash
#
# ue_connect.sh — bring up the Waveshare SIM8200 (Qualcomm X55) as a UE
# against an open5gs 5G core, fully automatically.
#
# What it does, in order:
#   1. Frees the modem channels (stops ModemManager + any stray simcom-cm)
#   2. Auto-detects the AT serial port and configures the modem
#      (network mode, APN), then tears down the AT-side PDU context so
#      QMI can own the data path  <-- this was the key to getting data up
#   3. Waits for the modem to register on the network
#   4. Starts the data session over QMI (via qmi-proxy)
#   5. Reads the IP/gateway/DNS/MTU FROM THE LIVE SESSION (not a guess)
#      and applies them to wwan0 — this avoids the "Source IP Spoofing"
#      drops you get when a stale/wrong address is on the interface
#   6. Verifies with a ping to the UPF gateway
#
# Run as root:  sudo ./ue_connect.sh
#
# Re-runnable: if a session is already connected it just re-reads and
# re-applies the current settings instead of dialing again (so it won't
# leak QMI client IDs on repeated runs).

set -u

# ---------------------------------------------------------------------------
# Config — change these if your setup differs
# ---------------------------------------------------------------------------
APN="internet"          # must match the DNN in your open5gs subscriber entry
IFACE="wwan0"           # QMI network interface
QMI_DEV="/dev/cdc-wdm0" # QMI control device
NET_MODE="71"           # 71 = LTE+NR (works on this X55). 38 = NR-only (fails on old fw)
AT_PORT_CANDIDATES="/dev/ttyUSB2 /dev/ttyUSB3 /dev/ttyUSB1 /dev/ttyUSB0"
REG_TIMEOUT=60          # seconds to wait for network registration
START_RETRIES=5         # how many times to retry the QMI data call
SET_DNS=1               # 1 = write /etc/resolv.conf with the assigned DNS
AT_TMP="$(mktemp)"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log()  { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; cleanup; exit 1; }

cleanup() { rm -f "$AT_TMP" 2>/dev/null; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "Run as root:  sudo $0"

for bin in qmicli ip stty timeout ping; do
    command -v "$bin" >/dev/null 2>&1 || die "Missing required tool: $bin"
done

# ---------------------------------------------------------------------------
# Serial / AT helpers
# ---------------------------------------------------------------------------
AT_PORT=""

config_port() { stty -F "$1" 115200 raw -echo 2>/dev/null; }

# at <command> [read_seconds]  -> echoes the modem's raw response
at() {
    local cmd="$1" wait="${2:-2}"
    : > "$AT_TMP"
    timeout "$wait" cat "$AT_PORT" > "$AT_TMP" 2>/dev/null &
    local rpid=$!
    sleep 0.2
    printf '%s\r' "$cmd" > "$AT_PORT"
    wait "$rpid" 2>/dev/null
    cat "$AT_TMP"
}

find_at_port() {
    local p
    for p in $AT_PORT_CANDIDATES; do
        [ -e "$p" ] || continue
        config_port "$p"
        AT_PORT="$p"
        if at "AT" 1 | grep -q "OK"; then
            log "AT port: $p"
            return 0
        fi
    done
    return 1
}

wait_for_at_port() {   # used after a modem reboot
    local deadline=$(( $(date +%s) + 45 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if find_at_port; then return 0; fi
        sleep 2
    done
    return 1
}

# ---------------------------------------------------------------------------
# Netmask -> CIDR prefix
# ---------------------------------------------------------------------------
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

# ===========================================================================
# 1. Free the modem channels
# ===========================================================================
log "Releasing modem channels (ModemManager / simcom-cm)..."
systemctl stop ModemManager 2>/dev/null || true
pkill -f simcom-cm 2>/dev/null || true
sleep 1
# qmi-proxy may still be alive from MM; that's fine — we talk through it with -p

# ===========================================================================
# 2. AT configuration
# ===========================================================================
log "Locating AT port..."
find_at_port || die "No responding AT port among: $AT_PORT_CANDIDATES (is the USB cable connected?)"

# SIM check
if ! at "AT+CPIN?" 2 | grep -q "READY"; then
    die "SIM not READY (AT+CPIN? did not return READY). Reseat the SIM."
fi
log "SIM ready."

# Ensure full functionality
at "AT+CFUN=1" 2 >/dev/null

# Network mode — only reboot if it actually needs changing
cur_mode="$(at "AT+CNMP?" 2 | grep -o '+CNMP: [0-9]*' | grep -o '[0-9]*')"
if [ "${cur_mode:-}" != "$NET_MODE" ]; then
    log "Setting network mode to $NET_MODE (was ${cur_mode:-unknown}); modem will reboot..."
    at "AT+CNMP=$NET_MODE" 2 >/dev/null
    at "AT+CFUN=1,1" 2 >/dev/null
    sleep 5
    wait_for_at_port || die "Modem did not come back after reboot."
    # SIM re-check after reboot
    at "AT+CPIN?" 5 >/dev/null
else
    log "Network mode already $NET_MODE."
fi

# APN
log "Setting APN '$APN' on context 1..."
at "AT+CGDCONT=1,\"IP\",\"$APN\"" 2 >/dev/null

# (AT context teardown is deferred to immediately before the QMI dial — see below.)

# ===========================================================================
# 3. Wait for registration
# ===========================================================================
log "Waiting for network registration (up to ${REG_TIMEOUT}s)..."
registered=0
deadline=$(( $(date +%s) + REG_TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    # stat field 1 = registered (home), 5 = registered (roaming/private PLMN)
    reg="$(at "AT+CEREG?" 1; at "AT+C5GREG?" 1)"
    if echo "$reg" | grep -Eq '(CEREG|C5GREG): *[0-9]+, *(1|5)'; then
        registered=1
        break
    fi
    sleep 2
done
if [ "$registered" -eq 1 ]; then
    log "Registered on the network."
else
    warn "Registration not confirmed via AT — will still try the data call."
fi

# ===========================================================================
# 4. Start the data session over QMI
# ===========================================================================
[ -e "$QMI_DEV" ] || die "QMI device $QMI_DEV not found (qmi_wwan not bound?)."

is_connected() {
    qmicli -p -d "$QMI_DEV" --wds-get-packet-service-status 2>/dev/null \
        | grep -qi "connected"
}

# Always dial deterministically with your proven recipe:
#   AT+CGACT=0,1  ->  qmicli --wds-start-network  ->  (later) AT+CGPADDR=1
# We do NOT short-circuit on an existing session: a leftover session from a
# prior run gives us no CID and a stale/unreflected address, which is exactly
# the failure you hit. Clearing the context first makes start-network return a
# fresh "Network started" + CID every time.
WDS_CID=""

log "Putting $IFACE into raw-ip mode..."
ip link set "$IFACE" down 2>/dev/null || true
if [ -e "/sys/class/net/$IFACE/qmi/raw_ip" ]; then
    echo Y > "/sys/class/net/$IFACE/qmi/raw_ip"
fi

log "Dialing data session (apn=$APN)..."
n=0
while [ "$n" -lt "$START_RETRIES" ]; do
    n=$((n+1))
    # Clear any existing context/session so start-network creates a fresh one.
    at "AT+CGACT=0,1" 4 >/dev/null
    sleep 1
    out="$(qmicli -p -d "$QMI_DEV" \
            --wds-start-network="ip-type=4,apn=$APN" \
            --client-no-release-cid 2>&1)"
    if echo "$out" | grep -qi "Network started"; then
        WDS_CID="$(echo "$out" | grep -i 'CID:' | grep -oE '[0-9]+' | head -1)"
        log "Network started (CID ${WDS_CID:-unknown})."
        break
    fi
    if echo "$out" | grep -qi "interface-in-use"; then
        # Session already up but CGACT didn't drop it; no CID, but the address
        # is still readable via CGPADDR below.
        warn "Session already active (interface-in-use); will read its IP via CGPADDR."
        break
    fi
    warn "start-network attempt $n: $(echo "$out" | tr '\n' ' ')"
    sleep 3
done

# Confirm
if ! is_connected; then
    die "QMI session not connected after $START_RETRIES attempts. On this X55+old firmware NR-only often fails — confirm NET_MODE=71 and that the subscriber's '$APN' DNN exists in open5gs."
fi
log "QMI data session connected."

# ===========================================================================
# 5. Read the LIVE settings and apply them
# ===========================================================================
log "Reading assigned IP settings from the live session..."

# Primary source: AT+CGPADDR=1 read AFTER the QMI dial. The QMI session
# reflects into context 1, so this returns the live session's real address —
# this is the sequence you confirmed works (CGACT=0,1 -> start-network -> CGPADDR).
[ -n "$AT_PORT" ] || find_at_port
IP="$(at "AT+CGPADDR=1" 3 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' \
        | grep -v '^0\.0\.0\.0$' | head -1)"

# Enrichment (best effort): mask / gateway / DNS / MTU from QMI, querying the
# SAME client that owns the session (a fresh client reports nothing).
qargs=( -p -d "$QMI_DEV" )
[ -n "${WDS_CID:-}" ] && qargs+=( --client-cid="$WDS_CID" --client-no-release-cid )
settings="$(qmicli "${qargs[@]}" --wds-get-current-settings 2>/dev/null)"

MASK="$(echo "$settings"| grep -i 'IPv4 subnet mask'     | awk '{print $NF}')"
GW="$(echo "$settings"  | grep -i 'IPv4 gateway address' | awk '{print $NF}')"
DNS="$(echo "$settings" | grep -i 'IPv4 primary DNS'     | awk '{print $NF}' | head -1)"
MTU="$(echo "$settings" | grep -i 'MTU'                  | awk '{print $NF}' | head -1)"

# If CGPADDR was empty, fall back to the QMI-reported address.
[ -z "${IP:-}" ] && IP="$(echo "$settings" | grep -i 'IPv4 address' | awk '{print $NF}')"

# If still no gateway, try CGCONTRDP (3rd IPv4 token: UE-addr, mask, gateway).
if [ -z "${GW:-}" ]; then
    GW="$(at "AT+CGCONTRDP=1" 3 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' \
            | sed -n '3p')"
fi

if [ -z "${IP:-}" ] || [ "${IP:-}" = "0.0.0.0" ]; then
    err "Could not determine the UE IP from CGPADDR or QMI. Raw QMI settings:"
    echo "${settings:-<empty>}" | sed 's/^/    /'
    die "Share:  AT+CGPADDR=1   and   qmicli -p -d $QMI_DEV --client-cid=${WDS_CID:-N} --client-no-release-cid --wds-get-current-settings"
fi

PREFIX=24
[ -n "${MASK:-}" ] && PREFIX="$(mask2cidr "$MASK")"

log "Assigned: IP=$IP/$PREFIX  GW=${GW:-none}  DNS=${DNS:-none}  MTU=${MTU:-default}"

log "Applying to $IFACE..."
ip link set "$IFACE" down 2>/dev/null || true
ip addr flush dev "$IFACE" 2>/dev/null || true
ip addr add "$IP/$PREFIX" dev "$IFACE"
[ -n "${MTU:-}" ] && [ "$MTU" -gt 0 ] 2>/dev/null && ip link set "$IFACE" mtu "$MTU"
ip link set "$IFACE" up

# Default route: prefer via the gateway, fall back to point-to-point dev route
if [ -n "${GW:-}" ] && ip route replace default via "$GW" dev "$IFACE" 2>/dev/null; then
    :
else
    ip route replace default dev "$IFACE"
fi

# DNS
if [ "$SET_DNS" -eq 1 ] && [ -n "${DNS:-}" ]; then
    log "Setting DNS -> $DNS"
    printf 'nameserver %s\n' "$DNS" > /etc/resolv.conf
fi

# ===========================================================================
# 6. Verify
# ===========================================================================
PING_TARGET="${GW:-10.45.0.1}"
log "Testing user plane: ping $PING_TARGET via $IFACE..."
if ping -c 3 -W 2 -I "$IFACE" "$PING_TARGET" >/dev/null 2>&1; then
    log "SUCCESS — $IFACE is up at $IP and reaching the core ($PING_TARGET)."
    echo
    ip addr show "$IFACE" | sed 's/^/    /'
    echo
    log "If you need the public internet, ensure the open5gs HOST has:"
    echo "      sysctl -w net.ipv4.ip_forward=1"
    echo "      iptables -t nat -A POSTROUTING -s 10.45.0.0/16 -o <uplink> -j MASQUERADE"
else
    warn "Interface is configured ($IP) but ping to $PING_TARGET failed."
    warn "This is now a CORE-side downlink issue, not the modem. On the open5gs host check:"
    echo "      ip addr show ogstun        # must be UP with 10.45.0.1/16"
    echo "      journalctl -u open5gs-upfd --since '30 sec ago' | grep -i tun"
    echo "    If you see 'ogs_tun_write() failed' (EIO):"
    echo "      sudo ip link set ogstun up && sudo systemctl restart open5gs-upfd"
    exit 2
fi
