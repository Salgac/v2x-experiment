#!/usr/bin/env bash
# full_reset.sh - One clean, fully-consolidated attempt: resets every AT and
# QMI setting touched this session, in the right order, with a real wait
# at the end. Not a new fix -- a clean-slate re-application of everything
# already individually confirmed correct, to rule out residual/inconsistent
# state from incremental changes.
set -e

AT_PORT="${1:-/dev/ttyUSB5}"
QMI_DEV="/dev/cdc-wdm0"

echo "[1/6] Stopping ModemManager..."
sudo systemctl stop ModemManager 2>/dev/null || true

echo "[2/6] AT layer: mode=auto, acquisition order=factory default, APN..."
python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
for cmd in [
    'AT+CNMP=2',
    'AT+CNAOP=7,9,12,5,3,2,4,11',
    'AT+CGDCONT=1,\"IP\",\"internet.static\"',
]:
    ser.write((cmd + '\r\n').encode())
    time.sleep(1)
    print(cmd, '->', ser.read(200))
"

echo "[3/6] QMI layer: mode preference lte+5gnr, network selection automatic..."
sudo qmicli -p -d "$QMI_DEV" --nas-set-system-selection-preference="lte|5gnr",automatic

echo "[4/6] Full QMI-level reset (reboots the modem)..."
sudo qmicli -d "$QMI_DEV" --dms-set-operating-mode=reset

echo "[5/6] Waiting 90s for the modem to fully come back and search..."
sleep 90

echo "[6/6] Status:"
sudo qmicli -d "$QMI_DEV" --nas-get-serving-system
sudo qmicli -d "$QMI_DEV" --nas-get-signal-info 2>&1 || true