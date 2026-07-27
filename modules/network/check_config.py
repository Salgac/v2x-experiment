#!/usr/bin/env python3
"""
check_band_config.py - Read-only check of the modem's current preferred
band configuration (AT+CNBP?). Does not change anything.

If this looks like a narrow/custom band list (rather than "everything
enabled"), that's likely why LTE registration keeps failing even though
real LTE coverage exists at this location -- see conversation context.

Usage:
    python3 check_band_config.py --port /dev/ttyUSB3
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

    print("Current preferred band configuration (AT+CNBP?):")
    print(send_at(ser, "AT+CNBP?").strip())
    print()
    print("Current network mode (AT+CNMP?):")
    print(send_at(ser, "AT+CNMP?").strip())

    ser.close()


if __name__ == "__main__":
    main()
