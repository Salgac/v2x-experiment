# 6G, CAM and V2X experiment setup


A Raspberry Pi–based mobile data logger for a university V2X research project, deployed on a bus. Currently logs high-precision GPS/RTK position and cellular network parameters; designed to scale to four simultaneous cellular modems and a V2X module. Power-managed via a UPS HAT so it boots when the bus starts and shuts down cleanly (with data sync) when it stops.

## Status

- **GPS/RTK logging: working.**
- **Network (cellular) logging: blocked on an unresolved modem issue** — see [Known Issues](#known-issues) below. The logging code itself works; the modem currently cannot complete registration on the commercial network.
- **Power orchestration (boot-on-power, graceful shutdown, sync, auto re-boot): working**, validated end-to-end.
- **V2X module: not yet built.** `modules/v2x/` is a placeholder for it.
- **Scaling to 4 modems: not yet done.** The code is written to support it (see `connect_all_modems.sh`), but only one physical modem has been wired up and tested so far.

## Hardware

| Component      | Model                                          | Notes                           |
| -------------- | ---------------------------------------------- | ------------------------------- |
| SBC            | Raspberry Pi 4B                                | Raspberry Pi OS Lite            |
| GNSS receiver  | u-blox EVB-C099-F9P (ZED-F9P)                  | USB, RTK-capable                |
| Cellular modem | Waveshare SIM8200EA-M2 (SIMCom SIM8200M44A-M2) | USB, AT + QMI                   |
| Power          | Waveshare UPS HAT (D), 21700 Li-ion            | I2C (INA219 @ 0x43, MCU @ 0x2D) |

## Architecture

`main.py` is a process supervisor, not a logger itself. It launches each logging module as an independent subprocess, restarts any that crash, and captures each one's output to its own log file. Modules know nothing about each other or about `main.py` — each is a standalone script that could be run and tested on its own.

```
bus power on
    -> UPS HAT boots the Pi (hardware feature, arm/re-arm each cycle via modules/ups)
    -> systemd starts v2x-experiment.service (main.py) and ups-monitor.service
    -> main.py launches gps_logger.py and net_logger.py as subprocesses
    -> ups_monitor.py watches battery charge/discharge direction over I2C

bus power off
    -> ups_monitor.py detects the direction flip within ~15-30s
    -> runs modules/shutdown_sequence.sh:
         1. SIGTERM to main.py -> every module stops cleanly, files closed
         2. modules/sync_and_cleanup.sh: rsync logs to the lab server,
            verify, delete locally ONLY if verified (left in place otherwise)
         3. re-arm UPS boot-on-power-connect
         4. shutdown -h now
```

## Directory layout

```
v2x-experiment/
├── main.py                          orchestrator
├── install.sh                       one-time systemd setup
├── requirements.txt
├── v2x-experiment.service.example
├── ups-monitor.service.example
├── .env                             real secrets -- gitignored, never commit
├── .env.example
├── .gitignore
│
├── config/                          one-time setup scripts (run manually, not by main.py)
│   ├── configure_receiver.py        enable NAV-PVT output on the GNSS receiver
│   └── enable_autoboot.py           arm UPS boot-on-power-connect
│
├── modules/
│   ├── shutdown_sequence.sh         graceful stop -> sync -> re-arm -> power off
│   ├── sync_and_cleanup.sh          rsync + verify-before-delete
│   │
│   ├── gps/
│   │   ├── gps_logger.py            (?) confirm which of these two is live -- see note above
│   │   ├── gps_logger_rtk.py        (?)
│   │   └── ntrip2.py                RTK correction client (NTRIP) -- confirm import matches this name
│   │
│   ├── network/
│   │   ├── net_logger.py            continuous AT-layer network parameter logger
│   │   └── modem/
│   │       ├── connect_and_verify.sh    QMI-native modem bring-up + connectivity check
│   │       ├── connect_all_modems.sh    runs connect_and_verify.sh across all modems
│   │       ├── diagnose.sh              one-shot read-only AT+QMI status dump
│   │       └── unlock_sim.py            safety-checked SIM PIN unlock
│   │
│   ├── ups/
│   │   └── ups_monitor.py           watches for power loss, triggers shutdown_sequence.sh
│   │
│   └── v2x/                         placeholder -- not yet built
│
├── old/                             retired/superseded files, kept for reference
│   └── ntrip.py
│
├── logs/                            created at runtime
│   ├── gps/
│   └── system/                      orchestrator.log + each module's stdout/stderr
└── sync_history/                    created at runtime, sync operation logs
```

> Some scripts from earlier in this project (`check_band_config.py`, `check_5g_mode.py`, `reset_net_mode.py`, `ue_connect.sh`, `full_reset.sh`) aren't in the current tree. Most of their functionality is folded into `diagnose.sh`; `ue_connect.sh` (private open5gs core use case) may just not be shown here — confirm whether it's been moved, archived in `old/`, or genuinely dropped.

## Setup

1. **Flash Raspberry Pi OS Lite**, enable SSH and I2C (`sudo raspi-config` -> Interfacing Options).
2. **Wire up hardware**: GNSS antenna on the correct SMA port (not the ODIN-W2/WiFi port), cellular antennas connected, UPS HAT seated with mounting screws tightened (poor pogo-pin contact is a known issue on this HAT family).
3. **Clone/copy this project** to `~/v2x-experiment` and install dependencies:
   ```bash
   pip install -r requirements.txt --break-system-packages
   pip install smbus2 python-dotenv --break-system-packages
   ```
4. **Configure secrets**: `cp .env.example .env`, fill in NTRIP credentials and lab server sync details.
5. **GPS**: run `config/configure_receiver.py` once (enables NAV-PVT output on the receiver's USB port, needed since it's off by default).
6. **Network**: find the modem's AT port (`modules/network/net_logger.py --probe`), unlock the SIM if needed (`modules/network/modem/unlock_sim.py`), confirm registration (`modules/network/modem/connect_and_verify.sh`) — currently blocked, see Known Issues.
7. **UPS**: run `config/enable_autoboot.py` once. Verify the charge/discharge sign convention with `modules/ups/ups_monitor.py --test` before trusting it (confirmed on this specific unit: **negative = charging, positive = discharging** — already set correctly in the script, but worth re-checking if the HAT is ever swapped).
8. **Install as services**: `sudo ./install.sh` — registers both `main.py` and `ups_monitor.py` as systemd services that start on boot and restart on failure.
9. **Test the full cycle before deploying**: power on normally, confirm both services are running (`systemctl status v2x-experiment ups-monitor`), then pull power and watch the whole shutdown chain complete, then reconnect power and confirm it boots again.

## Running things manually (without systemd)

```bash
cd ~/v2x-experiment
python3 main.py                                          # runs everything main.py knows about
python3 modules/gps/gps_logger_rtk.py --debug             # GPS module alone (confirm this is the live one -- see note above)
python3 modules/network/net_logger.py --probe             # find the modem's AT port
sudo modules/network/modem/connect_and_verify.sh          # bring up + verify one modem
sudo modules/network/modem/connect_all_modems.sh          # same, across all configured modems
sudo modules/network/modem/diagnose.sh                    # full read-only AT+QMI status dump
```

## Known Issues

> **Check this first**: `main.py`'s `build_modules()` has a hardcoded path to the GPS logger script. Confirm it points at whichever of `gps_logger.py` / `gps_logger_rtk.py` is actually the live one (see the note at the top of the directory layout above) — if it's pointing at the stale file, GPS logging would silently run old/wrong code under `main.py` even though manual testing of the other file looks fine.

### Cellular modem cannot register on the commercial network (unresolved)

The SIM8200M44A-M2 (firmware `B02V04_210826`, ~2021) was previously configured for **SA-only 5G** operation against a private open5gs core. Slovakia's public networks only offer **NSA** (LTE+NR dual connectivity) — there is no public SA deployment here. Despite extensive, systematic reconfiguration across both the AT-command layer and the QMI layer, the modem still cannot complete registration on Orange SK with the same SIM:

- **Confirmed NOT the cause**: SIM/account/coverage (works perfectly in a phone at the same location — full LTE band 20 + 5G NSA), SIM PIN state, band restrictions (`AT+CNBP` and `AT+CSYSSEL` both fully decoded and confirmed wide-open, including LTE band 20 and broad NR SA/NSA bands), RAT acquisition order, carrier/network personalization locks (all confirmed inactive via `AT+CLCK`), and `qmicli`'s "technology preference" / "mode preference" (confirmed set correctly to `lte, 5gnr` via read-back).
- **What we can see**: `qmicli --nas-get-system-info` consistently shows `LTE service: Status: 'none'` while `5G SA service` reaches `'limited'` (camps on a real NR cell, gets a valid Tracking Area Code) and then stalls — the modem appears to structurally never attempt an LTE-anchored NSA attach, regardless of configuration.
- **Next step**: a detailed support request has been sent to SIMCom (`support@simcom.com`) with the full diagnostic history. A deep-capture debugging attempt via `scat` (Qualcomm DIAG protocol capture) was planned but not completed.
- Full diagnostic history, every command tried, and reasoning is preserved in this project's chat history / a `TROUBLESHOOTING.md` if one has been written since.

**Practical implication**: `net_logger.py` itself works correctly and will log real data the moment registration succeeds — this is a modem/firmware issue, not a code issue. `PLMN` in `connect_and_verify.sh` defaults to `automatic`; it was briefly pinned manually to Orange's PLMN (`23101`) during debugging but that wasn't the fix and isn't appropriate as a default across different carriers on future modems.

### UPS boot-on-power-connect may need re-arming every cycle

Waveshare's documented register write (`0x55` to register `0x01` at I2C address `0x2D`) reads like a one-shot arm rather than a persistent setting. `shutdown_sequence.sh` now re-applies it automatically before every shutdown as a precaution. If boot-on-power-connect ever stops working again, check pogo-pin contact between the HAT and the Pi before assuming it's a software issue.

## Security notes

- `.env` holds real credentials (NTRIP, lab server SSH key path) — never commit it. `.env.example` is the template.
- Lab server sync requires a passwordless (key-based) SSH login, since it runs unattended during shutdown — set up with `ssh-keygen` + `ssh-copy-id`, not a password.
- The SKPOS NTRIP password used during setup should be rotated if it was ever shared outside this project, as routine hygiene.

## Next steps

- Resolve the modem registration issue (pending SIMCom's reply).
- Wire up modems 2-4: fill in their entries in `connect_all_modems.sh`'s `MODEMS` array once physically installed, using `net_logger.py --probe` and `diagnose.sh` per modem the same way modem 1 was brought up.
- Build the V2X module (`modules/v2x/`).
- Solve persistent device naming (`/dev/ttyUSB*`/`/dev/ttyACM*` numbering shifts across reboots and when other USB devices are plugged in) via `udev` rules keyed to physical USB port path — deferred earlier, now more urgent with 4 modems planned.
- Verify UPS battery runtime against real sync/shutdown timing — `shutdown_sequence.sh` currently doesn't enforce a time budget against the battery running out mid-sync.