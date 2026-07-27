#!/usr/bin/env python3
"""
reset_net_mode.py - Reset the modem's network mode back to auto-seek and
do a full radio reset, then report status. Useful after ue_connect.sh set
a fixed LTE+NR-only mode (AT+CNMP=71) for the private core -- that setting
persists in NVM across SIM swaps and can prevent attaching to a public
carrier that doesn't like the forced RAT combination.

Usage:
    python3 reset_net_mode.py --port /dev/ttyUSB4
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


def wait_for_port(port, baud, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = serial.Serial(port, baud, timeout=2)
            if "OK" in send_at(s, "AT"):
                return s
            s.close()
        except serial.SerialException:
            pass
        time.sleep(2)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument(
        "--mode",
        default="2",
        help="AT+CNMP value to set (2=auto, 71=LTE+NR combined, "
        "38=NR-only -- see your carrier/firmware notes)",
    )
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    print("Current network mode:", send_at(ser, "AT+CNMP?").strip())
    print(f"Setting network mode to {args.mode}...")
    print(send_at(ser, f"AT+CNMP={args.mode}").strip())

    print("Resetting radio (AT+CFUN=1,1) -- modem will reboot...")
    send_at(ser, "AT+CFUN=1,1")
    ser.close()

    print("Waiting for the modem to come back (up to 45s)...")
    ser = wait_for_port(args.port, args.baud)
    if ser is None:
        sys.exit(
            "Modem did not come back -- try again in a few seconds, "
            "or power-cycle the HAT."
        )

    print("Modem is back.")
    print("SIM status:", send_at(ser, "AT+CPIN?").strip())
    print()
    print("Polling registration for up to 30s...")
    deadline = time.time() + 30
    while time.time() < deadline:
        cpsi = send_at(ser, "AT+CPSI?", timeout=3)
        cgreg = send_at(ser, "AT+CGREG?", timeout=3)
        print(f"CPSI: {cpsi.strip()!r}")
        print(f"CGREG: {cgreg.strip()!r}")
        if ",1" in cgreg or ",5" in cgreg:
            print("Registered!")
            break
        time.sleep(3)

    ser.close()


if __name__ == "__main__":
    main()
