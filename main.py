#!/usr/bin/env python3
"""
main.py - Central orchestrator for the v2x-experiment logging stack.

Launches each logging module (GPS now; network x4 and V2X to be added
later) as its own independent subprocess, captures each one's stdout and
stderr to its own log file, and restarts any module that exits
unexpectedly. This script contains NO logging logic itself -- it only
starts, watches, and stops the modules listed in build_modules() below.

Usage:
    python3 main.py

Stop with Ctrl+C, or SIGTERM (e.g. `systemctl stop` once this is running
as a service) -- all child modules are given a chance to shut down
cleanly (closing their CSV files etc.) before this process exits.
"""

import datetime as dt
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs" / "system"
GPS_LOG_DIR = BASE_DIR / "logs" / "gps"

RESTART_BACKOFF_S = 10
POLL_INTERVAL_S = 2
SHUTDOWN_TIMEOUT_S = 10


def build_modules():
    """
    Each entry is one independent module. `cmd` is the full command line
    to launch it (as you'd type it yourself); `-u` keeps Python's stdout
    unbuffered so the log file updates in real time instead of only on
    buffer flush/crash. Add new modules here as they're built -- main.py
    doesn't need any other changes to run them.
    """
    return [
        {
            "name": "gps",
            "cmd": [
                sys.executable,
                "-u",
                str(BASE_DIR / "modules" / "gps" / "gps_logger_rtk.py"),
                "--port",
                "/dev/ttyACM0",
                "--outdir",
                str(GPS_LOG_DIR),
            ],
            "restart_on_exit": True,
        },
        # --- add future modules the same way, e.g.: ---
        # {
        #     "name": "network1",
        #     "cmd": [sys.executable, "-u",
        #             str(BASE_DIR / "modules" / "network" / "net_logger.py"),
        #             "--modem", "/dev/ttyUSB0",
        #             "--outdir", str(BASE_DIR / "logs" / "network1")],
        #     "restart_on_exit": True,
        # },
        # {"name": "network2", "cmd": [...], "restart_on_exit": True},
        # {"name": "network3", "cmd": [...], "restart_on_exit": True},
        # {"name": "network4", "cmd": [...], "restart_on_exit": True},
        # {"name": "v2x",      "cmd": [...], "restart_on_exit": True},
    ]


class Supervisor:
    def __init__(self, modules):
        self.modules = modules
        self.procs = {}
        self.logfiles = {}
        self.stopping = False

    def _log(self, msg):
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        line = f"[{timestamp}] {msg}"
        print(line, flush=True)
        with open(LOG_DIR / "orchestrator.log", "a") as f:
            f.write(line + "\n")

    def start(self, module):
        name = module["name"]
        log_path = (
            LOG_DIR / f"{name}_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M%S}.log"
        )
        f = open(log_path, "a", buffering=1)
        self.logfiles[name] = f
        self._log(f"Starting module '{name}' -> {log_path.name}")
        proc = subprocess.Popen(
            module["cmd"],
            cwd=str(BASE_DIR),
            stdout=f,
            stderr=subprocess.STDOUT,
        )
        self.procs[name] = proc

    def start_all(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        GPS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        for module in self.modules:
            self.start(module)

    def monitor_forever(self):
        while not self.stopping:
            for module in self.modules:
                name = module["name"]
                proc = self.procs.get(name)
                if proc is not None and proc.poll() is not None:
                    self._log(f"Module '{name}' exited with code {proc.returncode}.")
                    self.logfiles[name].close()
                    if module.get("restart_on_exit") and not self.stopping:
                        self._log(f"Restarting '{name}' in {RESTART_BACKOFF_S}s...")
                        time.sleep(RESTART_BACKOFF_S)
                        if not self.stopping:
                            self.start(module)
            time.sleep(POLL_INTERVAL_S)

    def stop_all(self):
        if self.stopping:
            return
        self.stopping = True
        self._log("Stopping all modules...")
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + SHUTDOWN_TIMEOUT_S
        for name, proc in self.procs.items():
            try:
                proc.wait(timeout=max(0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                self._log(f"Module '{name}' didn't stop gracefully, killing.")
                proc.kill()
        for f in self.logfiles.values():
            if not f.closed:
                f.close()
        self._log("All modules stopped.")


def main():
    sup = Supervisor(build_modules())

    def handle_signal(signum, frame):
        sup.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    sup.start_all()
    sup.monitor_forever()  # blocks until a signal fires handle_signal()


if __name__ == "__main__":
    main()
