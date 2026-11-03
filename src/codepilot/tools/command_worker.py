"""独立标准库 supervisor；先建立进程树归属，再接受已批准的命令。"""

import ctypes
import json
import os
import subprocess
import sys


def windows_job() -> object:
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
            ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError("job creation failed")
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE；句柄不继承给 shell。
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        kernel.CloseHandle(job)
        raise OSError("job assignment failed")
    # 不主动关闭已归属自身的 Job；ExitProcess 关闭句柄并清理其余成员。
    return job


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    job = None
    try:
        if os.name == "nt":
            job = windows_job()
        print(json.dumps({"supervisor": "ready", "pid": os.getpid()}), flush=True)
        line = sys.stdin.readline()
        if not line:
            return 125
        payload = json.loads(line)
        # 父进程确认握手并发出已批准参数后，才创建任何命令进程。
        use_stdio = payload.get("stdio") is True
        if use_stdio:
            # 客户端等到此确认才发首条协议消息，避免握手读取预取工具输入。
            print('{"supervisor":"starting"}', flush=True)
        process = subprocess.Popen(payload["argv"], cwd=payload["cwd"], stdin=None if use_stdio else subprocess.DEVNULL)
        return process.wait()
    except (OSError, ValueError, KeyError):
        if job is None:
            print('{"supervisor":"unavailable"}', flush=True)
        else:
            print("Command supervisor failed", file=sys.stderr, flush=True)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
