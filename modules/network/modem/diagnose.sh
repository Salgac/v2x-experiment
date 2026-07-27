#!/usr/bin/env bash
#
# diagnose.sh — one-shot dump of every read-only AT and QMI check found
# useful while debugging modem registration this session. Nothing here
# changes any setting -- entirely safe to run any time, on any modem, as
# a first step before further debugging.
#
# Usage:
#   sudo ./diagnose.sh /dev/ttyUSB5 /dev/cdc-wdm0
#   (both arguments optional -- defaults shown below are for modem1)
#
set -u
AT_PORT="${1:-/dev/ttyUSB5}"
QMI_DEV="${2:-/dev/cdc-wdm0}"

section() { echo; echo "=== $* ==="; }

section "AT: module identity, SIM, and basic signal"
python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
def at(cmd, wait=1.5):
    ser.reset_input_buffer()
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    print(f'{cmd} -> {ser.read(500).decode(\"ascii\", errors=\"replace\").strip()}')
for cmd in [
    'AT+SIMCOMATI', 'AT+CPIN?', 'AT+CSQ', 'AT+COPS?', 'AT+CREG?',
    'AT+CEREG?', 'AT+C5GREG?', 'AT+CPSI?', 'AT+CGDCONT?',
]:
    at(cmd)
"

section "AT: network mode and acquisition order"
python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
def at(cmd, wait=1.5):
    ser.reset_input_buffer()
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    print(f'{cmd} -> {ser.read(500).decode(\"ascii\", errors=\"replace\").strip()}')
for cmd in ['AT+CNMP?', 'AT+CNAOP?']:
    at(cmd)
"

section "AT: CSYSSEL (band lists, NR5G disable, acquisition order)"
python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
def at(cmd, wait=1.5):
    ser.reset_input_buffer()
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    print(f'{cmd} -> {ser.read(500).decode(\"ascii\", errors=\"replace\").strip()}')
for cmd in [
    'AT+CSYSSEL=\"nr5g_disable\"', 'AT+CSYSSEL=\"rat_acq_order\"',
]:
    at(cmd)
"

section "AT: CNBP band mask (raw -- decode with check_band_config.py's method if needed)"
python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
ser.write(b'AT+CNBP?\r\n')
time.sleep(1.5)
print(ser.read(500).decode('ascii', errors='replace').strip())
"

section "AT: personalization locks (PN/PU/PP/PC)"
python3 -c "
import serial, time
ser = serial.Serial('$AT_PORT', 115200, timeout=2)
for fac in ['PN', 'PU', 'PP', 'PC']:
    ser.reset_input_buffer()
    ser.write(f'AT+CLCK=\"{fac}\",2\r\n'.encode())
    time.sleep(1.5)
    print(f'{fac} -> {ser.read(300).decode(\"ascii\", errors=\"replace\").strip()}')
"

section "QMI: system selection preference (mode, PLMN, bands)"
qmicli -d "$QMI_DEV" --nas-get-system-selection-preference 2>&1

section "QMI: technology preference"
qmicli -d "$QMI_DEV" --nas-get-technology-preference 2>&1

section "QMI: serving system"
qmicli -d "$QMI_DEV" --nas-get-serving-system 2>&1

section "QMI: signal info"
qmicli -d "$QMI_DEV" --nas-get-signal-info 2>&1

section "QMI: system info (per-RAT status)"
qmicli -d "$QMI_DEV" --nas-get-system-info 2>&1

section "QMI: operating mode"
qmicli -d "$QMI_DEV" --dms-get-operating-mode 2>&1

echo
echo "Done. Nothing above was changed -- this was read-only."