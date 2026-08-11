#!/usr/bin/env python3
"""
enable_autoboot.py - ONE-TIME setup: tells the UPS HAT (D)'s onboard MCU
to boot the Raspberry Pi automatically whenever external (bus) power is
connected, per Waveshare's documented register for this HAT.

From Waveshare's wiki for UPS HAT (D): writing 0x55 to register 0x01 on
the MCU (I2C address 0x2D) makes the MCU start checking for external
power after ~30s, and pull GPIO3 low to boot the Pi once power is found.

IMPORTANT (per Waveshare's own instructions): after running this, power
off the Pi soon after (it does NOT need to stay running) -- the
auto-boot-on-power behavior only arms correctly if the Pi is off when
external power is next connected. This is a one-time setup step, not
something to run on every boot.

Usage:
    pip install smbus2 --break-system-packages
    sudo python3 enable_autoboot.py
"""

import sys

try:
    from smbus2 import SMBus
except ImportError:
    sys.exit("Missing dependency: pip install smbus2 --break-system-packages")

MCU_ADDR = 0x2D
REG = 0x01
VALUE = 0x55
I2C_BUS = 1  # standard Pi I2C bus


def main():
    try:
        bus = SMBus(I2C_BUS)
    except FileNotFoundError:
        sys.exit(
            "I2C bus not found -- enable it first: sudo raspi-config -> "
            "Interfacing Options -> I2C -> yes, then reboot."
        )

    print(
        f"Writing 0x{VALUE:02X} to register 0x{REG:02X} at address 0x{MCU_ADDR:02X}..."
    )
    bus.write_byte_data(MCU_ADDR, REG, VALUE)

    readback = bus.read_byte_data(MCU_ADDR, REG)
    print(f"Register now reads: 0x{readback:02X}")
    if readback == VALUE:
        print("Set successfully.")
    else:
        print(
            "WARNING: readback doesn't match what was written -- "
            "double check the HAT is seated correctly and I2C is working."
        )

    print()
    print("Auto-boot-on-power-connect is now armed. Per Waveshare's own")
    print("instructions: power this Pi off soon (it doesn't need to stay")
    print("running) so the behavior takes effect correctly next time")
    print("external power is connected.")


if __name__ == "__main__":
    main()
