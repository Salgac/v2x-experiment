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

IMPORTANT: the sign convention (does a positive or negative raw reading
mean "charging" vs "discharging") is standard for this chip family but
NOT independently confirmed against your exact physical unit in this
session. Run this in --test mode FIRST and manually plug/unplug USB power
while watching the printed values, to confirm which sign means what on
your actual hardware, before trusting the automated trigger.

Usage:
    pip install smbus2 --break-system-packages

    # verify the sign convention on your actual hardware first:
    python3 ups_monitor.py --test

    # once confirmed, run for real (as a systemd service -- see
    # ups-monitor.service):
    sudo python3 ups_monitor.py
"""

import argparse
import subprocess
import sys
import time

try:
    from smbus2 import SMBus
except ImportError:
    sys.exit("Missing dependency: pip install smbus2 --break-system-packages")

INA219_ADDR = 0x43
SHUNT_VOLTAGE_REG = 0x01
I2C_BUS = 1

SHUTDOWN_SCRIPT = "/home/dominik/v2x-experiment/modules/shutdown_sequence.sh"
POLL_INTERVAL_S = 5
DEBOUNCE_READINGS = 3  # consecutive "discharging" readings required
# before triggering, to ignore noise/blips
# Confirmed empirically on this hardware: negative = charging (power
# connected), positive = discharging (power disconnected) -- the opposite
# of the initial assumption.
NEGATIVE_MEANS_DISCHARGING = False


def read_raw_shunt_voltage(bus):
    """Reads the INA219 shunt voltage register as a signed 16-bit value.
    Unlike the Current register (0x04), this one is valid immediately on
    power-up with no calibration step needed -- we only care about its
    SIGN (charging vs discharging direction), not the calibrated mA value,
    which we don't have a confirmed calibration constant for on this board."""
    data = bus.read_i2c_block_data(INA219_ADDR, SHUNT_VOLTAGE_REG, 2)
    raw = (data[0] << 8) | data[1]
    if raw > 32767:
        raw -= 65536
    return raw


def is_discharging(raw):
    return (raw < 0) if NEGATIVE_MEANS_DISCHARGING else (raw > 0)


def test_mode():
    print("Test mode -- reading raw current every 2s. Plug/unplug the USB-C")
    print("power to the HAT and watch how the sign changes, to confirm the")
    print(
        f"convention. Currently assuming: negative = discharging = "
        f"{NEGATIVE_MEANS_DISCHARGING}"
    )
    print("Ctrl+C to stop.\n")
    bus = SMBus(I2C_BUS)
    try:
        while True:
            raw = read_raw_shunt_voltage(bus)
            state = (
                "DISCHARGING (no external power)"
                if is_discharging(raw)
                else "CHARGING/idle (external power present)"
            )
            print(f"raw={raw:>7}  -> {state}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")


def monitor_mode():
    bus = SMBus(I2C_BUS)
    consecutive_discharging = 0
    print("Monitoring for power loss (Ctrl+C to stop)...")
    while True:
        try:
            raw = read_raw_shunt_voltage(bus)
        except OSError as e:
            print(f"I2C read error: {e} -- retrying in {POLL_INTERVAL_S}s")
            time.sleep(POLL_INTERVAL_S)
            continue

        if is_discharging(raw):
            consecutive_discharging += 1
            print(
                f"raw={raw} discharging ({consecutive_discharging}/{DEBOUNCE_READINGS})"
            )
        else:
            if consecutive_discharging > 0:
                print(f"raw={raw} charging again -- resetting debounce counter")
            consecutive_discharging = 0

        if consecutive_discharging >= DEBOUNCE_READINGS:
            print("Power loss confirmed -- triggering shutdown sequence.")
            subprocess.run(["sudo", SHUTDOWN_SCRIPT])
            return  # shutdown_sequence.sh ends in `shutdown -h now`; nothing more to do

        time.sleep(POLL_INTERVAL_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--test",
        action="store_true",
        help="print raw readings only, don't trigger anything",
    )
    args = ap.parse_args()

    if args.test:
        test_mode()
    else:
        monitor_mode()


if __name__ == "__main__":
    main()
