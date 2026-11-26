"""Small terminal safety helpers for the interactive experimental mode."""
from __future__ import annotations

import os
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO


def discard_pending_input(stream: TextIO | None = None) -> bool:
    """Drop pasted or typed-ahead console input before an important question.

    Returns True if something was waiting. Redirected input is left untouched.
    """
    stream = stream or sys.stdin
    try:
        if not stream.isatty():
            return False
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.FlushConsoleInputBuffer.argtypes = [wintypes.HANDLE]
            waiting = bool(msvcrt.kbhit())
            kernel.FlushConsoleInputBuffer(kernel.GetStdHandle(wintypes.DWORD(-10)))
            return waiting
        import select
        import termios

        waiting = bool(select.select([stream], [], [], 0)[0])
        termios.tcflush(stream, termios.TCIFLUSH)
        return waiting
    except (OSError, ValueError, AttributeError):
        return False


@contextmanager
def interruptible() -> Iterator[None]:
    """Let Ctrl+C interrupt a blocking read made inside an asyncio loop.

    asyncio.run only cancels its task on the first Ctrl+C, which cannot take
    effect while the loop is blocked reading from the console.
    """
    previous = signal.getsignal(signal.SIGINT)
    if threading.current_thread() is not threading.main_thread() or previous is None:
        yield
        return
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@contextmanager
def on_console_close(callback: Callable[[], None]) -> Iterator[None]:
    """Run callback if the Windows console is closed, or on logoff or shutdown."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    routine_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handle(event: int) -> bool:
        if event in (2, 5, 6):  # close, logoff, shutdown
            callback()
        return False  # Windows then continues its normal handling.

    routine = routine_type(handle)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetConsoleCtrlHandler.argtypes = [routine_type, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler(routine, True)
    try:
        yield
    finally:
        kernel.SetConsoleCtrlHandler(routine, False)


def process_alive(pid: int) -> bool:
    """Whether a process exists. Errs on the side of True when unsure."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    # os.kill would terminate the process on Windows.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() != 87  # 87: no such process
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102  # still running
    finally:
        kernel.CloseHandle(handle)
