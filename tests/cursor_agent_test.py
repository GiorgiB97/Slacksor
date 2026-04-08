from __future__ import annotations

import io
import subprocess
from typing import Any

import pytest

from cursor_agent import (
    CursorAgentClient,
    CursorBinaryNotFoundError,
    _extract_assistant_text,
    _parse_model_ids,
    _parse_event_line,
    cursor_cli_binary_from_env,
    process_exists,
)


class FakePopen:
    def __init__(self, lines: list[str], returncode: int = 0, pid: int = 999) -> None:
        self.stdout = io.StringIO("".join(lines))
        self.stderr = io.StringIO("")
        self.returncode = returncode
        self.pid = pid
        self._poll = None

    def poll(self) -> int | None:
        if self.stdout.tell() >= len(self.stdout.getvalue()):
            return self.returncode
        return self._poll

    def wait(self, timeout: int | None = None) -> int:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self._poll = 0

    def kill(self) -> None:
        self.returncode = -9
        self._poll = -9


def test_create_chat_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class RunResult:
        returncode = 0
        stdout = "chat-123\n"
        stderr = ""

    def fake_run(*args: Any, **kwargs: Any) -> RunResult:
        del args, kwargs
        return RunResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CursorAgentClient(binary="cursor")
    assert client.create_chat() == "chat-123"


def test_run_prompt_streams_assistant(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lines = [
        '{"type":"system","subtype":"init"}\n',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n',
        '{"type":"result","subtype":"success","is_error":false}\n',
    ]
    fake_process = FakePopen(lines=lines, pid=222)

    def fake_popen(*args: Any, **kwargs: Any) -> FakePopen:
        del args, kwargs
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    client = CursorAgentClient()
    chunks: list[str] = []
    started: list[int] = []
    pid, result = client.run_prompt(
        chat_id="chat-1",
        workspace_path=tmp_path,
        prompt="hi",
        model="auto",
        timeout_seconds=5,
        keepalive_seconds=100,
        on_assistant_chunk=chunks.append,
        on_process_started=started.append,
    )
    assert pid == 222
    assert started == [222]
    assert chunks == ["hello"]
    assert result.status == "completed"


def test_parse_helpers() -> None:
    parsed = _parse_event_line('{"type":"assistant"}')
    assert parsed is not None
    assert _parse_event_line("not json") is None
    message = _extract_assistant_text(
        {
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "ignored", "text": "x"},
                ]
            }
        }
    )
    assert message == "hello"


def test_process_exists_for_invalid_pid() -> None:
    assert process_exists(-1) is False


def test_create_chat_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class RunResult:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def fake_run(*args: Any, **kwargs: Any) -> RunResult:
        del args, kwargs
        return RunResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CursorAgentClient(binary="cursor")
    with pytest.raises(RuntimeError):
        client.create_chat()


def test_run_prompt_failed_status(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lines = ['{"type":"result","subtype":"success","is_error":true,"result":"oops"}\n']
    fake_process = FakePopen(lines=lines, pid=333)

    def fake_popen(*args: Any, **kwargs: Any) -> FakePopen:
        del args, kwargs
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    client = CursorAgentClient()
    _, result = client.run_prompt(
        chat_id="chat-1",
        workspace_path=tmp_path,
        prompt="hi",
        model="gpt-5",
        timeout_seconds=5,
        keepalive_seconds=100,
        on_assistant_chunk=lambda _: None,
    )
    assert result.status == "failed"


def test_terminate_process_uses_active_map() -> None:
    client = CursorAgentClient()
    process = FakePopen(lines=[], pid=444)
    client._active[444] = process  # noqa: SLF001
    client.terminate_process(444)
    assert process.returncode == 0


def test_parse_model_ids_strips_ansi() -> None:
    raw = (
        "\x1b[2K\x1b[GLoading models...\n"
        "Available models\n"
        "auto - Auto\n"
        "gpt-5.3-codex - GPT-5.3 Codex\n"
        "Tip: use --model <id>\n"
    )
    assert _parse_model_ids(raw) == ["auto", "gpt-5.3-codex"]


def test_list_models_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class RunResult:
        returncode = 0
        stdout = "auto - Auto\ngpt-5.3-codex - GPT-5.3 Codex\n"
        stderr = ""

    def fake_run(*args: Any, **kwargs: Any) -> RunResult:
        del args, kwargs
        return RunResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CursorAgentClient(binary="cursor")
    assert client.list_models() == ["auto", "gpt-5.3-codex"]


def test_list_models_raises_when_cursor_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise FileNotFoundError(2, "No such file or directory", "cursor")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CursorAgentClient(binary="cursor")
    with pytest.raises(CursorBinaryNotFoundError) as exc_info:
        client.list_models()
    assert "cursor" in str(exc_info.value)


def test_check_auth_propagates_cursor_binary_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise FileNotFoundError(2, "No such file or directory", "cursor")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CursorAgentClient(binary="cursor")
    with pytest.raises(CursorBinaryNotFoundError):
        client.check_auth()


def test_cursor_cli_binary_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACKSOR_CURSOR_BIN", raising=False)
    assert cursor_cli_binary_from_env() == "cursor"
    monkeypatch.setenv("SLACKSOR_CURSOR_BIN", "/opt/cursor")
    assert cursor_cli_binary_from_env() == "/opt/cursor"


def test_list_models_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class RunResult:
        returncode = 1
        stdout = ""
        stderr = "not authenticated"

    def fake_run(*args: Any, **kwargs: Any) -> RunResult:
        del args, kwargs
        return RunResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = CursorAgentClient(binary="cursor")
    with pytest.raises(RuntimeError):
        client.list_models()
