import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from ..config import defaults

def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def find_listening_pid() -> int | None:
    """Finds PID of gateway if managed by systemd user service."""
    try:
        res = subprocess.run(
            ["systemctl", "--user", "show", "webgpt-gateway.service", "--property=MainPID"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("MainPID="):
                    spid = int(line.split("=", 1)[1].strip())
                    if spid > 0 and is_pid_alive(spid):
                        return spid
    except Exception:
        pass
    return None

def read_pid() -> int | None:
    pid_file = defaults.DEFAULT_PID_FILE
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if is_pid_alive(pid):
                return pid
        except Exception:
            pass

    spid = find_listening_pid()
    if spid:
        try:
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(spid), encoding="utf-8")
        except Exception:
            pass
        return spid
    return None

def start_daemon(host: str = defaults.DEFAULT_GATEWAY_HOST, port: int = defaults.DEFAULT_GATEWAY_PORT) -> int:
    pid = read_pid()
    if pid:
        print(f"Gateway already running (PID {pid}).")
        return pid

    defaults.DEFAULT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    defaults.DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = defaults.DEFAULT_LOG_DIR / "gateway.log"

    cmd = [
        sys.executable,
        "-m", "uvicorn",
        "gpt_cli.server.app:create_app",
        "--factory",
        "--host", host,
        "--port", str(port),
    ]

    with open(log_file, "a", encoding="utf-8") as out:
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )

    defaults.DEFAULT_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.5)
    if is_pid_alive(proc.pid):
        print(f"Gateway started in background (PID {proc.pid}) at http://{host}:{port}")
        return proc.pid
    else:
        print("Failed to start gateway. Check logs at:", log_file)
        return -1

def stop_daemon() -> bool:
    pid = read_pid()
    if not pid:
        if defaults.DEFAULT_PID_FILE.exists():
            defaults.DEFAULT_PID_FILE.unlink(missing_ok=True)
        print("Gateway is not running.")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

    defaults.DEFAULT_PID_FILE.unlink(missing_ok=True)
    print(f"Gateway (PID {pid}) stopped.")
    return True

def status_daemon() -> dict[str, Any]:
    pid = read_pid()
    if pid:
        return {"running": True, "pid": pid}
    return {"running": False, "pid": None}
