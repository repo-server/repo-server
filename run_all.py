#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent

def is_windows() -> bool:
    """Check if the operating system is Windows."""
    return os.name == "nt"

def venv_python(venv_dir: Path) -> Path:
    """Return the path to the Python executable in the virtual environment.

    Args:
        venv_dir (Path): Path to the virtual environment directory.

    Returns:
        Path: Full path to the Python executable.
    """
    return venv_dir / ("Scripts/python.exe" if is_windows() else "bin/python")

def wait_for_health(url: str, timeout_s: int = 60, interval_s: float = 1.5) -> None:
    """Wait for a service to become healthy by polling its health check URL.

    Args:
        url (str): The health check URL.
        timeout_s (int): Timeout in seconds.
        interval_s (float): Polling interval in seconds.

    Raises:
        RuntimeError: If health check fails within the timeout.
    """
    start = time.time()
    last_err: Optional[Exception] = None
    while time.time() - start < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except Exception as e:
            last_err = e
        time.sleep(interval_s)
    raise RuntimeError(f"Healthcheck timed out: {url} (last error: {last_err})")

def get_local_ip() -> str:
    """Get the local IPv4 address (LAN). Defaults to 127.0.0.1 on failure.

    Returns:
        str: The local IP address.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def start_api() -> subprocess.Popen:
    """Start the FastAPI service using Uvicorn in a subprocess.

    Returns:
        subprocess.Popen: The process running the FastAPI server.

    Raises:
        FileNotFoundError: If the Python executable in the virtual environment is not found.
    """
    fastapi_dir = ROOT / "fastapi"
    py = venv_python(fastapi_dir / ".venv")
    if not py.exists():
        raise FileNotFoundError(f"FastAPI venv python not found: {py}")

    cmd = [
        str(py),
        "-m", "uvicorn",
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--reload",
    ]

    print(f"[api] Starting: {' '.join(cmd)} (cwd={fastapi_dir})")
    proc = subprocess.Popen(cmd, cwd=str(fastapi_dir))

    urls_to_check = [
        "http://127.0.0.1:8000/health",
        "http://localhost:8000/health",
    ]

    for url in urls_to_check:
        try:
            print(f"[api] Waiting for health @ {url}")
            wait_for_health(url, timeout_s=30)
            print(f"[api] Healthy @ {url}")
        except Exception as e:
            print(f"[api] Health check failed for {url} → {e}")

    return proc

def start_streamlit() -> subprocess.Popen:
    """Start the Streamlit UI service in a subprocess.

    Returns:
        subprocess.Popen: The process running the Streamlit server.

    Raises:
        FileNotFoundError: If the Python executable in the virtual environment is not found.
    """
    ui_dir = ROOT / "streamlit"
    py = venv_python(ui_dir / ".venv")
    if not py.exists():
        raise FileNotFoundError(f"Streamlit venv python not found: {py}")

    cmd = [
        str(py), "-m", "streamlit", "run", "app.py",
        "--server.address", "0.0.0.0",
        "--server.port", "8501",
    ]
    print(f"[ui] Starting: {' '.join(cmd)} (cwd={ui_dir})")
    proc = subprocess.Popen(cmd, cwd=str(ui_dir))

    loopback_url = "http://127.0.0.1:8501"
    print(f"[ui] Waiting for health @ {loopback_url}")
    wait_for_health(loopback_url, timeout_s=60)
    print(f"[ui] Healthy @ {loopback_url}")

    local_ip = get_local_ip()
    local_ip_url = f"http://{local_ip}:8501"

    print(f"[ui] Access from this machine: {loopback_url}")
    print(f"[ui] Access from other devices on LAN: {local_ip_url}")

    try:
        webbrowser.open(local_ip_url)
    except Exception:
        webbrowser.open(loopback_url)

    return proc

def terminate(proc: Optional[subprocess.Popen]) -> None:
    """Terminate the given subprocess gracefully.

    Args:
        proc (Optional[subprocess.Popen]): The process to terminate.
    """
    if not proc:
        return
    try:
        if is_windows():
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass

def main() -> None:
    """Main function to start and monitor both API and UI services."""
    api_proc = ui_proc = None
    try:
        api_proc = start_api()
        ui_proc = start_streamlit()
        print("All services are up. Press CTRL+C to exit.")
        while True:
            code_api = api_proc.poll()
            code_ui = ui_proc.poll()
            if code_api is not None:
                print(f"[api] Exited with code {code_api}")
                break
            if code_ui is not None:
                print(f"[ui] Exited with code {code_ui}")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCTRL+C received, shutting down...")
    finally:
        terminate(ui_proc)
        terminate(api_proc)

if __name__ == "__main__":
    main()
