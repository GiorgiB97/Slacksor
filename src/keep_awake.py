"""Cross-platform system sleep prevention.

macOS:  spawns ``caffeinate -s`` as a child process.
Windows: calls ``SetThreadExecutionState`` via ctypes.
Linux/other: no-op (logged once).
"""

from __future__ import annotations

import platform
import subprocess
import sys
from typing import Callable


_SYSTEM = platform.system()


class _SleepInhibitor:
    """Base no-op inhibitor."""

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        self._logger = logger

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)


class _MacOSInhibitor(_SleepInhibitor):

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        super().__init__(logger)
        self._process: subprocess.Popen[bytes] | None = None

    def activate(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                ["caffeinate", "-s"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log("Sleep prevention active (caffeinate)")
        except FileNotFoundError:
            self._log("caffeinate not found; sleep prevention unavailable")

    def deactivate(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        self._process.wait()
        self._process = None
        self._log("Sleep prevention deactivated")


class _WindowsInhibitor(_SleepInhibitor):

    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001

    def activate(self) -> None:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            self._ES_CONTINUOUS | self._ES_SYSTEM_REQUIRED,
        )
        self._log("Sleep prevention active (SetThreadExecutionState)")

    def deactivate(self) -> None:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            self._ES_CONTINUOUS,
        )
        self._log("Sleep prevention deactivated")


def create_inhibitor(logger: Callable[[str], None] | None = None) -> _SleepInhibitor:
    """Return a platform-appropriate sleep inhibitor."""
    if _SYSTEM == "Darwin":
        return _MacOSInhibitor(logger)
    if _SYSTEM == "Windows":
        return _WindowsInhibitor(logger)
    inhibitor = _SleepInhibitor(logger)
    if logger is not None:
        logger(f"Sleep prevention not supported on {_SYSTEM}; skipping")
    return inhibitor
