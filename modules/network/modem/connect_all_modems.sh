#!/usr/bin/env bash
#
# connect_all_modems.sh — run connect_and_verify.sh across every configured
# modem, one after another, and print a summary at the end. Each modem's
# bring-up is independent -- one failing doesn't stop the others from
# being attempted.
#
# Edit MODEMS below as each physical modem comes online. Fields per line
# (colon-separated): name:QMI_DEV:IFACE:AT_PORT_CANDIDATES:APN:PLMN
#   - AT_PORT_CANDIDATES: space-separated ports, quote the whole field
#   - APN: confirm per-operator, no safe universal default
#   - PLMN: leave blank for automatic (the default/recommended setting --
#     see connect_and_verify.sh header for when you'd want to override it)
#
# Usage:
#   sudo ./connect_all_modems.sh
#
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$SCRIPT_DIR/connect_and_verify.sh"

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
[ -x "$WORKER" ] || { echo "connect_and_verify.sh not found/executable next to this script" >&2; exit 1; }

# --- Modem definitions -- fill these in as each modem comes online ---------
MODEMS=(
    "modem1:/dev/cdc-wdm0:wwan0:/dev/ttyUSB5 /dev/ttyUSB4 /dev/ttyUSB3 /dev/ttyUSB2:internet:"
    # "modem2:/dev/cdc-wdm1:wwan1:/dev/ttyUSB12 /dev/ttyUSB13:internet:"
    # "modem3:/dev/cdc-wdm2:wwan2:/dev/ttyUSB20 /dev/ttyUSB21:internet:"
    # "modem4:/dev/cdc-wdm3:wwan3:/dev/ttyUSB28 /dev/ttyUSB29:internet:"
)

declare -A RESULTS

for entry in "${MODEMS[@]}"; do
    IFS=':' read -r name qmi_dev iface at_ports apn plmn <<< "$entry"
    echo
    echo "=================================================================="
    echo "  $name  ($qmi_dev / $iface)"
    echo "=================================================================="

    if [ ! -e "$qmi_dev" ]; then
        echo "  SKIPPED -- $qmi_dev does not exist"
        RESULTS["$name"]="SKIPPED (device not present)"
        continue
    fi

    if QMI_DEV="$qmi_dev" IFACE="$iface" AT_PORT_CANDIDATES="$at_ports" \
       APN="$apn" PLMN="$plmn" "$WORKER"; then
        RESULTS["$name"]="OK"
    else
        RESULTS["$name"]="FAILED (exit $?)"
    fi
done

echo
echo "=================================================================="
echo "  Summary"
echo "=================================================================="
for entry in "${MODEMS[@]}"; do
    name="${entry%%:*}"
    printf "  %-10s %s\n" "$name" "${RESULTS[$name]:-not attempted}"
done