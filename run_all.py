#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict

# =========================
# Root path for the project
# =========================
ROOT = Path(__file__).resolve().parent
CFG_FILE = ROOT / "servers.json"


# =========================
# Helpers
# =========================
def healthcheck(url: str, timeout_s: float = 20.0) -> bool:
    """
    Synchronous health check using urllib.request.

    Args:
        url (str): The URL to check.
        timeout_s (float): Timeout in seconds.

    Returns:
        bool: True if 200 OK is received, False otherwise.
    """
    end_time = time.time() + timeout_s
    while time.time() < end_time:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def build_cmd(svc: Dict[str, Any]) -> list[str]:
    """
    Build the command list to launch a service based on its configuration.

    Args:
        svc (Dict[str, Any]): The service configuration dictionary.

    Returns:
        list[str]: A list representing the command to execute.
    """
    exe = svc.get("python_exe")
    if exe:
        return [exe, *svc["cmd"]]
    return [sys.executable, *svc["cmd"]]


# =========================
# Main Runner
# =========================
def main() -> None:
    """
    Main function to read service configurations, launch services,
    monitor their health, and terminate them gracefully on exit.
    """
    if not CFG_FILE.exists():
        print(f"Config not found: {CFG_FILE}")
        sys.exit(1)

    cfg = json.loads(CFG_FILE.read_text(encoding="utf-8"))
    services: Dict[str, Dict[str, Any]] = cfg.get("services", {})

    # Convert relative paths to absolute
    for svc in services.values():
        if "cwd" in svc:
            svc["cwd"] = str(ROOT / svc["cwd"])
        if "python_exe" in svc:
            svc["python_exe"] = str(ROOT / svc["python_exe"])

    # Select services from command line arguments
    wanted = set(sys.argv[1:])
    order = cfg.get("launch_order", list(services.keys()))

    procs: Dict[str, subprocess.Popen] = {}

    try:
        for name in order:
            if wanted and name not in wanted:
                continue

            svc = services[name]
            cmd = build_cmd(svc)

            print(f"[{name}] Starting: {' '.join(cmd)}  (cwd={svc['cwd']})")
            p = subprocess.Popen(
                cmd,
                cwd=svc["cwd"],
                env={**os.environ, **svc.get("env_from", {})},
            )
            procs[name] = p

            # Health check
            if "health" in svc:
                ok = healthcheck(svc["health"], svc.get("health_timeout", 20.0))
                print(f"[{name}] {'Healthy' if ok else 'Health timeout'} @ {svc['health']}")
                if not ok:
                    raise RuntimeError(f"{name} failed healthcheck")

            # Open browser if service is streamlit
            if name == "streamlit":
                url_to_open = svc.get("exports", {}).get("url")
                if url_to_open:
                    try:
                        webbrowser.open(url_to_open)
                    except Exception:
                        pass

        print("All services launched. Press Ctrl+C to stop.")

        # Monitor processes
        while True:
            time.sleep(1.0)
            for name, p in procs.items():
                if p.poll() is not None:
                    code = p.returncode
                    print(f"[{name}] Exited with code {code}. Stopping all...")
                    raise SystemExit(code if code is not None else 1)

    except KeyboardInterrupt:
        print("\nCaught Ctrl+C, stopping all...")
    finally:
        for name, p in procs.items():
            if p.poll() is None:
                print(f"[{name}] Terminating...")
                try:
                    p.send_signal(signal.SIGTERM)
                except Exception:
                    pass
        time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass