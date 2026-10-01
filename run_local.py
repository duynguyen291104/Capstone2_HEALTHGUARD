"""Run the complete HealthGuard development stack with a Telegram webhook.

This launcher starts the API, medication worker, frontend, and a temporary
HTTPS tunnel.  Because the tunnel address changes between runs, it also updates
the Telegram webhook automatically from the secrets stored in ``BE/.env``.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "BE"
FRONTEND_DIR = ROOT / "FE"
ENV_PATH = BACKEND_DIR / ".env"
PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
SSH = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
PORTABLE_NODE = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "HealthGuardTools"
    / "node-v24.19.0-win-x64"
)
TUNNEL_URL_PATTERN = re.compile(r"https://[a-z0-9.-]+\.lhr\.life")


def load_env() -> dict[str, str]:
    if not ENV_PATH.is_file():
        raise RuntimeError("Không tìm thấy BE/.env")
    values: dict[str, str] = {}
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"Không tìm thấy {label}: {path}")


def require_free_port(port: int) -> None:
    try:
        connection = socket.create_connection(("localhost", port), timeout=0.5)
    except OSError:
        return
    connection.close()
    raise RuntimeError(
        f"Cổng {port} đang được sử dụng. HealthGuard có thể đang chạy ở cửa sổ khác."
    )


def wait_for_api(timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError("Backend không khởi động được tại http://localhost:8000")


def stream_lines(process: subprocess.Popen[str], output: queue.Queue[str | None]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        output.put(line)
    output.put(None)


def wait_for_tunnel(process: subprocess.Popen[str], timeout_seconds: int = 30) -> str:
    output: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=stream_lines, args=(process, output), daemon=True).start()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            line = output.get(timeout=1)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        if line is None:
            break
        match = TUNNEL_URL_PATTERN.search(line)
        if match:
            return match.group(0)
    raise RuntimeError("Không tạo được HTTPS tunnel cho Telegram")


def set_telegram_webhook(env: dict[str, str], public_url: str) -> None:
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    secret = env.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not token or not secret:
        raise RuntimeError("BE/.env chưa có TELEGRAM_BOT_TOKEN hoặc TELEGRAM_WEBHOOK_SECRET")
    webhook_url = f"{public_url}/api/v1/integrations/telegram/webhook"
    body = urllib.parse.urlencode(
        {
            "url": webhook_url,
            "secret_token": secret,
            "drop_pending_updates": "true",
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("Không đăng ký được Telegram webhook") from exc
    if not result.get("ok"):
        raise RuntimeError("Telegram từ chối webhook")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    require_file(PYTHON, "Python virtual environment")
    require_file(SSH, "OpenSSH")
    require_free_port(8000)
    require_free_port(3000)
    env_values = load_env()

    frontend_env = os.environ.copy()
    if PORTABLE_NODE.is_dir():
        frontend_env["PATH"] = f"{PORTABLE_NODE}{os.pathsep}{frontend_env.get('PATH', '')}"
    npm = shutil.which("npm.cmd", path=frontend_env.get("PATH"))
    if not npm:
        raise RuntimeError("Không tìm thấy npm")

    processes: list[subprocess.Popen] = []
    try:
        print("[1/4] Đang chạy backend...")
        processes.append(
            subprocess.Popen(
                [str(PYTHON), "-m", "uvicorn", "app.main:app", "--port", "8000"],
                cwd=BACKEND_DIR,
            )
        )
        wait_for_api()

        print("[2/4] Đang chạy worker nhắc thuốc...")
        processes.append(
            subprocess.Popen([str(PYTHON), "-m", "app.worker"], cwd=BACKEND_DIR)
        )

        print("[3/4] Đang chạy frontend...")
        processes.append(
            subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND_DIR, env=frontend_env)
        )

        print("[4/4] Đang tạo HTTPS tunnel và đăng ký Telegram webhook...")
        tunnel = subprocess.Popen(
            [
                str(SSH),
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ServerAliveInterval=30",
                "-R",
                "80:127.0.0.1:8000",
                "nokey@localhost.run",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        processes.append(tunnel)
        public_url = wait_for_tunnel(tunnel)
        set_telegram_webhook(env_values, public_url)

        print()
        print("HealthGuard đã sẵn sàng:")
        print("  Website: http://localhost:3000")
        print("  API docs: http://localhost:8000/docs")
        print(f"  Telegram webhook: {public_url}")
        print("Giữ cửa sổ này mở. Nhấn Ctrl+C để tắt toàn bộ.")

        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("Một tiến trình HealthGuard đã dừng ngoài dự kiến")
    except KeyboardInterrupt:
        print("\nĐang tắt HealthGuard...")
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        raise SystemExit(1)
