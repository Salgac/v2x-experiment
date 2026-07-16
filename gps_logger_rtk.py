#!/usr/bin/env python3
"""
gps_logger_rtk.py - GNSS logger with RTK corrections via NTRIP.

Combines, in a single process on a single shared serial connection:
  1. A UBX NAV-PVT reader (same approach as gps_logger.py), logging fixes
     to CSV, now including carrSoln (RTK float/fixed status).
  2. A background thread (ntrip.stream_corrections) that fetches RTCM3
     correction data from an NTRIP caster and writes it to the receiver.

The NTRIP thread is the ONLY thing that ever writes to the serial port;
the main loop only ever reads from it. That split is what makes it safe
to share one open serial handle between the two -- see ntrip.py.

Usage:
    python3 gps_logger_rtk.py --port /dev/ttyACM0

    (NTRIP server/port/mountpoint/credentials come from .env -- see
    ntrip.py / .env.example. Override any of them with --ntrip-* flags
    if needed.)

Dependencies:
    pip install pyserial pyubx2 python-dotenv --break-system-packages
"""

import argparse
import csv
import datetime as dt
import os
import sys
import threading
import time
from collections import Counter

import serial
from pyubx2 import UBXReader, UBX_PROTOCOL

from ntrip2 import stream_corrections, NTRIP_SERVER, NTRIP_PORT, NTRIP_MOUNTPOINT

FIELDS = [
    "log_time_utc",
    "gps_time_utc",
    "lat",
    "lon",
    "height_m",
    "hMSL_m",
    "fixType",
    "carrSoln",
    "numSV",
    "hAcc_m",
    "vAcc_m",
    "gSpeed_mps",
    "headMot_deg",
    "pDOP",
]

FIX_TYPES = {
    0: "no fix",
    1: "dead reckoning",
    2: "2D",
    3: "3D",
    4: "GNSS+DR",
    5: "time only",
}
CARR_SOLN = {0: "none", 1: "float", 2: "fixed"}


def open_writer(outdir):
    os.makedirs(outdir, exist_ok=True)
    fname = os.path.join(
        outdir, f"gps_rtk_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M%S}.csv"
    )
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
    ap.add_argument("--debug", action="store_true")

    ap.add_argument("--ntrip-server", default=NTRIP_SERVER)
    ap.add_argument("--ntrip-port", type=int, default=NTRIP_PORT)
    ap.add_argument("--mountpoint", default=NTRIP_MOUNTPOINT)
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    stop_event = threading.Event()
    ntrip_thread = threading.Thread(
        target=stream_corrections,
        args=(ser,),
        kwargs={
            "server": args.ntrip_server,
            "port": args.ntrip_port,
            "mountpoint": args.mountpoint,
            "stop_event": stop_event,
            "debug": args.debug,
        },
        daemon=True,
    )
    ntrip_thread.start()
    print(
        f"NTRIP thread started ({args.ntrip_server}:{args.ntrip_port}/{args.mountpoint})"
    )

    # --- UBX NAV-PVT reader, same approach as gps_logger.py ---
    ubr = UBXReader(ser, protfilter=UBX_PROTOCOL)
    f, csv_writer = open_writer(args.outdir)
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
                    "log_time_utc": dt.datetime.now(dt.timezone.utc).isoformat(
                        timespec="milliseconds"
                    ),
                    "gps_time_utc": (
                        f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}T"
                        f"{parsed.hour:02d}:{parsed.min:02d}:{parsed.second:02d}"
                    ),
                    "lat": parsed.lat,
                    "lon": parsed.lon,
                    "height_m": parsed.height / 1000.0,
                    "hMSL_m": parsed.hMSL / 1000.0,
                    "fixType": FIX_TYPES.get(parsed.fixType, parsed.fixType),
                    "carrSoln": CARR_SOLN.get(getattr(parsed, "carrSoln", 0), "?"),
                    "numSV": parsed.numSV,
                    "hAcc_m": parsed.hAcc / 1000.0,
                    "vAcc_m": parsed.vAcc / 1000.0,
                    "gSpeed_mps": parsed.gSpeed / 1000.0,
                    "headMot_deg": parsed.headMot,
                    "pDOP": getattr(parsed, "pDOP", None),
                }
                csv_writer.writerow(row)
                f.flush()
                rows_written += 1
                print(
                    f"{row['log_time_utc']}  fix={row['fixType']:<9} "
                    f"carr={row['carrSoln']:<5} sats={row['numSV']:>2} "
                    f"lat={row['lat']:.7f} lon={row['lon']:.7f} "
                    f"hAcc={row['hAcc_m']:.3f}m"
                )

            if time.time() - last_status > 10:
                print(
                    f"[stats] message types: {dict(seen_types)}  "
                    f"rows_written={rows_written}  "
                    f"ntrip_alive={ntrip_thread.is_alive()}"
                )
                last_status = time.time()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop_event.set()
        f.close()
        ser.close()


if __name__ == "__main__":
    main()
