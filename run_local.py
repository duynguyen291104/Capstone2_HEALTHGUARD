"""Start HealthGuard locally, with an optional temporary Telegram HTTPS tunnel."""

from __future__ import annotations

import argparse
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

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "BE"
FRONTEND_DIR = ROOT / "FE"
ENV_PATH = BACKEND_DIR / ".env"
PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
SSH = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
TASKKILL = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "taskkill.exe"
PORTABLE_NODE = Path(os.environ.get("LOCALAPPDATA", "")) / "HealthGuardTools" / "node-v24.19.0-win-x64"
NEXT_CLI = FRONTEND_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
TUNNEL_URL_PATTERN = re.compile(r"https://[a-z0-9.-]+\.lhr\.life")
TELEGRAM_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "TELEGRAM_WEBHOOK_SECRET")


def load_env() -> dict[str, str]:
    if not ENV_PATH.is_file():
        raise RuntimeError("Không tìm thấy BE/.env. Hãy cấu hình backend trước khi chạy.")
    # Match backend settings: dotenv quotes/comments plus OS environment priority.
    values = {key: value for key, value in dotenv_values(ENV_PATH, encoding="utf-8-sig").items() if value is not None}
    for key in set(values) | set(TELEGRAM_ENV_KEYS):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"Không tìm thấy {label}: {path}")


def require_free_port(port: int) -> None:
    for host in ("127.0.0.1", "::1"):
        try:
            connection = socket.create_connection((host, port), timeout=.5)
        except OSError:
            continue
        connection.close()
        raise RuntimeError(
            f"Cổng {port} đang được sử dụng. Hãy đóng cửa sổ HealthGuard cũ hoặc ứng dụng dùng cổng này rồi chạy lại."
        )


def require_running(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for label, process in processes:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"{label} đã dừng (mã {code}). Xem lỗi phía trên để xử lý.")


def wait_for_http(url: str, label: str, processes: list[tuple[str, subprocess.Popen]], timeout_seconds: float = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        require_running(processes)
        try:
            with urllib.request.urlopen(url, timeout=min(2, max(.1, deadline - time.monotonic()))) as response:
                if response.status == 200:
                    require_running(processes)
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
    require_running(processes)
    raise RuntimeError(f"{label} chưa sẵn sàng tại {url}. Xem lỗi phía trên rồi thử lại.")


def stream_lines(process: subprocess.Popen[str], output: queue.Queue[str | None]) -> None:
    assert process.stdout is not None
    try:
        for line in process.stdout:
            try:
                output.put_nowait(line)
            except queue.Full:
                pass  # Keep draining SSH output after the tunnel URL was found.
    except (OSError, ValueError):
        pass
    finally:
        try:
            output.put_nowait(None)
        except queue.Full:
            pass


def wait_for_tunnel(process: subprocess.Popen[str], timeout_seconds: float = 30) -> str:
    output: queue.Queue[str | None] = queue.Queue(maxsize=100)
    threading.Thread(target=stream_lines, args=(process, output), daemon=True).start()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            line = output.get(timeout=min(1, max(.1, deadline - time.monotonic())))
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
    token, secret = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not token or not secret:
        raise RuntimeError("Telegram chưa có bot token hoặc webhook secret")
    body = urllib.parse.urlencode({
        "url": f"{public_url}/api/v1/integrations/telegram/webhook", "secret_token": secret,
        # Telegram's default keeps pending updates, including unused link codes.
    }).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/setWebhook", data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except (OSError, urllib.error.URLError, ValueError):
        # Never include the request URL/exception: it contains the bot token.
        raise RuntimeError("Không đăng ký được Telegram webhook") from None
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("Telegram từ chối webhook")


def launch_process(arguments: list[str], **kwargs) -> subprocess.Popen:
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(arguments, **kwargs)


def stop_process(process: subprocess.Popen) -> None:
    """Stop only this launcher's live child tree, never a port/global process name."""
    if process.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(
                    [str(TASKKILL), "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, check=False, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    if process.stdout is not None:
        process.stdout.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true", help="Chạy web/API/worker; tắt Telegram cho lượt chạy này, không cần SSH/Internet.")
    options = parser.parse_args(argv)

    require_file(PYTHON, "Python virtual environment BE/.venv (cài backend trước)")
    env_values = load_env()
    require_free_port(8000)
    require_free_port(3000)
    child_env = os.environ.copy()
    if options.local_only:
        child_env.update({key: "" for key in TELEGRAM_ENV_KEYS})
    frontend_env = child_env.copy()
    if PORTABLE_NODE.is_dir():
        frontend_env["PATH"] = f"{PORTABLE_NODE}{os.pathsep}{frontend_env.get('PATH', '')}"
    node = shutil.which("node.exe" if os.name == "nt" else "node", path=frontend_env.get("PATH"))
    if not node:
        raise RuntimeError("Không tìm thấy Node.js. Cài Node.js rồi mở lại cửa sổ terminal.")
    require_file(NEXT_CLI, "Next.js trong FE/node_modules (chạy npm install tại FE)")

    started: list[subprocess.Popen] = []
    core: list[tuple[str, subprocess.Popen]] = []
    tunnel: subprocess.Popen | None = None
    public_url: str | None = None
    try:
        print("[1/4] Đang chạy backend...", flush=True)
        backend = launch_process([str(PYTHON), "-m", "uvicorn", "app.main:app", "--port", "8000"], cwd=BACKEND_DIR, env=child_env)
        started.append(backend)
        core.append(("Backend", backend))
        wait_for_http("http://127.0.0.1:8000/health", "Backend", core)

        print("[2/4] Đang chạy worker nhắc thuốc...", flush=True)
        worker = launch_process([str(PYTHON), "-m", "app.worker"], cwd=BACKEND_DIR, env=child_env)
        started.append(worker)
        core.append(("Worker nhắc thuốc", worker))
        print("[3/4] Đang chạy frontend...", flush=True)
        # Launch Node directly. There is no npm.cmd wrapper that can exit early
        # and leave a Next.js process outside our known child PID tree.
        frontend = launch_process([node, str(NEXT_CLI), "dev", "--port", "3000"], cwd=FRONTEND_DIR, env=frontend_env)
        started.append(frontend)
        core.append(("Frontend", frontend))
        wait_for_http("http://127.0.0.1:3000", "Frontend", core)

        configured = bool(env_values.get("TELEGRAM_BOT_TOKEN") and env_values.get("TELEGRAM_WEBHOOK_SECRET"))
        if options.local_only:
            print("[4/4] Chế độ local: Telegram đã tắt cho lượt chạy này.", flush=True)
        elif not configured:
            print("[4/4] Telegram chưa cấu hình đầy đủ; web/API/worker vẫn chạy.", flush=True)
        else:
            print("[4/4] Đang tạo HTTPS tunnel và đăng ký Telegram webhook...", flush=True)
            try:
                require_file(SSH, "OpenSSH")
                tunnel = launch_process([
                    str(SSH), "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
                    "-o", "ConnectTimeout=10", "-o", "ExitOnForwardFailure=yes",
                    "-R", "80:127.0.0.1:8000", "nokey@localhost.run",
                ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                started.append(tunnel)
                public_url = wait_for_tunnel(tunnel)
                set_telegram_webhook(env_values, public_url)
                if tunnel.poll() is not None:
                    raise RuntimeError("HTTPS tunnel đã dừng")
            except (RuntimeError, OSError):
                print("Telegram webhook chưa kết nối được. Web/API/worker vẫn chạy; kiểm tra mạng/SSH và khởi động lại nếu cần liên kết Telegram.", flush=True)
                if tunnel is not None:
                    stop_process(tunnel)
                    started.remove(tunnel)
                tunnel, public_url = None, None

        require_running(core)
        print("\nHealthGuard đã sẵn sàng:")
        print("  Website: http://localhost:3000")
        print("  API docs: http://localhost:8000/docs")
        print(f"  Telegram webhook: {public_url}" if public_url else "  Telegram webhook: chưa bật")
        print("Giữ cửa sổ này mở. Nhấn Ctrl+C để tắt các tiến trình đã mở.", flush=True)
        while True:
            require_running(core)
            if tunnel is not None and tunnel.poll() is not None:
                print("HTTPS tunnel Telegram đã dừng; web/API/worker vẫn đang chạy. Khởi động lại để kết nối lại webhook.", flush=True)
                tunnel = None
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nĐang tắt HealthGuard...", flush=True)
        return 0
    except OSError:
        raise RuntimeError("Không khởi động được một tiến trình HealthGuard. Kiểm tra Python/Node và quyền chạy chương trình.") from None
    finally:
        for process in reversed(started):
            try:
                stop_process(process)
            except (OSError, subprocess.TimeoutExpired):
                print(f"Chưa dừng được tiến trình đã mở PID {process.pid}; các tiến trình còn lại vẫn được dọn dẹp.", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        raise SystemExit(1)
