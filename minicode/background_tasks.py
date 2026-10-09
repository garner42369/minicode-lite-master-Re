"""minicode 的后台任务管理（Day 26）。

长命令（pytest、构建）不应阻塞主会话。后台任务 = 一个 OS 进程，登记 PID、cwd、状态，
支持查询、读取输出、取消，用 slot 限制并发。重启后靠 PID 存活检测核对进程是否仍在。

课件「三种对象不要混淆」：后台进程（OS 进程，本文件）、子 Agent（隔离 agent 循环，
tools/task.py）、任务图（逻辑依赖 DAG，task_graph.py）。本文件只管「后台进程」。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_tasks: dict[str, "BackgroundTask"] = {}
_max_slots = 5


@dataclass
class BackgroundTask:
    """一个后台进程的登记信息（可序列化，重启后靠 PID 核对）。"""

    task_id: str
    command: str
    cwd: str
    pid: int
    status: str  # running / completed / failed / cancelled
    started_at: float
    output_file: str = ""


def _is_process_alive(pid: int) -> bool | None:
    """进程是否存活：True 活着 / False 确定已死 / None 无法判断（按失败处理）。"""
    if sys.platform == "win32":
        # Windows 上 os.kill(pid, 0) 对任何情况都抛 OSError，改用 ctypes 查退出码。
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return None
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None


def _refresh(task: "BackgroundTask") -> "BackgroundTask":
    """用 PID 存活检测刷新 running 任务的状态。"""
    if task.status != "running":
        return task
    alive = _is_process_alive(task.pid)
    if alive is True:
        return task
    task.status = "completed" if alive is False else "failed"
    return task


def start_background_task(command: str, cwd: str) -> BackgroundTask:
    """启动一个后台进程：spawn + 输出重定向到文件 + 登记。"""
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    output_dir = Path(cwd) / ".mini-code-task-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{task_id}.log"
    output_f = open(output_file, "w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=output_f,
        stderr=subprocess.STDOUT,
    )
    output_f.close()  # 子进程持有 dup 的 fd，父进程关闭自己的句柄

    task = BackgroundTask(task_id, command, str(cwd), process.pid, "running", time.time(), str(output_file))
    with _lock:
        _tasks[task_id] = task
    return task


def list_background_tasks() -> list[BackgroundTask]:
    """列出所有后台任务（刷新状态）。"""
    with _lock:
        records = list(_tasks.values())
    return [_refresh(t) for t in records]


def get_background_task(task_id: str) -> BackgroundTask | None:
    with _lock:
        task = _tasks.get(task_id)
    return _refresh(task) if task is not None else None


def cancel_background_task(task_id: str) -> bool:
    """取消一个运行中的后台进程；返回是否成功。"""
    task = _tasks.get(task_id)
    if task is None or task.status != "running":
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(task.pid)], capture_output=True, timeout=5)
        else:
            os.kill(task.pid, 15)  # SIGTERM
    except OSError:
        pass
    task.status = "cancelled"
    return True


def get_output(task_id: str) -> str:
    """读取后台任务的输出（spawn 时重定向到的文件）。"""
    task = _tasks.get(task_id)
    if task is None or not task.output_file:
        return ""
    try:
        return Path(task.output_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def get_slot_stats() -> dict[str, Any]:
    with _lock:
        running = sum(1 for t in _tasks.values() if t.status == "running")
        total = len(_tasks)
    return {
        "used_slots": running,
        "max_slots": _max_slots,
        "available_slots": max(0, _max_slots - running),
        "total_tracked": total,
    }


def set_max_slots(max_slots: int) -> None:
    global _max_slots
    _max_slots = max(1, max_slots)


def _clear() -> None:
    """清空登记（测试用）。"""
    with _lock:
        _tasks.clear()
