#!/usr/bin/env python3
"""
qnet_logger.py - LTE / 5G NR network logger for Quectel modems (RM530N-GL,
RM5xx family) via AT commands.

Every --interval seconds it queries:
    AT+QENG="servingcell"   serving-cell metrics (LTE anchor + NR5G-NSA leg)
    AT+COPS?                operator + access technology (13 = EN-DC / 5G NSA)

and writes one CSV row per sample. The raw AT response is stored too, so the
data can be re-parsed later if the parser gets improved.

Survives modem dropouts: if the serial port disappears (USB disconnect,
modem reset), it writes an event row, waits for the port to come back,
reopens it and continues -- it never exits on its own.

Port: prefer the stable /dev/serial/by-id/ path (survives renumbering):
    ls /dev/serial/by-id/
    ...usb-Quectel_RM530N-GL_<serial>-if02-port0  -> AT port
    ...usb-Quectel_RM530N-GL_<serial>-if03-port0  -> second AT port
With --port auto (default) the first Quectel if02 port is used.

Usage:
    python3 qnet_logger.py --outdir ./logs/network1
    python3 qnet_logger.py --port /dev/ttyUSB5 --interval 1 --debug
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

LTE_BW_MHZ = {0: 1.4, 1: 3, 2: 5, 3: 10, 4: 15, 5: 20}
NR_BW_MHZ = {0: 5, 1: 10, 2: 15, 3: 20, 4: 25, 5: 30, 6: 40, 7: 50, 8: 60,
             9: 70, 10: 80, 11: 90, 12: 100, 13: 200, 14: 400}
NR_SCS_KHZ = {0: 15, 1: 30, 2: 60, 3: 120, 4: 240}

FIELDS = [
    "log_time_utc", "mono_s", "event", "state", "operator", "act",
    # LTE (anchor in NSA, or standalone LTE)
    "lte_duplex", "lte_mcc", "lte_mnc", "lte_cell_id", "lte_pci", "lte_earfcn",
    "lte_band", "lte_ul_bw_mhz", "lte_dl_bw_mhz", "lte_tac", "lte_rsrp",
    "lte_rsrq", "lte_rssi", "lte_sinr", "lte_cqi", "lte_tx_power",
    # NR (NSA secondary leg, or SA)
    "nr_mode", "nr_pci", "nr_cell_id", "nr_tac", "nr_arfcn", "nr_band",
    "nr_dl_bw_mhz", "nr_scs_khz", "nr_rsrp", "nr_rsrq", "nr_sinr",
    "raw",
]


# The modem reports "no valid measurement" as -32768 (sometimes scaled, e.g.
# -3276). Anything this low is never a real dB/dBm value -> store as empty.
INVALID_BELOW = -1000


def _num(v):
    """'-96' -> -96, '-' / '--' / '' / sentinel -> None, else unchanged."""
    v = v.strip().strip('"')
    if v in ("", "-", "--"):
        return None
    try:
        n = int(v)
    except ValueError:
        try:
            n = float(v)
        except ValueError:
            return v
    return None if n <= INVALID_BELOW else n


def _split(payload):
    return [p.strip().strip('"') for p in payload.split(",")]


def _lte(vals, row):
    # duplex,MCC,MNC,cellID,PCID,EARFCN,band,UL_bw,DL_bw,TAC,RSRP,RSRQ,RSSI,SINR,CQI,tx_power,srxlev
    keys = ["lte_duplex", "lte_mcc", "lte_mnc", "lte_cell_id", "lte_pci",
            "lte_earfcn", "lte_band", "lte_ul_bw_mhz", "lte_dl_bw_mhz",
            "lte_tac", "lte_rsrp", "lte_rsrq", "lte_rssi", "lte_sinr",
            "lte_cqi", "lte_tx_power"]
    for k, v in zip(keys, vals):
        row[k] = v if k in ("lte_duplex", "lte_mnc", "lte_cell_id", "lte_tac") else _num(v)
    for k in ("lte_ul_bw_mhz", "lte_dl_bw_mhz"):
        if isinstance(row.get(k), int):
            row[k] = LTE_BW_MHZ.get(row[k], row[k])


def _nr_nsa(vals, row):
    # MCC,MNC,PCID,RSRP,SINR,RSRQ,ARFCN,band,DL_bw,SCS
    row["nr_mode"] = "NSA"
    k = ["_mcc", "_mnc", "nr_pci", "nr_rsrp", "nr_sinr", "nr_rsrq",
         "nr_arfcn", "nr_band", "nr_dl_bw_mhz", "nr_scs_khz"]
    for key, v in zip(k, vals):
        if not key.startswith("_"):
            row[key] = _num(v)
    row["nr_dl_bw_mhz"] = NR_BW_MHZ.get(row.get("nr_dl_bw_mhz"), row.get("nr_dl_bw_mhz"))
    row["nr_scs_khz"] = NR_SCS_KHZ.get(row.get("nr_scs_khz"), row.get("nr_scs_khz"))


def _nr_sa(vals, row):
    # duplex,MCC,MNC,cellID,PCID,TAC,ARFCN,band,DL_bw,RSRP,RSRQ,SINR,SCS,srxlev
    row["nr_mode"] = "SA"
    k = ["_dup", "_mcc", "_mnc", "nr_cell_id", "nr_pci", "nr_tac", "nr_arfcn",
         "nr_band", "nr_dl_bw_mhz", "nr_rsrp", "nr_rsrq", "nr_sinr", "nr_scs_khz"]
    for key, v in zip(k, vals):
        if not key.startswith("_"):
            row[key] = v if key in ("nr_cell_id", "nr_tac") else _num(v)
    row["nr_dl_bw_mhz"] = NR_BW_MHZ.get(row.get("nr_dl_bw_mhz"), row.get("nr_dl_bw_mhz"))
    row["nr_scs_khz"] = NR_SCS_KHZ.get(row.get("nr_scs_khz"), row.get("nr_scs_khz"))


def parse_qeng(text):
    """Parses every +QENG line. Handles both layouts seen on RM530N-GL:
       - EN-DC: separate "servingcell", "LTE" and "NR5G-NSA" lines
       - LTE only / SA: everything on the single "servingcell" line."""
    row = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*\+QENG:\s*(.*)$", line)
        if not m:
            continue
        vals = _split(m.group(1))
        if not vals:
            continue
        tag = vals[0]
        if tag == "servingcell":
            row["state"] = vals[1] if len(vals) > 1 else None
            if len(vals) > 2:
                rat = vals[2]
                if rat == "LTE":
                    _lte(vals[3:], row)
                elif rat == "NR5G-SA":
                    _nr_sa(vals[3:], row)
        elif tag == "LTE":
            _lte(vals[1:], row)
        elif tag == "NR5G-NSA":
            _nr_nsa(vals[1:], row)
    return row


ACT = {"7": "LTE", "10": "LTE/5GC", "11": "NR/5GC", "12": "NG-RAN",
       "13": "EN-DC (5G NSA)"}


def parse_cops(text):
    m = re.search(r'\+COPS:\s*\d+,\d+,"([^"]*)"(?:,(\d+))?', text or "")
    if not m:
        return None, None
    return m.group(1), ACT.get(m.group(2), m.group(2))


# ---------------------------------------------------------------------------
# serial handling
# ---------------------------------------------------------------------------
def resolve_port(spec):
    if spec != "auto":
        return spec if os.path.exists(spec) else None
    hits = sorted(glob.glob("/dev/serial/by-id/*Quectel*if02*"))
    return hits[0] if hits else None


def send_at(ser, cmd, timeout=3.0):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("ascii"))
    buf, deadline = b"", time.time() + timeout
    while time.time() < deadline:
        buf += ser.read(512)
        if b"OK\r\n" in buf or b"ERROR" in buf:
            break
    txt = buf.decode("ascii", errors="replace")
    return txt if "OK" in txt else None


def open_modem(port_spec, debug):
    """Blocks until the port exists and answers AT."""
    waited = 0
    while True:
        port = resolve_port(port_spec)
        if port:
            try:
                ser = serial.Serial(port, 115200, timeout=0.5)
                if send_at(ser, "ATE0") is not None:
                    if debug:
                        print(f"modem ready on {port}", flush=True)
                    return ser, port
                ser.close()
            except (serial.SerialException, OSError):
                pass
        if waited % 30 == 0 or debug:
            print(f"waiting for modem ({port_spec}) ... {waited}s", flush=True)
        time.sleep(2)
        waited += 2


class Writer:
    def __init__(self, outdir, rotate_min):
        self.outdir, self.rotate_s = outdir, rotate_min * 60
        os.makedirs(outdir, exist_ok=True)
        self.f = None
        self._open()

    def _open(self):
        if self.f:
            self.f.close()
        name = f"qnet_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M%S}.csv"
        self.f = open(os.path.join(self.outdir, name), "w", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=FIELDS, extrasaction="ignore")
        self.w.writeheader()
        self.opened = time.monotonic()
        print(f"Logging to {self.f.name}", flush=True)

    def write(self, row):
        if self.rotate_s and time.monotonic() - self.opened > self.rotate_s:
            self._open()
        self.w.writerow(row)
        self.f.flush()

    def close(self):
        if self.f:
            self.f.close()


def base_row(event):
    return {"log_time_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
            "mono_s": round(time.monotonic(), 3), "event": event}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="auto",
                    help="serial port, or 'auto' = /dev/serial/by-id/*Quectel*if02*")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--outdir", default="./logs/network1")
    ap.add_argument("--rotate-min", type=float, default=15,
                    help="start a new CSV every N minutes (0 = never)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    out = Writer(args.outdir, args.rotate_min)
    ser, port = open_modem(args.port, args.debug)
    out.write(base_row("modem_up"))
    try:
        while True:
            t0 = time.monotonic()
            try:
                qeng = send_at(ser, 'AT+QENG="servingcell"')
                cops = send_at(ser, "AT+COPS?")
            except (serial.SerialException, OSError) as e:
                print(f"modem lost: {e}", flush=True)
                out.write(base_row("modem_lost"))
                try:
                    ser.close()
                except Exception:
                    pass
                ser, port = open_modem(args.port, args.debug)
                out.write(base_row("modem_up"))
                continue

            row = base_row("sample" if qeng else "no_response")
            row.update(parse_qeng(qeng))
            row["operator"], row["act"] = parse_cops(cops)
            row["raw"] = " | ".join(l.strip() for l in (qeng or "").splitlines()
                                    if l.strip().startswith("+QENG"))
            out.write(row)
            print(f"{row['log_time_utc']} {row.get('state')} {row.get('act')} "
                  f"LTE b{row.get('lte_band')} pci={row.get('lte_pci')} "
                  f"rsrp={row.get('lte_rsrp')} sinr={row.get('lte_sinr')} | "
                  f"NR n{row.get('nr_band')} pci={row.get('nr_pci')} "
                  f"rsrp={row.get('nr_rsrp')} sinr={row.get('nr_sinr')}", flush=True)
            time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        out.write(base_row("stop"))
        out.close()
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
