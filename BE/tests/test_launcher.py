"""Launcher checks use fake processes/HTTP; they never launch the actual stack."""

import importlib.util
import io
import os
import subprocess
import urllib.parse
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("healthguard_launcher", Path(__file__).resolve().parents[2] / "run_local.py")
launcher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(launcher)


class FakeProcess:
    def __init__(self, pid=101, code=None, stdout=None):
        self.pid, self.code, self.stdout = pid, code, stdout
        self.terminated = self.killed = 0

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated += 1
        self.code = 0

    def kill(self):
        self.killed += 1
        self.code = 0

    def wait(self, timeout=None):
        return self.code


class HttpResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_main(monkeypatch, env=None):
    launched, checked, stopped, ready = [], [], [], []
    monkeypatch.setattr(launcher, "require_file", lambda path, label: checked.append(path))
    monkeypatch.setattr(launcher, "require_free_port", lambda port: None)
    monkeypatch.setattr(launcher, "load_env", lambda: env or {})
    monkeypatch.setattr(launcher.shutil, "which", lambda *args, **kwargs: "node.exe")
    def launch(args, **kwargs):
        process = FakeProcess(pid=100 + len(launched))
        # Record only relevant environment values; never dump the host's secrets.
        telegram_env = {key: kwargs.get("env", {}).get(key) for key in launcher.TELEGRAM_ENV_KEYS}
        launched.append((args, process, telegram_env))
        return process
    monkeypatch.setattr(launcher, "launch_process", launch)
    monkeypatch.setattr(launcher, "wait_for_http", lambda url, label, processes: ready.append((url, label)))
    monkeypatch.setattr(launcher, "stop_process", lambda process: stopped.append(process.pid))
    def interrupt(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(launcher.time, "sleep", interrupt)
    return launched, checked, stopped, ready


def test_dotenv_quotes_comments_bom_and_environment_priority(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text('\ufeffTELEGRAM_BOT_TOKEN="fictional token" # comment\nTELEGRAM_WEBHOOK_SECRET=\'fictional#secret\'\nEMPTY=\n', encoding="utf-8")
    monkeypatch.setattr(launcher, "ENV_PATH", env_path)
    for key in launcher.TELEGRAM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    values = launcher.load_env()
    assert values["TELEGRAM_BOT_TOKEN"] == "fictional token"
    assert values["TELEGRAM_WEBHOOK_SECRET"] == "fictional#secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    assert launcher.load_env()["TELEGRAM_BOT_TOKEN"] == ""


def test_ports_detect_existing_ipv4_and_ipv6_listener_without_killing(monkeypatch):
    closed = []
    class Connection:
        def close(self):
            closed.append(True)
    monkeypatch.setattr(launcher.socket, "create_connection", lambda *args, **kwargs: Connection())
    with pytest.raises(RuntimeError, match="8000"):
        launcher.require_free_port(8000)
    assert closed == [True]
    attempted = []
    def ipv6_only(address, **kwargs):
        attempted.append(address[0])
        if address[0] == "127.0.0.1":
            raise OSError("IPv4 unavailable")
        return Connection()
    monkeypatch.setattr(launcher.socket, "create_connection", ipv6_only)
    with pytest.raises(RuntimeError, match="3000"):
        launcher.require_free_port(3000)
    assert attempted == ["127.0.0.1", "::1"]


def test_ready_requires_live_owned_process_even_if_http_responds(monkeypatch):
    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda *args, **kwargs: HttpResponse())
    dead = FakeProcess(code=1)
    with pytest.raises(RuntimeError, match="Frontend"):
        launcher.wait_for_http("http://local", "Frontend", [("Frontend", dead)])
    live = FakeProcess()
    launcher.wait_for_http("http://local", "Frontend", [("Frontend", live)])


def test_readiness_has_bounded_retry_and_does_not_announce_success(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(launcher.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    def unavailable(*args, **kwargs):
        raise OSError("not listening yet")
    monkeypatch.setattr(launcher.urllib.request, "urlopen", unavailable)
    with pytest.raises(RuntimeError, match="chưa sẵn sàng"):
        launcher.wait_for_http("http://local", "Frontend", [("Frontend", FakeProcess())], timeout_seconds=2)
    assert now[0] == 2


def test_missing_telegram_skips_ssh_checks_and_keeps_three_core_processes(monkeypatch, capsys):
    launched, checked, stopped, ready = fake_main(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("Telegram must not be called without configuration")
    monkeypatch.setattr(launcher, "set_telegram_webhook", forbidden)
    assert launcher.main([]) == 0
    assert len(launched) == 3
    assert launcher.SSH not in checked
    assert [label for _, label in ready] == ["Backend", "Frontend"]
    assert stopped == [102, 101, 100]
    output = capsys.readouterr().out
    assert "đã sẵn sàng" in output
    assert "Telegram chưa cấu hình" in output
    assert launched[2][0][0] == "node.exe" and "npm.cmd" not in launched[2][0]


def test_local_only_disables_telegram_only_in_child_environment(monkeypatch):
    for key in launcher.TELEGRAM_ENV_KEYS:
        monkeypatch.setenv(key, "fictional-config")
    original = {key: os.environ[key] for key in launcher.TELEGRAM_ENV_KEYS}
    launched, checked, _, _ = fake_main(monkeypatch, env=original)
    assert launcher.main(["--local-only"]) == 0
    assert len(launched) == 3 and launcher.SSH not in checked
    assert all(all(value == "" for value in item[2].values()) for item in launched)
    assert all(os.environ[key] == value for key, value in original.items())


def test_tunnel_or_webhook_failure_is_optional_and_does_not_leak_secrets(monkeypatch, capsys):
    launched, _, stopped, _ = fake_main(monkeypatch, env={"TELEGRAM_BOT_TOKEN": "fictional-secret-token", "TELEGRAM_WEBHOOK_SECRET": "fictional-secret"})
    monkeypatch.setattr(launcher, "wait_for_tunnel", lambda *args: "https://fake.lhr.life")
    def fail(*args):
        raise RuntimeError("fictional-secret-token must not reach console")
    monkeypatch.setattr(launcher, "set_telegram_webhook", fail)
    assert launcher.main([]) == 0
    assert len(launched) == 4
    assert stopped == [103, 102, 101, 100]
    output = capsys.readouterr().out
    assert "Web/API/worker vẫn chạy" in output and "đã sẵn sàng" in output
    assert "fictional-secret-token" not in output


def test_frontend_startup_failure_cleans_up_without_ready_message(monkeypatch, capsys):
    launched, _, stopped, _ = fake_main(monkeypatch)
    def fail_frontend(url, label, processes):
        if label == "Frontend":
            raise RuntimeError("Frontend not ready")
    monkeypatch.setattr(launcher, "wait_for_http", fail_frontend)
    with pytest.raises(RuntimeError, match="Frontend not ready"):
        launcher.main([])
    assert len(launched) == 3 and stopped == [102, 101, 100]
    assert "đã sẵn sàng" not in capsys.readouterr().out


def test_tunnel_exit_after_ready_does_not_stop_core_services(monkeypatch, capsys):
    launched, _, stopped, _ = fake_main(monkeypatch, env={"TELEGRAM_BOT_TOKEN": "fictional-token", "TELEGRAM_WEBHOOK_SECRET": "fictional-secret"})
    monkeypatch.setattr(launcher, "wait_for_tunnel", lambda process: "https://fake.lhr.life")
    def register(*args):
        tunnel = launched[-1][1]
        polls = [None, 7]
        tunnel.poll = lambda: polls.pop(0) if polls else 7
    monkeypatch.setattr(launcher, "set_telegram_webhook", register)
    assert launcher.main([]) == 0
    output = capsys.readouterr().out
    assert "đã sẵn sàng" in output
    assert "tunnel Telegram đã dừng" in output
    assert stopped == [103, 102, 101, 100]


def test_occupied_port_fails_before_starting_any_process(monkeypatch):
    launched, _, stopped, _ = fake_main(monkeypatch)
    def occupied(port):
        raise RuntimeError(f"Cổng {port} đang được sử dụng")
    monkeypatch.setattr(launcher, "require_free_port", occupied)
    with pytest.raises(RuntimeError, match="8000"):
        launcher.main([])
    assert launched == [] and stopped == []


def test_webhook_preserves_pending_updates_and_sanitizes_http_errors(monkeypatch):
    requests = []
    def success(request, **kwargs):
        requests.append(urllib.parse.parse_qs(request.data.decode()))
        return io.BytesIO(b'{"ok": true}')
    monkeypatch.setattr(launcher.urllib.request, "urlopen", success)
    launcher.set_telegram_webhook({"TELEGRAM_BOT_TOKEN": "fictional-token", "TELEGRAM_WEBHOOK_SECRET": "fictional-secret"}, "https://fake.lhr.life")
    assert requests[0]["url"] == ["https://fake.lhr.life/api/v1/integrations/telegram/webhook"]
    assert "drop_pending_updates" not in requests[0]
    def failure(*args, **kwargs):
        raise OSError("https://example/fictional-token")
    monkeypatch.setattr(launcher.urllib.request, "urlopen", failure)
    with pytest.raises(RuntimeError) as error:
        launcher.set_telegram_webhook({"TELEGRAM_BOT_TOKEN": "fictional-token", "TELEGRAM_WEBHOOK_SECRET": "fictional-secret"}, "https://fake.lhr.life")
    assert "fictional-token" not in str(error.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree behavior")
def test_windows_cleanup_targets_only_its_owned_live_pid_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs: calls.append(args))
    owned = FakeProcess(pid=12345)
    launcher.stop_process(owned)
    assert calls == [[str(launcher.TASKKILL), "/PID", "12345", "/T", "/F"]]
    assert owned.terminated == 1  # Fallback when mocked taskkill leaves it alive.
    launcher.stop_process(FakeProcess(pid=54321, code=1))
    assert len(calls) == 1  # Never target a exited/recycled PID.


@pytest.mark.skipif(os.name != "nt", reason="Windows Ctrl+C child group behavior")
def test_spawn_uses_new_process_group_for_managed_shutdown(monkeypatch):
    flags = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **kwargs: flags.append(kwargs.get("creationflags")))
    launcher.launch_process(["fictional-child.exe"])
    assert flags == [subprocess.CREATE_NEW_PROCESS_GROUP]
