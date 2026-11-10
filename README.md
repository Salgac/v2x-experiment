# 6G, CAM and V2X experiment setup


A Raspberry Pi–based mobile data logger for a university V2X research project, deployed on a bus. Currently logs high-precision GPS/RTK position and cellular network parameters; designed to scale to four simultaneous cellular modems and a V2X module. Power-managed via a UPS HAT so it boots when the bus starts and shuts down cleanly (with data sync) when it stops.

## Status

- **GPS/RTK logging: working.**
- **Network (cellular) logging: working** with the Quectel RM530N-GL (registers on Orange SK in 5G NSA / EN-DC). `qnet_logger.py` logs LTE + NR serving-cell metrics at 1 Hz and survives modem dropouts. The original SIMCom SIM8200 could not register on the commercial network (firmware issue) and has been replaced.
- **Modem power: unresolved** — the modem drops off USB every ~0.5–6 min when powered from the Pi; see [Known Issues](#known-issues).
- **Power orchestration (boot-on-power, graceful shutdown, sync, auto re-boot): working**, validated end-to-end.
- **V2X module: not yet built.** `modules/v2x/` is a placeholder for it.
- **Scaling to 4 modems: not yet done.** The code is written to support it (see `connect_all_modems.sh`), but only one physical modem has been wired up and tested so far.

## Hardware

| Component      | Model                                          | Notes                           |
| -------------- | ---------------------------------------------- | ------------------------------- |
| SBC            | Raspberry Pi 4B                                | Raspberry Pi OS Lite            |
| GNSS receiver  | u-blox EVB-C099-F9P (ZED-F9P)                  | USB, RTK-capable                |
| Cellular modem | Waveshare 5G dongle, Quectel RM530N-GL (WS-25928) | USB 3.1, AT + QMI (`usbnet 0`)  |
| Power          | Waveshare UPS HAT (D), 21700 Li-ion            | I2C (INA219 @ 0x43, MCU @ 0x2D) |

## Architecture

`main.py` is a process supervisor, not a logger itself. It launches each logging module as an independent subprocess, restarts any that crash, and captures each one's output to its own log file. Modules know nothing about each other or about `main.py` — each is a standalone script that could be run and tested on its own.

```
bus power on
    -> UPS HAT boots the Pi (hardware feature, arm/re-arm each cycle via modules/ups)
    -> systemd starts v2x-experiment.service (main.py) and ups-monitor.service
    -> main.py launches gps_logger_rtk.py and qnet_logger.py as subprocesses
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
│   │   ├── gps_logger.py            older logger without RTK (not used by main.py)
│   │   ├── gps_logger_rtk.py        live GNSS logger (NAV-PVT + NTRIP/RTK)
│   │   └── ntrip2.py                RTK correction client (NTRIP)
│   │
│   ├── network/
│   │   ├── qnet_logger.py           live LTE/NR logger for Quectel modems (AT+QENG)
│   │   ├── net_logger.py            older generic AT logger (CSQ/COPS/CREG only, not used)
│   │   └── modem/
│   │       ├── at.py                    send AT commands, print responses (diagnostics)
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
6. **Network**: check the modem shows up (`ls /dev/serial/by-id/` -> `usb-Quectel_RM530N-GL_<serial>-if00..if03-port0`; `if02` is the AT port used by the logger, `if03` is free for diagnostics). Confirm SIM and registration: `python3 modules/network/modem/at.py /dev/serial/by-id/...-if03-port0 'AT+CPIN?' 'AT+COPS?' 'AT+QENG="servingcell"'`.
7. **UPS**: run `config/enable_autoboot.py` once. Verify the charge/discharge sign convention with `modules/ups/ups_monitor.py --test` before trusting it (confirmed on this specific unit: **negative = charging, positive = discharging** — already set correctly in the script, but worth re-checking if the HAT is ever swapped).
8. **Install as services**: `sudo ./install.sh` — registers both `main.py` and `ups_monitor.py` as systemd services that start on boot and restart on failure.
9. **Test the full cycle before deploying**: power on normally, confirm both services are running (`systemctl status v2x-experiment ups-monitor`), then pull power and watch the whole shutdown chain complete, then reconnect power and confirm it boots again.

## Running things manually (without systemd)

```bash
cd ~/v2x-experiment
python3 main.py                                          # runs everything main.py knows about
python3 modules/gps/gps_logger_rtk.py --debug             # GPS module alone (confirm this is the live one -- see note above)
python3 modules/network/qnet_logger.py --debug            # network module alone (auto-detects the Quectel AT port)
python3 modules/network/modem/at.py <port> 'AT+QENG="servingcell"'   # one-off AT query
sudo modules/network/modem/connect_and_verify.sh          # bring up + verify one modem
sudo modules/network/modem/connect_all_modems.sh          # same, across all configured modems
sudo modules/network/modem/diagnose.sh                    # full read-only AT+QMI status dump
```

## Known Issues

### Modem drops off USB when powered from the Pi (unresolved)

On 2026-11-10 the RM530N-GL, plugged directly into a Pi USB 3.0 port, disconnected and re-enumerated 11 times in ~27 min of uptime (`dmesg`: `usb 2-1: USB disconnect`), at irregular intervals (20 s – 6 min), and 6 times during a 6-min logging test even when untouched. Each dropout takes ~20 s until the modem answers AT again, so roughly a third of the samples were lost. `vcgencmd get_throttled` reported `0x50000` (under-voltage has occurred since boot). Irregular timing + under-voltage point to insufficient USB power for the 5G modem's transmit current peaks (the Pi 4 has ~1.2 A shared across all USB ports; the Pi itself is fed through the UPS HAT pogo pins).

- **Next step**: test with a powered USB 3.0 hub. A powered hub is needed for 4 modems anyway, and it must stay on the UPS/battery during the shutdown flush.
- `qnet_logger.py` already survives these dropouts (writes `modem_lost` / `modem_up` event rows, waits for the port and continues), so the data shows exactly where the gaps are.

### UPS monitor: false power-loss trigger, shutdown never ran (fixed in code, needs redeploy)

Found on 2026-11-10: `ups-monitor.service` was `inactive (dead)` minutes after boot, so there was no power-loss protection at all. Three bugs combined:
1. **Sign-only detection.** With a full battery the shunt reading hovers around zero (-3..+2) and flips sign randomly; three positive readings in a row were taken as "power lost". Now a threshold is used (`raw > 900`, measured: noise -3..+40 with full battery, ~100..730 right after re-plugging, 1200..1900 with power actually removed).
2. **Shutdown script not executable** (`-rw-rw-r--`), so `sudo shutdown_sequence.sh` failed silently -- a real power loss would not have been handled either. Now invoked via `bash`, and the executable bit is set in git.
3. **`Restart=on-failure`** + the monitor exiting with code 0 after triggering = never restarted. Now `Restart=always`, and the monitor keeps running if the shutdown script returns.

Also: stdout was buffered (all journal lines got the same timestamp at exit) -- now `python3 -u` + per-line timestamps. Paths are derived from the script location instead of `/home/dominik/...`.

Open question: after unplugging and re-plugging the HAT's power input, the reading stayed at ~300-600 (battery still supplying part of the load) instead of returning to ~0, while battery voltage stayed at 4.17 V. Check whether the adapter delivers enough current for Pi + 5G modem (ties in with the modem USB dropouts above).

### Previous modem (SIMCom SIM8200EA-M2) could not register -- resolved by replacement

The SIM8200M44A-M2 (firmware `B02V04_210826`) never completed registration on Orange SK (only NSA is deployed in Slovakia; the modem stalled at 5G SA "limited service"). It was replaced by the Quectel RM530N-GL, which registered on the same network in EN-DC (5G NSA) out of the box. The SIMCom-specific notes in `modules/network/modem/connect_and_verify.sh` describe that old debugging and no longer apply as-is.

### UPS boot-on-power-connect may need re-arming every cycle

Waveshare's documented register write (`0x55` to register `0x01` at I2C address `0x2D`) reads like a one-shot arm rather than a persistent setting. `shutdown_sequence.sh` now re-applies it automatically before every shutdown as a precaution. If boot-on-power-connect ever stops working again, check pogo-pin contact between the HAT and the Pi before assuming it's a software issue.

## Security notes

- `.env` holds real credentials (NTRIP, lab server SSH key path) — never commit it. `.env.example` is the template.
- Lab server sync requires a passwordless (key-based) SSH login, since it runs unattended during shutdown — set up with `ssh-keygen` + `ssh-copy-id`, not a password.
- The SKPOS NTRIP password used during setup should be rotated if it was ever shared outside this project, as routine hygiene.

## Next steps

- Fix modem power (powered USB hub) and re-run the dropout test.
- Wire up modems 2-4: fill in their entries in `connect_all_modems.sh`'s `MODEMS` array once physically installed, using `ls /dev/serial/by-id/` and `modules/network/modem/at.py` per modem; add matching `network2..4` entries in `main.py` (each with its own `/dev/serial/by-id/...-if02-port0`).
- Build the V2X module (`modules/v2x/`).
- Device naming: `main.py` now uses `/dev/serial/by-id/` paths (stable across reboots, keyed to device serial). If 4 identical modems ever need to be tied to fixed physical USB ports instead, add `udev` rules keyed to the port path.
- Bring up the data session over `wwan0` (QMI) without overriding the Pi's Wi-Fi default route (policy routing per modem), for active measurements (latency/throughput). Note: in idle (`NOCONN`) the NR leg is only measured opportunistically, so continuous light traffic is needed to measure connected-mode metrics.
- Time sync from GNSS (gpsd + chrony, ideally PPS): the Pi has no RTC and restores a stale time at boot (seen in `orchestrator.log`).
- Verify UPS battery runtime against real sync/shutdown timing — `shutdown_sequence.sh` currently doesn't enforce a time budget against the battery running out mid-sync.