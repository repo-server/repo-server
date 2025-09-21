#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from typing import Dict, Any, List

SERVERS_FILE = "servers.json"

def load_config(path: str) -> Dict[str, Any]:
    """Load JSON configuration from a given file path.

    Args:
        path (str): Path to the configuration file.

    Returns:
        Dict[str, Any]: Parsed JSON content as a dictionary.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def topo_sort(services: Dict[str, Any]) -> List[str]:
    """Topologically sort services based on their dependencies.

    Args:
        services (Dict[str, Any]): Dictionary of service configurations.

    Returns:
        List[str]: Sorted list of service names.

    Raises:
        RuntimeError: If circular or unknown dependencies are found.
    """
    seen = set()
    order: List[str] = []
    temp = set()

    def visit(name: str):
        if name in seen:
            return
        if name in temp:
            raise RuntimeError(f"Circular dependency at {name}")
        temp.add(name)
        deps = services[name].get("depends_on", [])
        for d in deps:
            if d not in services:
                raise RuntimeError(f"Unknown dependency '{d}' for '{name}'")
            visit(d)
        temp.remove(name)
        seen.add(name)
        order.append(name)

    for n in services:
        visit(n)
    return order

def resolve_env_from(name: str, svc: Dict[str, Any], exports: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Resolve environment variables from other services' exports.

    Args:
        name (str): Current service name.
        svc (Dict[str, Any]): Current service configuration.
        exports (Dict[str, Dict[str, str]]): Exported variables from other services.

    Returns:
        Dict[str, str]: Resolved environment variables.

    Raises:
        RuntimeError: If references are malformed or missing.
    """
    env = {}
    mapping: Dict[str, str] = svc.get("env_from", {})
    for env_key, ref in mapping.items():
        if ref.startswith("${") and ref.endswith("}"):
            body = ref[2:-1]
            if ":" not in body:
                raise RuntimeError(f"[{name}] bad env_from ref '{ref}'")
            sname, key = body.split(":", 1)
            val = exports.get(sname, {}).get(key)
            if val is None:
                raise RuntimeError(f"[{name}] missing export {ref}")
            env[env_key] = str(val)
        else:
            env[env_key] = str(ref)
    return env

def build_cmd(cmd: List[str], python_exe: str = None) -> List[str]:
    """Construct the command to be executed for the service.

    Args:
        cmd (List[str]): Command list from config.
        python_exe (str, optional): Specific Python executable to use.

    Returns:
        List[str]: Final command list.
    """
    if cmd and cmd[0] == "-m":
        return [python_exe or sys.executable] + cmd
    return cmd

def start_service(name: str, svc: Dict[str, Any], base_env: Dict[str, str]) -> subprocess.Popen:
    """Start a service subprocess.

    Args:
        name (str): Name of the service.
        svc (Dict[str, Any]): Service configuration.
        base_env (Dict[str, str]): Environment variables for the subprocess.

    Returns:
        subprocess.Popen: The running process.

    Raises:
        RuntimeError: If the command is empty.
    """
    python_exe = svc.get("python_exe")
    cmd = build_cmd(svc.get("cmd", []), python_exe=python_exe)
    if not cmd:
        raise RuntimeError(f"[{name}] empty cmd")
    cwd = svc.get("cwd") or None
    env = base_env.copy()
    for k, v in svc.get("exports", {}).items():
        env_name = f"SELF_{k}"
        env[env_name] = str(v)
    print(f"[{name}] starting: {' '.join(cmd)}  (cwd={cwd or os.getcwd()})")
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

def healthcheck(url: str, timeout_s: float = 20.0) -> bool:
    """Check if a service is healthy by polling a URL.

    Args:
        url (str): URL to poll.
        timeout_s (float): Maximum time to wait.

    Returns:
        bool: True if service is healthy, False otherwise.
    """
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with urlopen(url, timeout=3) as r:
                if 200 <= r.status < 400:
                    return True
        except URLError:
            pass
        time.sleep(0.6)
    return False

def main() -> None:
    """Main function to launch and monitor services defined in a JSON file."""
    cfg = load_config(SERVERS_FILE)
    services: Dict[str, Any] = cfg["services"]
    order = topo_sort(services)
    print("Launch order:", " -> ".join(order))

    exports: Dict[str, Dict[str, str]] = {}
    procs: Dict[str, subprocess.Popen] = {}

    try:
        for name in order:
            svc = services[name]
            base_env = os.environ.copy()
            for dep in svc.get("depends_on", []):
                for k, v in services[dep].get("exports", {}).items():
                    base_env[f"{dep.upper()}_{k}"] = str(v)
            base_env.update(resolve_env_from(name, svc, exports))
            p = start_service(name, svc, base_env)
            procs[name] = p

            if "health" in svc:
                ok = healthcheck(svc["health"], timeout_s=svc.get("health_timeout", 20.0))
                print(f"[{name}] {'healthy' if ok else 'health timeout'} @ {svc['health']}")
                if not ok:
                    raise RuntimeError(f"{name} failed healthcheck")

            exports[name] = {k: str(v) for k, v in svc.get("exports", {}).items()}

        print("All services launched. Press Ctrl+C to stop.")

        while True:
            for name, p in list(procs.items()):
                if p.stdout:
                    line = p.stdout.readline()
                    if line:
                        print(f"[{name}] {line.rstrip()}")
                if p.poll() is not None:
                    code = p.returncode
                    print(f"[{name}] exited with code {code}")
                    raise SystemExit(code if code is not None else 1)
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\nStopping all services...")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        for name, p in procs.items():
            if p.poll() is None:
                try:
                    if os.name == "nt":
                        p.terminate()
                    else:
                        p.send_signal(signal.SIGINT)
                except Exception:
                    pass
        time.sleep(1.0)
        for name, p in procs.items():
            if p.poll() is None:
                p.kill()
        print("Bye.")

if __name__ == "__main__":
    print(f"Python: {sys.executable}")
    print(f"CWD: {os.getcwd()}")
    main()
