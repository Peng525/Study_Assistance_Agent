"""启动器端口与进程生命周期；全部进程终止调用均为 mock。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_file_location("demo_launcher", Path(__file__).resolve().parents[2] / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_local_exact_listening_port_is_selected(launcher, monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *a, **kw: """
 TCP 127.0.0.1:5173 0.0.0.0:0 LISTENING 101
 TCP [::]:5173 [::]:0 LISTENING 101
 TCP 0.0.0.0:15173 0.0.0.0:0 LISTENING 202
 TCP 127.0.0.1:50000 127.0.0.1:5173 ESTABLISHED 303
 TCP 127.0.0.1:5173 127.0.0.1:50000 TIME_WAIT 0
""")
    assert launcher.listening_pids(5173) == {101}


def test_clear_port_kills_listener_tree_once_and_checks_release(launcher, monkeypatch):
    states = iter([{101}, set(), set()])
    monkeypatch.setattr(launcher, "listening_pids", lambda port: next(states))
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(stderr="", stdout=""))
    launcher.clear_port(5173)
    assert calls == [["taskkill", "/PID", "101", "/T", "/F"]]


@pytest.mark.parametrize("pid", [0, 4])
def test_system_process_is_never_killed(launcher, monkeypatch, pid):
    monkeypatch.setattr(launcher, "listening_pids", lambda port: {pid})
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: pytest.fail("must not kill protected PID"))
    with pytest.raises(RuntimeError, match="protected PID"):
        launcher.clear_port(8080)


def test_kill_failure_is_reported(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "listening_pids", lambda port: {101})
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: SimpleNamespace(stderr="Access denied", stdout=""))
    with pytest.raises(RuntimeError, match="Access denied"):
        launcher.clear_port(5173)


def test_reserved_frontend_port_falls_back_without_killing_other_ports(launcher, monkeypatch):
    seen = []
    def bind(port):
        seen.append(port)
        if port == 5173:
            raise PermissionError("reserved")
        return port
    monkeypatch.setattr(launcher, "bindable_port", bind)
    assert launcher.frontend_port() == 5273
    assert seen == [5173, 5273]


def test_both_fixed_frontend_ports_unavailable_uses_os_port(launcher, monkeypatch):
    def bind(port):
        if port:
            raise OSError("unavailable")
        return 60123
    monkeypatch.setattr(launcher, "bindable_port", bind)
    assert launcher.frontend_port() == 60123


def test_main_cleans_backend_when_frontend_start_fails(launcher, monkeypatch, tmp_path):
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(launcher, "FRONTEND", tmp_path)
    monkeypatch.setattr(launcher, "PYTHON", Path(__file__))
    monkeypatch.setattr(launcher, "clear_port", lambda p: None)
    monkeypatch.setattr(launcher, "bindable_port", lambda p: p)
    backend = object()
    stopped = []
    def fail(procs, port):
        procs.append(("BACKEND", backend))
        raise OSError("npm unavailable")
    monkeypatch.setattr(launcher, "run_services", fail)
    monkeypatch.setattr(launcher, "terminate_proc", lambda proc, name: stopped.append((name, proc)))
    assert launcher.main() == 1
    assert stopped == [("BACKEND", backend)]


def test_frontend_exit_ends_supervision_and_uses_explicit_port(launcher, monkeypatch):
    calls = []
    backend = SimpleNamespace(poll=lambda: None)
    frontend = SimpleNamespace(poll=lambda: 7)
    processes = iter([backend, frontend])
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **kw: calls.append(args) or next(processes))
    monkeypatch.setattr(launcher.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    assert launcher.run_services([], 5273) == 7
    assert calls[1][-5:] == ["--host", "127.0.0.1", "--port", "5273", "--strictPort"]


def test_windows_shutdown_terminates_child_tree(launcher, monkeypatch):
    calls = []
    waits = []
    proc = SimpleNamespace(pid=567, poll=lambda: None, wait=lambda **kw: waits.append(kw))
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kw: calls.append(args))
    launcher.terminate_proc(proc, "FRONTEND")
    assert calls == [["taskkill", "/PID", "567", "/T", "/F"]]
    assert waits == [{"timeout": 5}]
