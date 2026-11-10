#!/usr/bin/env python3
"""
at.py - send one or more AT commands to a modem and print the responses.
Read-only diagnostic helper; does not change anything unless you send a
command that does.

Usage:
    python3 at.py <port> <command> [<command> ...]

Example (Quectel RM530N-GL, second AT port so it doesn't clash with the logger):
    python3 at.py /dev/serial/by-id/usb-Quectel_RM530N-GL_<serial>-if03-port0 \\
        ATI 'AT+COPS?' 'AT+QNWINFO' 'AT+QENG="servingcell"'
"""
import sys
import time

import serial

if len(sys.argv) < 3:
    sys.exit(__doc__)

s = serial.Serial(sys.argv[1], 115200, timeout=1)
for cmd in sys.argv[2:]:
    s.reset_input_buffer()
    s.write((cmd + "\r\n").encode())
    out, t = b"", time.time()
    while time.time() - t < 3:
        out += s.read(512)
        if b"OK\r\n" in out or b"ERROR" in out:
            break
    print(out.decode(errors="replace").strip())
    print("-" * 40)
