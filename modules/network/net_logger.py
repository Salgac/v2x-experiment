#!/usr/bin/env python3
"""
net_logger.py - Basic cellular network parameter logger for a SIMCom-based
modem (e.g. Waveshare SIM8200EA-M2), via AT commands over a serial port.

Logs, once per --interval seconds: signal strength (RSSI/BER), operator
name, radio access technology (2G/3G/4G), and registration status
(including LAC/Cell ID where the modem provides them).

First-time setup: find which /dev/ttyUSB* is the AT command port on your
board -- don't guess, probe:

    python3 net_logger.py --probe

Then log:

    python3 net_logger.py --port /dev/ttyUSB2 --outdir ./logs/network1

Dependencies:
    pip install pyserial --break-system-packages
"""

import argparse
import csv
import datetime as dt
import glob
import os
import re
import sys
import time

import serial

ACT_NAMES = {
    "0": "GSM",
    "1": "GSM Compact",
    "2": "UTRAN(3G)",
    "3": "GSM/EGPRS",
    "4": "UTRAN HSDPA",
    "5": "UTRAN HSUPA",
    "6": "UTRAN HSDPA+HSUPA",
    "7": "E-UTRAN(4G)",
    "8": "EC-GSM-IoT",
    "9": "E-UTRAN NB-IoT",
}

REG_STATUS_NAMES = {
    0: "not registered",
    1: "registered (home)",
    2: "searching",
    3: "denied",
    4: "unknown",
    5: "registered (roaming)",
}

FIELDS = [
    "log_time_utc",
    "rssi_raw",
    "rssi_dbm",
    "ber",
    "operator",
    "act",
    "reg_stat",
    "reg_stat_text",
    "lac",
    "cell_id",
]


def send_at(ser, command, timeout=2, debug=False):
    """Sends an AT command, returns the raw response text, or None if the
    modem replied ERROR, sent junk/NUL data, or nothing came back in time."""

    ser.reset_input_buffer()
    if debug:
        print(f">> {command}")
    ser.write((command.strip() + "\r\n").encode("ascii"))

    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            if b"OK\r\n" in buf or b"ERROR" in buf:
                break

    if debug:
        # truncate in debug output too -- a NUL flood is still huge otherwise
        preview = buf[:120]
        print(f"<< {preview!r}{' ...(truncated)' if len(buf) > 120 else ''}")

    if not buf:
        return None

    # A response that's mostly NUL bytes isn't a real ERROR or OK -- it
    # means this port isn't actually answering AT commands right now
    # (often a break condition: a diagnostic/data port rather than a true
    # AT channel, or nothing is currently driving its TX line). Flag it
    # distinctly instead of silently treating it as valid.
    if buf.count(b"\x00") > len(buf) * 0.5:
        if debug:
            print(
                "(mostly NUL bytes -- this does not look like a real AT "
                "port; likely a diagnostic/data port, or busy elsewhere)"
            )
        return None

    text = buf.decode("ascii", errors="replace")
    if b"OK\r\n" not in buf or b"ERROR" in buf:
        return None
    return text


def parse_csq(text):
    if not text:
        return None, None, None
    m = re.search(r"\+CSQ:\s*(\d+),(\d+)", text)
    if not m:
        return None, None, None
    rssi_raw, ber = int(m.group(1)), int(m.group(2))
    rssi_dbm = None if rssi_raw == 99 else -113 + 2 * rssi_raw
    return rssi_raw, rssi_dbm, (None if ber == 99 else ber)


def parse_cops(text):
    if not text:
        return None, None
    m = re.search(r'\+COPS:\s*\d+,\d+,"([^"]*)"(?:,(\d+))?', text)
    if not m:
        return None, None
    operator = m.group(1)
    act = m.group(2)
    return operator, ACT_NAMES.get(act, f"AcT={act}" if act else None)


def parse_creg(text):
    if not text:
        return None, None, None, None
    m = re.search(r'\+CREG:\s*\d+,(\d+)(?:,"([0-9A-Fa-f]+)","([0-9A-Fa-f]+)")?', text)
    if not m:
        return None, None, None, None
    stat = int(m.group(1))
    return stat, REG_STATUS_NAMES.get(stat, f"stat={stat}"), m.group(2), m.group(3)


def probe_ports(baud, timeout=1.0):
    candidates = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if not candidates:
        print("No /dev/ttyUSB* or /dev/ttyACM* devices found.")
        return
    print(f"Probing {len(candidates)} port(s) at {baud} baud with a plain 'AT'...")
    for port in candidates:
        try:
            ser = serial.Serial(port, baud, timeout=timeout)
            resp = send_at(ser, "AT", timeout=timeout)
            ser.close()
            if resp is not None:
                print(f"  {port}: responded OK  <-- likely the AT command port")
            else:
                print(
                    f"  {port}: no OK response (could be a data/diag/NMEA port, "
                    f"or busy with another process)"
                )
        except (serial.SerialException, OSError) as e:
            print(f"  {port}: could not open ({e})")


def open_writer(outdir):
    os.makedirs(outdir, exist_ok=True)
    fname = os.path.join(
        outdir, f"net_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M%S}.csv"
    )
    f = open(fname, "w", newline="")
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    f.flush()
    print(f"Logging to {fname}")
    return f, writer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--outdir", default="./logs")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="scan available serial ports for one that answers "
        "AT commands, then exit",
    )
    args = ap.parse_args()

    if args.probe:
        probe_ports(args.baud)
        return

    if not args.port:
        sys.exit("--port is required (run with --probe first to find it)")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=2)
    except (serial.SerialException, OSError) as e:
        sys.exit(f"Could not open {args.port}: {e}")

    # Basic init: echo off, enable extended registration info (LAC/cell ID)
    send_at(ser, "ATE0", debug=args.debug)
    send_at(ser, "AT+CREG=2", debug=args.debug)

    cpin = send_at(ser, "AT+CPIN?", debug=args.debug)
    if cpin and "+CPIN: READY" in cpin:
        print("SIM status: READY")
    elif cpin:
        print(f"SIM status: {cpin.strip()!r} (not READY -- check PIN lock/SIM state)")
    else:
        print("SIM status: no response to AT+CPIN? -- is a SIM inserted?")

    f, writer = open_writer(args.outdir)
    print("Logging network parameters (Ctrl+C to stop)...")
    try:
        while True:
            rssi_raw, rssi_dbm, ber = parse_csq(
                send_at(ser, "AT+CSQ", debug=args.debug)
            )
            operator, act = parse_cops(send_at(ser, "AT+COPS?", debug=args.debug))
            reg_stat, reg_stat_text, lac, cell_id = parse_creg(
                send_at(ser, "AT+CREG?", debug=args.debug)
            )

            row = {
                "log_time_utc": dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="milliseconds"
                ),
                "rssi_raw": rssi_raw,
                "rssi_dbm": rssi_dbm,
                "ber": ber,
                "operator": operator,
                "act": act,
                "reg_stat": reg_stat,
                "reg_stat_text": reg_stat_text,
                "lac": lac,
                "cell_id": cell_id,
            }
            writer.writerow(row)
            f.flush()
            print(
                f"{row['log_time_utc']}  rssi={rssi_dbm}dBm  ber={ber}  "
                f"op={operator}  act={act}  reg={reg_stat_text}  "
                f"lac={lac}  cell={cell_id}"
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        f.close()
        ser.close()


if __name__ == "__main__":
    main()
