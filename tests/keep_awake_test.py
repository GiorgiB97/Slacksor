from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import keep_awake
from keep_awake import _MacOSInhibitor, _SleepInhibitor, create_inhibitor


def test_base_inhibitor_is_noop() -> None:
    logs: list[str] = []
    inhibitor = _SleepInhibitor(logger=logs.append)
    inhibitor.activate()
    inhibitor.deactivate()
    assert logs == []


def test_create_inhibitor_darwin() -> None:
    with patch.object(keep_awake, "_SYSTEM", "Darwin"):
        inhibitor = create_inhibitor()
    assert isinstance(inhibitor, _MacOSInhibitor)


def test_create_inhibitor_unsupported_platform() -> None:
    logs: list[str] = []
    with patch.object(keep_awake, "_SYSTEM", "FreeBSD"):
        inhibitor = create_inhibitor(logger=logs.append)
    assert isinstance(inhibitor, _SleepInhibitor)
    assert len(logs) == 1
    assert "FreeBSD" in logs[0]


def test_macos_inhibitor_activate_deactivate() -> None:
    logs: list[str] = []
    inhibitor = _MacOSInhibitor(logger=logs.append)

    mock_proc = MagicMock()
    with patch("subprocess.Popen", return_value=mock_proc) as popen_mock:
        inhibitor.activate()
        popen_mock.assert_called_once_with(
            ["caffeinate", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    assert any("active" in msg for msg in logs)

    inhibitor.deactivate()
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once()
    assert any("deactivated" in msg for msg in logs)


def test_macos_inhibitor_activate_idempotent() -> None:
    inhibitor = _MacOSInhibitor()
    mock_proc = MagicMock()
    with patch("subprocess.Popen", return_value=mock_proc) as popen_mock:
        inhibitor.activate()
        inhibitor.activate()
        assert popen_mock.call_count == 1


def test_macos_inhibitor_deactivate_without_activate() -> None:
    inhibitor = _MacOSInhibitor()
    inhibitor.deactivate()


def test_macos_inhibitor_caffeinate_not_found() -> None:
    logs: list[str] = []
    inhibitor = _MacOSInhibitor(logger=logs.append)
    with patch("subprocess.Popen", side_effect=FileNotFoundError):
        inhibitor.activate()
    assert any("not found" in msg for msg in logs)
    assert inhibitor._process is None
