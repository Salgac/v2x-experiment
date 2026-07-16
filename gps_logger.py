#!/usr/bin/env python3
"""
gps_logger.py - Real-time GNSS logger for the u-blox ZED-F9P
(C099-F9P evaluation board), connected to the Pi over USB.

This board's USB port streams the UBX binary protocol by default (not
NMEA text), so this script parses UBX NAV-PVT messages directly using
pyubx2, which handles UBX's own frame sync/length/checksum instead of
relying on newlines. This also gives richer fields than NMEA would --
horizontal/vertical accuracy estimates, DOP, fix type -- useful for
correlating against cellular network quality later.

Usage:
    python3 gps_logger.py [--port /dev/ttyACM0] [--outdir ./logs] [--debug]

Dependencies:
    pip install pyserial pyubx2 --break-system-packages
"""

import argparse
import csv
import datetime as dt
import os
import sys
import time
from collections import Counter

import serial
from pyubx2 import UBXReader, UBX_PROTOCOL

FIELDS = [
    "log_time_utc", "gps_time_utc", "lat", "lon", "height_m", "hMSL_m",
    "fixType", "numSV", "hAcc_m", "vAcc_m", "gSpeed_mps", "headMot_deg", "pDOP",
]

FIX_TYPES = {
    0: "no fix", 1: "dead reckoning", 2: "2D", 3: "3D",
    4: "GNSS+DR", 5: "time only",
}


def open_writer(outdir):
    os.makedirs(outdir, exist_ok=True)
    fname = os.path.join(outdir, f"gps_{dt.datetime.utcnow():%Y%m%d_%H%M%S}.csv")
    f = open(fname, "w", newline="")
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    f.flush()
    print(f"Logging to {fname}")
    return f, writer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--outdir", default="./logs")
    ap.add_argument("--debug", action="store_true",
                     help="print every new UBX message identity as it's first seen")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    ubr = UBXReader(ser, protfilter=UBX_PROTOCOL)
    f, writer = open_writer(args.outdir)

    seen_types = Counter()
    rows_written = 0
    last_status = time.time()

    print("Waiting for UBX messages (Ctrl+C to stop)...")
    try:
        while True:
            try:
                _, parsed = ubr.read()
            except Exception as e:
                if args.debug:
                    print(f"(frame error, resyncing: {e})")
                continue

            if parsed is None:
                continue

            if args.debug and seen_types[parsed.identity] == 0:
                print(f"First time seeing message type: {parsed.identity}")
            seen_types[parsed.identity] += 1

            if parsed.identity == "NAV-PVT":
                row = {
                    "log_time_utc": dt.datetime.utcnow().isoformat(timespec="milliseconds"),
                    "gps_time_utc": (
                        f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}T"
                        f"{parsed.hour:02d}:{parsed.min:02d}:{parsed.second:02d}"
                    ),
                    "lat": parsed.lat,
                    "lon": parsed.lon,
                    "height_m": parsed.height / 1000.0,
                    "hMSL_m": parsed.hMSL / 1000.0,
                    "fixType": FIX_TYPES.get(parsed.fixType, parsed.fixType),
                    "numSV": parsed.numSV,
                    "hAcc_m": parsed.hAcc / 1000.0,
                    "vAcc_m": parsed.vAcc / 1000.0,
                    "gSpeed_mps": parsed.gSpeed / 1000.0,
                    "headMot_deg": parsed.headMot,
                    "pDOP": getattr(parsed, "pDOP", None),
                }
                writer.writerow(row)
                f.flush()
                rows_written += 1
                print(
                    f"{row['log_time_utc']}  fix={row['fixType']:<9} "
                    f"sats={row['numSV']:>2} lat={row['lat']:.7f} "
                    f"lon={row['lon']:.7f} hAcc={row['hAcc_m']:.2f}m"
                )

            if time.time() - last_status > 10:
                print(f"[stats] message types seen: {dict(seen_types)}  "
                      f"rows_written={rows_written}")
                last_status = time.time()
    except KeyboardInterrupt:
        print("\nStopping.")
        print(f"Message types seen: {dict(seen_types)}")
        if "NAV-PVT" not in seen_types:
            print(
                "No NAV-PVT messages were seen on this port -- it needs to be "
                "enabled once. Let Claude know what message types *did* show up "
                "above and it can help enable NAV-PVT output."
            )
    finally:
        f.close()
        ser.close()


if __name__ == "__main__":
    main()
