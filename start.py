"""AI 助学助手 - 统一启动器（单窗口 + 合并日志 + Ctrl+C 优雅停止）。

设计：subprocess 启动后端和前端，主线程读两个进程的 stdout，
加前缀实时打印到主窗口。Ctrl+C 触发所有子进程优雅退出。
"""

import os
import signal
import subprocess
import sys
import threading
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
    """优雅停止子进程。"""
    if proc and proc.poll() is None:
        try:
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
    print(f"[FRONTEND] Starting vite on 5173...")
    frontend_env = {**os.environ, "FORCE_COLOR": "0"}
    frontend = subprocess.Popen(
        [NPM, "run", "dev"],
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
    print(f" Frontend: http://localhost:5173")
    print(f" Backend : http://127.0.0.1:{BACKEND_PORT}/health")
    print(f" Accounts: admin/123456 (admin), user25/123456 (learner)")
    print()
    print(" Press Ctrl+C to stop all services.")
    print("=" * 56)
    print()

    # 主循环：等待后端退出（任一崩了都退）
    try:
        backend.wait()
    except KeyboardInterrupt:
        pass

    print("\n[MAIN] Backend exited, stopping all...")
    for name, proc in procs:
        terminate_proc(proc, name)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted")
        sys.exit(0)