#!/usr/bin/env python3
"""
ups_monitor.py - Watches the UPS HAT (D)'s battery current direction over
I2C (INA219 at address 0x43, shunt voltage register) and triggers
shutdown_sequence.sh as soon as it detects the battery has switched from
charging to discharging -- i.e. external (bus) power was just lost. This
reacts within seconds, unlike waiting for the battery to drain to a
low-percentage threshold.

Reads the Shunt Voltage register (0x01) rather than the Current register
(0x04): Current requires a calibration value written to register 0x05
first and reads a constant 0 without it. Shunt voltage is valid
immediately on power-up, needs no calibration, and shares the same sign
convention (direction of current flow) -- all we need, since we only care
about charging vs discharging, not the precise calibrated mA value.

Power loss = the raw reading staying ABOVE A THRESHOLD (default 900) for
3 consecutive polls -- not merely positive. With a full battery the reading
hovers around zero and flips sign randomly; the old sign-only check turned
that noise into a false power-loss trigger. Measured bands on this unit are
listed next to DEFAULT_THRESHOLD_RAW below; re-check them with --test (it
also prints battery voltage) whenever the hardware or load changes.

Usage:
    pip install smbus2 --break-system-packages

    # check the readings on your hardware first (unplug/re-plug power):
    sudo python3 ups_monitor.py --test

    # once confirmed, run for real (as a systemd service -- see
    # ups-monitor.service):
    sudo python3 ups_monitor.py
"""

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

try:
    from smbus2 import SMBus
except ImportError:
    sys.exit("Missing dependency: pip install smbus2 --break-system-packages")

INA219_ADDR = 0x43
SHUNT_VOLTAGE_REG = 0x01
BUS_VOLTAGE_REG = 0x02
I2C_BUS = 1

# Resolved relative to this file, so it works wherever the project is cloned.
SHUTDOWN_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "shutdown_sequence.sh"
)
POLL_INTERVAL_S = 5
DEBOUNCE_READINGS = 3  # consecutive readings above threshold required

# Measured on this unit on 2026-11-10 (raw shunt register, positive =
# battery discharging):
#   external power present, battery full ........ -3 .. +2, rare spikes ~40
#   external power present, after re-plug ....... ~100 .. ~730 (adapter
#                                                  covering only part of the load)
#   external power REMOVED ........................ ~1200 .. ~1900
# The old logic triggered on the SIGN alone (raw > 0), so noise around zero
# with a full battery caused a false "power loss" a few minutes after boot.
# A threshold between the two bands fixes that. Re-check with --test if the
# HAT, battery, adapter or load (number of modems) changes.
DEFAULT_THRESHOLD_RAW = 900


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_raw_shunt_voltage(bus):
    """INA219 shunt voltage register as a signed 16-bit value (10 uV/LSB).
    Valid immediately on power-up, no calibration needed. Positive on this
    board = battery discharging."""
    data = bus.read_i2c_block_data(INA219_ADDR, SHUNT_VOLTAGE_REG, 2)
    raw = (data[0] << 8) | data[1]
    if raw > 32767:
        raw -= 65536
    return raw


def read_battery_v(bus):
    data = bus.read_i2c_block_data(INA219_ADDR, BUS_VOLTAGE_REG, 2)
    return (((data[0] << 8) | data[1]) >> 3) * 0.004


def test_mode(threshold):
    print("Test mode -- prints readings every 2 s, never triggers anything.")
    print("Unplug / re-plug the power cable to the UPS HAT and watch the values.")
    print(f"Current threshold: raw > {threshold} counts as power loss.")
    print("Ctrl+C to stop.\n", flush=True)
    bus = SMBus(I2C_BUS)
    try:
        while True:
            raw = read_raw_shunt_voltage(bus)
            v = read_battery_v(bus)
            state = "POWER LOSS" if raw > threshold else "ok (external power)"
            print(f"{now()}  raw={raw:>6}  batt={v:.2f} V  -> {state}", flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")


def monitor_mode(threshold):
    bus = SMBus(I2C_BUS)
    above = 0
    print(f"{now()} Monitoring for power loss (threshold raw > {threshold}, "
          f"{DEBOUNCE_READINGS} readings x {POLL_INTERVAL_S}s)...", flush=True)
    while True:
        try:
            raw = read_raw_shunt_voltage(bus)
        except OSError as e:
            print(f"{now()} I2C read error: {e} -- retrying", flush=True)
            time.sleep(POLL_INTERVAL_S)
            continue

        if raw > threshold:
            above += 1
            print(f"{now()} raw={raw} above threshold ({above}/{DEBOUNCE_READINGS})",
                  flush=True)
        else:
            if above:
                print(f"{now()} raw={raw} back below threshold -- reset", flush=True)
            above = 0

        if above >= DEBOUNCE_READINGS:
            print(f"{now()} Power loss confirmed -- running {SHUTDOWN_SCRIPT}",
                  flush=True)
            # Run through bash so a missing +x bit can't silently block it
            # (that is exactly what happened on 2026-11-10).
            rc = subprocess.run(["bash", SHUTDOWN_SCRIPT]).returncode
            # shutdown_sequence.sh ends in `shutdown -h now`; if we get here,
            # it failed -- keep monitoring instead of exiting.
            print(f"{now()} shutdown_sequence.sh returned {rc} -- still running, "
                  f"monitoring continues", flush=True)
            above = 0

        time.sleep(POLL_INTERVAL_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true",
                    help="print readings only, don't trigger anything")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD_RAW,
                    help=f"raw shunt value treated as power loss (default "
                         f"{DEFAULT_THRESHOLD_RAW})")
    args = ap.parse_args()
    if args.test:
        test_mode(args.threshold)
    else:
        monitor_mode(args.threshold)


if __name__ == "__main__":
    main()
