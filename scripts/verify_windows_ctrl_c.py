"""用真实 Windows CTRL_C_EVENT 验证清理和同端口重启，不模拟业务关闭方法。"""

import ctypes
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def send_ctrl_c(process_id: int) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(process_id):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # 信号发给独立测试控制台；辅助进程忽略它，避免把自己也中断。
        if not kernel.SetConsoleCtrlHandler(None, True):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.GenerateConsoleCtrlEvent(0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.2)
    finally:
        kernel.FreeConsole()


def probe(port: int) -> dict[str, Any]:
    with socket.create_connection(("127.0.0.1", port), timeout=0.3) as connection:
        connection.sendall(b'{"jsonrpc":"2.0","id":"ctrl-c-check","method":"core.ping","params":{}}\n')
        with connection.makefile("rb") as stream:
            result = json.loads(stream.readline())
    assert result["id"] == "ctrl-c-check"
    assert result["result"]["message"] == "pong"
    return result


def check_once(port: int) -> dict[str, Any]:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen(
        [sys.executable, "-m", "codepilot.cli", "core", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startup,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                pong = probe(port)
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Core did not become ready")
                time.sleep(0.05)
        with socket.create_connection(("127.0.0.1", port), timeout=1) as active:
            active.sendall(b"{")
            start = time.monotonic()
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--signal", str(process.pid)],
                check=True,
                capture_output=True,
                timeout=3,
            )
            code = process.wait(timeout=5)
            elapsed = time.monotonic() - start
            assert code == 0, f"Unexpected exit code: {code}"
            assert elapsed < 5, f"Shutdown took {elapsed:.3f}s"
            try:
                assert active.recv(1) == b""
            except ConnectionResetError:
                pass
        stdout, stderr = process.communicate(timeout=1)
        logs = stderr.decode("utf-8", errors="replace")
        assert "Core stopped; connections and listener closed" in logs
        assert not stdout
        return {"exit_code": code, "shutdown_seconds": round(elapsed, 3), "pong": pong, "logs": logs}
    finally:
        if process.poll() is None:
            # 只清理由本脚本启动并持有 PID 的测试进程树；强制清理不算验收成功。
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            process.wait(timeout=3)
        process.communicate(timeout=1)


def main() -> int:
    if sys.platform != "win32":
        print("This verification requires native Windows", file=sys.stderr)
        return 2
    if len(sys.argv) == 3 and sys.argv[1] == "--signal":
        send_ctrl_c(int(sys.argv[2]))
        return 0
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    result = {"port": port, "first": check_once(port), "same_port_restart": check_once(port)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
