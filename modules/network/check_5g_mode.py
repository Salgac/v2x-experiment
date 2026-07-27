#!/usr/bin/env python3
"""
check_5g_mode.py - Read-only check of AT+CSYSSEL (system selection
preference: NR5G disable, NR5G band, NSA-specific NR5G band, LTE band,
WCDMA band) and AT+CPOL (preferred operator list). Does not change
anything -- just reports current values so we know the exact syntax
and current state before writing anything.

Usage:
    python3 check_5g_mode.py --port /dev/ttyUSB5
"""

import argparse
import sys
import time

import serial


def send_at(ser, command, timeout=3):
    ser.reset_input_buffer()
    ser.write((command.strip() + "\r\n").encode("ascii"))
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            if b"OK\r\n" in buf or b"ERROR" in buf:
                break
    return buf.decode("ascii", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    for cmd in [
        "AT+CSYSSEL=?",
        "AT+CSYSSEL?",
        'AT+CSYSSEL="nr5g_disable"',
        'AT+CSYSSEL="nr5g_band"',
        'AT+CSYSSEL="nsa_nr5g_band"',
        'AT+CSYSSEL="lte_band"',
        "AT+CPOL?",
        "AT+CGDCONT?",
    ]:
        print(f"--- {cmd} ---")
        print(send_at(ser, cmd).strip())
        print()

    ser.close()


if __name__ == "__main__":
    main()
