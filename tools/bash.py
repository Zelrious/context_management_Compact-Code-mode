"""Bash tool: chạy `bash -c` đồng bộ trong workspace, timeout ngắn, env tối thiểu.

Giới hạn: cwd và env tối thiểu KHÔNG phải sandbox. Lệnh bash vẫn có thể đọc/ghi ngoài workspace
với quyền của user đang chạy app. Chỉ dùng trong môi trường lab với dữ liệu giả.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

import paths

DEFAULT_TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 20_000
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"


def _find_shell() -> tuple[str, list[str]]:
    """Tìm binary sh/bash phù hợp theo hệ điều hành (ưu tiên Git sh/bash trên Windows để tránh lỗi WSL)."""
    if sys.platform == "win32":
        git_path = shutil.which("git")
        candidates = []
        if git_path:
            git_root = Path(git_path).resolve().parents[1]
            candidates.extend([
                git_root / "bin" / "sh.exe",
                git_root / "usr" / "bin" / "sh.exe",
                git_root / "bin" / "bash.exe",
                git_root / "usr" / "bin" / "bash.exe",
            ])
        candidates.extend([
            Path(r"C:\Program Files\Git\bin\sh.exe"),
            Path(r"C:\Program Files\Git\usr\bin\sh.exe"),
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"D:\Git\bin\sh.exe"),
            Path(r"D:\Git\usr\bin\sh.exe"),
            Path(r"D:\Git\bin\bash.exe"),
        ])
        for cand in candidates:
            if cand.is_file():
                extra_dirs = [str(cand.parent)]
                usr_bin = cand.parent.parent / "usr" / "bin"
                if usr_bin.is_dir() and str(usr_bin) not in extra_dirs:
                    extra_dirs.append(str(usr_bin))
                return str(cand), extra_dirs
        return "bash", []
    return shutil.which("bash") or "bash", []


def minimal_env(workspace: Path) -> dict[str, str]:
    """PATH trỏ venv của project trước, locale UTF-8. Không truyền API key hay biến môi trường khác."""
    venv_bin = str(Path(sys.executable).parent)
    locale = os.environ.get("LANG", "")
    lang = locale if "UTF-8" in locale.upper() else "C.UTF-8"
    if sys.platform == "win32":
        _, extra_dirs = _find_shell()
        win_paths = [venv_bin, *extra_dirs, r"C:\Windows\System32", r"C:\Windows"]
        return {
            "PATH": ";".join(p for p in win_paths if p),
            "HOME": str(workspace),
            "LANG": lang,
            "LC_ALL": lang,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
        }
    return {
        "PATH": f"{venv_bin}:{SYSTEM_PATH}",
        "HOME": str(workspace),
        "LANG": lang,
        "LC_ALL": lang,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + f"\n…[đã cắt, tổng {len(text)} ký tự]", True


def _run_bash(command: str, workspace: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    if not command.strip():
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "", "timed_out": False,
                "error": {"code": "EMPTY_COMMAND", "message": "Command rỗng."}}
    shell_cmd, _ = _find_shell()
    cmd_to_run = (
        ("pwd() { builtin pwd -W 2>/dev/null || builtin pwd \"$@\"; }; " + command)
        if sys.platform == "win32"
        else command
    )

    job_handle = None
    kernel32 = None
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            job_handle = kernel32.CreateJobObjectW(None, None)
        except Exception:
            job_handle = None

    process = subprocess.Popen(
        [shell_cmd, "-c", cmd_to_run],  # không login shell, không đọc profile
        cwd=workspace,
        env=minimal_env(workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=sys.platform != "win32",  # để kill cả process group khi timeout trên POSIX
    )

    if job_handle and process._handle:
        kernel32.AssignProcessToJobObject(job_handle, process._handle)

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if job_handle:
            kernel32.TerminateJobObject(job_handle, 1)
        else:
            if hasattr(os, "killpg") and hasattr(signal, "SIGKILL"):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        stdout, stderr = process.communicate()
        stdout, out_clipped = _clip(stdout or "")
        stderr, err_clipped = _clip(stderr or "")
        return {
            "ok": False,
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
            "truncated": out_clipped or err_clipped,
            "error": {"code": "TIMEOUT", "message": f"Command chạy quá {timeout} giây, đã dừng. stdout/stderr là output một phần."},
        }
    finally:
        if job_handle and kernel32:
            kernel32.CloseHandle(job_handle)

    stdout, out_clipped = _clip(stdout)
    stderr, err_clipped = _clip(stderr)
    return {
        "ok": True,
        "exit_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "truncated": out_clipped or err_clipped,
    }


@tool
def bash(command: str) -> str:
    """Chạy một lệnh bash ngắn (bash -c) trong thư mục workspace và trả về JSON.

    cwd là workspace, nên dùng đường dẫn tương đối như data/tasks.csv. `python` là Python của project.
    Timeout 10 giây; không chạy tiến trình nền hay lệnh tương tác.
    Kết quả: {"ok": true, "exit_code": 0, "stdout": "...", "stderr": "", "timed_out": false}.
    ok=true nghĩa là lệnh đã chạy xong và có kết quả; exit_code khác 0 nghĩa là chương trình báo lỗi.
    Timeout: {"ok": false, "exit_code": null, "timed_out": true, ...} kèm output một phần nếu có.
    """
    return json.dumps(_run_bash(command, paths.WORKSPACE_DIR), ensure_ascii=False)
