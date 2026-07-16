#!/usr/bin/env python3
"""
ntrip.py - Minimal NTRIP client that streams RTCM3 corrections from a
caster straight into a u-blox receiver's serial port.

Credentials/settings come from a .env file (see .env.example) instead of
being hardcoded, so they don't end up committed to source control.

Run standalone, just to test the correction stream on its own:
    python3 ntrip.py [serial_port]

Or import and run as a background thread from another script (this is how
gps_logger_rtk.py uses it -- see that file):
    from ntrip import stream_corrections
    threading.Thread(
        target=stream_corrections, args=(ser,), kwargs={"stop_event": stop_event},
        daemon=True,
    ).start()

Dependencies:
    pip install pyserial python-dotenv --break-system-packages
"""

import argparse
import base64
import os
import socket
import sys

import serial
from dotenv import load_dotenv

load_dotenv()  # reads a .env file in the current working directory

NTRIP_SERVER = os.getenv("NTRIP_SERVER", "195.28.70.16")
NTRIP_PORT = int(os.getenv("NTRIP_PORT", "2102"))
NTRIP_MOUNTPOINT = os.getenv("NTRIP_MOUNTPOINT", "BRAT3")
NTRIP_USER = os.getenv("USER", "")
NTRIP_PASSWORD = os.getenv("PASSWORD", "")

DEFAULT_SERIAL_PORT = os.getenv("UBLOX_USB_PORT", "/dev/ttyACM0")


def _basic_auth_header(username, password):
    inputstring = username + ":" + password
    pwd_bytes = base64.encodebytes(inputstring.encode("utf-8"))
    pwd = pwd_bytes.decode("utf-8").replace("\n", "")
    return pwd


def stream_corrections(
    ser,
    server=None,
    port=None,
    mountpoint=None,
    username=None,
    password=None,
    stop_event=None,
    debug=False,
):
    """
    Connects to an NTRIP caster and continuously writes raw RTCM3 bytes
    into `ser`. Blocks until the connection drops or stop_event is set.

    IMPORTANT: `ser` should not be written to by anything else while this
    runs -- see gps_logger_rtk.py, which only ever reads from the same
    handle, never writes, so there's no conflict.
    """
    server = server or NTRIP_SERVER
    port = port or NTRIP_PORT
    mountpoint = mountpoint or NTRIP_MOUNTPOINT
    username = username if username is not None else NTRIP_USER
    password = password if password is not None else NTRIP_PASSWORD

    auth = _basic_auth_header(username, password)
    request = (
        f"GET /{mountpoint} HTTP/1.0\r\n"
        f"User-Agent: NTRIP u-blox\r\n"
        f"Accept: */*\r\n"
        f"Authorization: Basic {auth}\r\n"
        f"Connection: close\r\n\r\n"
    )

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect((server, port))
        s.sendall(request.encode("utf-8"))
        resp = s.recv(2048)

        if resp.startswith(b"SOURCETABLE"):
            print(
                f"Mountpoint '{mountpoint}' not found on {server}:{port} "
                f"-- caster returned a sourcetable listing instead of a stream."
            )
            return
        if not (
            resp.startswith(b"ICY 200")
            or resp.startswith(b"HTTP/1.0 200")
            or resp.startswith(b"HTTP/1.1 200")
        ):
            print(f"NTRIP connection failed. Caster response:\n{resp[:300]!r}")
            return

        print(f"Connected to {server}:{port}/{mountpoint}")
        bytes_forwarded = 0
        s.settimeout(30)  # corrections should arrive at least this often

        while stop_event is None or not stop_event.is_set():
            data = s.recv(1024)
            if not data:
                print("NTRIP connection closed by caster.")
                break
            ser.write(data)
            bytes_forwarded += len(data)
            if debug:
                print(f"(forwarded {len(data)} RTCM bytes, total {bytes_forwarded})")

    except (socket.timeout, socket.error, OSError) as e:
        print(f"NTRIP connection error: {e}")
    finally:
        s.close()


def fetch_sourcetable(server=None, port=None):
    """Requests the caster's sourcetable (the list of valid mountpoints)
    and returns it as text. Use this when a mountpoint name doesn't work
    to see what's actually available on that caster."""
    server = server or NTRIP_SERVER
    port = port or NTRIP_PORT
    request = (
        "GET / HTTP/1.0\r\n"
        "User-Agent: NTRIP u-blox\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n\r\n"
    )
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    chunks = []
    try:
        s.connect((server, port))
        s.sendall(request.encode("utf-8"))
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    finally:
        s.close()
    return b"".join(chunks).decode("utf-8", errors="replace")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "port_or_flag",
        nargs="?",
        default=None,
        help="serial port to stream corrections into, e.g. /dev/ttyACM0",
    )
    ap.add_argument(
        "--list",
        action="store_true",
        help="fetch and print the caster's sourcetable (valid "
        "mountpoints) instead of streaming corrections",
    )
    args = ap.parse_args()

    if args.list:
        print(fetch_sourcetable())
    else:
        port_arg = args.port_or_flag or DEFAULT_SERIAL_PORT
        ser = serial.Serial(
            port_arg, 9600, timeout=2, xonxoff=False, rtscts=False, dsrdtr=False
        )
        ser.flushInput()
        ser.flushOutput()
        try:
            stream_corrections(ser, debug=True)
        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            ser.close()
