#!/usr/bin/env python3
"""
unlock_sim.py - Check SIM PIN status and, with explicit confirmation,
unlock it.

SAFETY: entering the WRONG PIN 3 times locks the SIM and requires the PUK
code to recover. Entering the wrong PUK 10 times destroys the SIM
permanently. This script always shows remaining attempts first (via
AT+CPINR) and requires you to type the PIN interactively -- it never
sends a PIN automatically or from a config file.

Usage:
    python3 unlock_sim.py --port /dev/ttyUSB4
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

    status = send_at(ser, "AT+CPIN?")
    print(f"SIM status: {status.strip()!r}")

    if "READY" in status:
        print("SIM is already unlocked -- nothing to do.")
        ser.close()
        return

    if "SIM PIN" not in status:
        print(
            "SIM is not simply asking for a PIN (could be PUK-locked, "
            "missing, or something else) -- stopping here rather than "
            "guessing. Review the raw status above before doing anything "
            "further."
        )
        ser.close()
        return

    retries = send_at(ser, "AT+CPINR")
    print(f"Remaining attempts: {retries.strip()!r}")
    print()
    print("!!! Wrong PIN x3 -> SIM locks, needs PUK.")
    print("!!! Wrong PUK x10 -> SIM is permanently destroyed.")
    print("!!! Only proceed if the 'Remaining attempts' count above looks")
    print("!!! healthy (e.g. 3) and you're confident in the PIN.")
    print()

    pin = input("Type the PIN to send it, or press Enter to cancel: ").strip()
    if not pin:
        print("Cancelled -- nothing sent.")
        ser.close()
        return

    result = send_at(ser, f'AT+CPIN="{pin}"', timeout=5)
    print(f"Result: {result.strip()!r}")

    final_status = send_at(ser, "AT+CPIN?")
    print(f"SIM status now: {final_status.strip()!r}")

    if "READY" in final_status:
        print()
        print("Unlocked. Note: this only lasts until the modem next loses")
        print("power/resets -- you'd need to re-enter the PIN every time")
        print("unless you disable PIN-lock on this SIM entirely, which is")
        print("normal practice for an always-on embedded deployment (the")
        print("device's physical security is the real protection layer at")
        print("that point, not a SIM PIN).")
        disable = (
            input("Disable PIN lock on this SIM permanently? [y/N]: ").strip().lower()
        )
        if disable == "y":
            r = send_at(ser, f'AT+CLCK="SC",0,"{pin}"', timeout=5)
            print(f"AT+CLCK result: {r.strip()!r}")
            print(f"Status now: {send_at(ser, 'AT+CPIN?').strip()!r}")

    ser.close()


if __name__ == "__main__":
    main()
