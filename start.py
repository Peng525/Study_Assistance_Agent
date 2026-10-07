"""AI 助学助手 - 统一启动器（清理端口 + 单窗口日志 + Ctrl+C 停止）。

设计：subprocess 启动后端和前端，主线程读两个进程的 stdout，
加前缀实时打印到主窗口。Windows 下 Ctrl+C 清理启动的进程树。
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# Windows: 让 Ctrl+C 真正传到子进程
if sys.platform == "win32":
    CREATE_NEW_PROCESS_GROUP = 0x00000200
else:
    CREATE_NEW_PROCESS_GROUP = 0

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PYTHON = BACKEND / "venv" / "Scripts" / "python.exe"
NPM = r"D:\devolop\node\npm.cmd"
BACKEND_PORT = 8080
FRONTEND_PORT = 5173


def listening_pids(port: int) -> set[int]:
    """只匹配指定本地 TCP 监听端口，不匹配远端地址或 TIME_WAIT。"""
    output = subprocess.check_output(
        ["netstat", "-ano", "-p", "TCP"], text=True, errors="replace", timeout=10,
    )
    pids = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[0] == "TCP" and fields[3] == "LISTENING":
            if fields[1].rsplit(":", 1)[-1] == str(port):
                pids.add(int(fields[4]))
    return pids


def clear_port(port: int) -> None:
    for pid in sorted(listening_pids(port)):
        if pid in (0, 4, os.getpid()):
            raise RuntimeError(f"Port {port} belongs to protected PID {pid}; cannot stop it.")
        print(f"[PORT] Stopping PID {pid} using port {port}...", flush=True)
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
        # 进程可能在查询后自行退出；以监听状态确认是否真的释放。
        if pid in listening_pids(port):
            raise RuntimeError(f"Cannot release port {port}, PID {pid}: {result.stderr.strip() or result.stdout.strip()}")
    if listening_pids(port):
        raise RuntimeError(f"Port {port} was occupied again. Please retry.")


def bindable_port(port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))
        return probe.getsockname()[1]


def frontend_port() -> int:
    # Windows 系统保留端口没有可杀的 PID；优先 5273，再由系统分配。
    for candidate in (FRONTEND_PORT, 5273, 0):
        try:
            port = bindable_port(candidate)
            if port != FRONTEND_PORT:
                print(f"[PORT] {FRONTEND_PORT} unavailable; frontend will use {port}.", flush=True)
            return port
        except OSError as exc:
            if candidate == 0:
                raise RuntimeError(f"Cannot bind a frontend port: {exc}") from exc
    raise RuntimeError("No frontend port available")


def stream_output(proc: subprocess.Popen, prefix: str) -> None:
    """读子进程 stdout，加前缀写到主进程。"""
    try:
        for line in iter(proc.stdout.readline, b""):
            text = line.decode("utf-8", errors="ignore").rstrip()
            if text:
                print(f"[{prefix}] {text}", flush=True)
    except Exception:
        pass


def terminate_proc(proc: subprocess.Popen, name: str) -> None:
    """停止子进程；Windows 同时清理 npm 启动的 Vite 子进程。"""
    if proc and proc.poll() is None:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"[{name}] 已停止", flush=True)


def main() -> int:
    print("=" * 56)
    print(" AI Study Assistant - Unified Launcher")
    print("=" * 56)
    print()

    # 前置检查
    if not PYTHON.exists():
        print(f"[ERROR] Backend venv not found: {PYTHON}")
        print("       Run: cd backend & python -m venv venv & venv\\Scripts\\pip install -r requirements.txt")
        return 1

    if not (FRONTEND / "node_modules").exists():
        print(f"[INFO] Installing frontend deps (first time)...")
        subprocess.check_call([NPM, "install"], cwd=str(FRONTEND))

    procs: list[tuple[str, subprocess.Popen]] = []

    try:
        clear_port(BACKEND_PORT)
        clear_port(FRONTEND_PORT)
        bindable_port(BACKEND_PORT)
        port = frontend_port()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"[ERROR] Port preparation failed: {exc}", flush=True)
        print("        Port 8080 must remain available; system-reserved ports cannot be released by taskkill.")
        return 1

    try:
        return run_services(procs, port)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[ERROR] Service startup failed: {exc}", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for name, proc in procs:
            terminate_proc(proc, name)


def run_services(procs: list, port: int) -> int:
    # 后端
    print(f"[BACKEND] Starting uvicorn on 127.0.0.1:{BACKEND_PORT}...")
    backend = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        cwd=str(BACKEND),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    procs.append(("BACKEND", backend))
    threading.Thread(target=stream_output, args=(backend, "BACKEND"), daemon=True).start()

    # 前端
    print(f"[FRONTEND] Starting vite on 127.0.0.1:{port}...")
    frontend_env = {**os.environ, "FORCE_COLOR": "0"}
    frontend = subprocess.Popen(
        [NPM, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=str(FRONTEND),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        env=frontend_env,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    procs.append(("FRONTEND", frontend))
    threading.Thread(target=stream_output, args=(frontend, "FRONTEND"), daemon=True).start()

    print()
    print(f" Frontend: http://127.0.0.1:{port}")
    print(f" Backend : http://127.0.0.1:{BACKEND_PORT}/health")
    print(f" Accounts: admin/123456 (admin), user25/123456 (learner)")
    print()
    print(" Press Ctrl+C to stop all services.")
    print("=" * 56)
    print()

    while True:
        for name, proc in procs:
            code = proc.poll()
            if code is not None:
                print(f"[MAIN] {name} exited ({code}), stopping all...", flush=True)
                return code or 1
        time.sleep(0.25)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted")
        sys.exit(0)
