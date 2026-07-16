#!/usr/bin/env python3
"""
configure_receiver.py - One-time setup: enable UBX NAV-PVT output on the
ZED-F9P's USB port, saved to RAM + battery-backed RAM + flash so it
survives power cycles (not just the current session).

Run this once, with NOTHING else using the serial port (stop gpsd, and
don't have gps_logger*.py running at the same time -- this needs
exclusive, uncontested access to send the config command and see the ACK).

Usage:
    python3 configure_receiver.py [--port /dev/ttyACM0]

Dependencies:
    pip install pyserial pyubx2 --break-system-packages
"""

import argparse
import sys
import time

import serial
from pyubx2 import UBXMessage, UBXReader, UBX_PROTOCOL

LAYER_RAM = 0x01
LAYER_BBR = 0x02
LAYER_FLASH = 0x04


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=38400)
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    msg = UBXMessage.config_set(
        layers=LAYER_RAM | LAYER_BBR | LAYER_FLASH,
        transaction=0,
        cfgData=[("CFG_MSGOUT_UBX_NAV_PVT_USB", 1)],
    )
    print(
        "Sending CFG-VALSET to enable NAV-PVT output on USB "
        "(layers: RAM + BBR + Flash)..."
    )
    ser.write(msg.serialize())

    ubr = UBXReader(ser, protfilter=UBX_PROTOCOL)
    deadline = time.time() + 3
    acked = False
    while time.time() < deadline:
        try:
            _, parsed = ubr.read()
        except Exception:
            continue
        if parsed is None:
            continue
        if parsed.identity == "ACK-ACK":
            print(
                "Receiver ACKed the change. NAV-PVT is now enabled on "
                "USB and saved -- it should survive a power cycle."
            )
            acked = True
            break
        if parsed.identity == "ACK-NAK":
            print(
                "Receiver NAKed the change -- something's wrong with "
                "the command (unexpected on this hardware/firmware)."
            )
            break

    if not acked:
        print(
            "No ACK seen within 3 seconds. Check nothing else has the "
            "port open, and try again."
        )

    ser.close()


if __name__ == "__main__":
    main()
