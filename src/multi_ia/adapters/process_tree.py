"""Ownership of a CLI process and everything it starts.

The tree must stay killable after the immediate process exits: a descendant can
outlive it and keep its pipes open. On POSIX the process group provides this;
on Windows a Job Object does. This is cleanup, not a sandbox: on Windows a
child started before the job assignment would not belong to the job.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess


def _windows_job(pid: int):
    """Return (kernel32, job handle) holding the process, or None on failure."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    # Also ends the tree if this program dies and the handle closes.
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    process = None
    try:
        if not kernel.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            return None
        process = kernel.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process or not kernel.AssignProcessToJobObject(job, process):
            return None
        owned, job = job, None
        return kernel, owned
    finally:
        if process:
            kernel.CloseHandle(process)
        if job:
            kernel.CloseHandle(job)


class ProcessTree:
    """Create right after starting a process (start_new_session on POSIX)."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._job = None
        if os.name == "nt":
            try:
                self._job = _windows_job(process.pid)
            except OSError:
                self._job = None

    def kill(self) -> None:
        """End every owned process. Never raises, so cancellation is kept."""
        try:
            if os.name != "nt":
                # The group outlives its leader while any member is running.
                os.killpg(self.process.pid, signal.SIGKILL)
            elif self._job is not None:
                kernel, job = self._job
                self._job = None
                kernel.TerminateJobObject(job, 1)
                kernel.CloseHandle(job)
            elif self.process.returncode is None:
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    capture_output=True, check=False, timeout=15,
                )
        except (OSError, subprocess.SubprocessError):
            pass
        if self.process.returncode is None:
            try:
                self.process.kill()
            except (OSError, ProcessLookupError):
                pass
