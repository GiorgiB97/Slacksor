from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


AssistantChunkCallback = Callable[[str], None]
KeepaliveCallback = Callable[[], None]
ProcessStartedCallback = Callable[[int], None]
MODEL_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s+-\s+.+$")
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
AUTH_PROMPT_PATTERN = "Press any key to log in"


class CursorBinaryNotFoundError(Exception):
    """The Cursor CLI executable could not be executed (missing or not on PATH)."""


def _cursor_binary_missing_message(binary: str) -> str:
    return (
        f"Cannot find or run the Cursor CLI ({binary!r}). "
        "Install it from Cursor: Command Palette, then run "
        "'Shell Command: Install cursor command in PATH'. "
        "Or set SLACKSOR_CURSOR_BIN to the full path of the cursor executable."
    )


def cursor_cli_binary_from_env() -> str:
    """CLI path: SLACKSOR_CURSOR_BIN if set, otherwise `cursor` on PATH."""
    value = os.getenv("SLACKSOR_CURSOR_BIN", "").strip()
    return value or "cursor"


@dataclass
class AgentRunResult:
    status: str
    assistant_messages: list[str] = field(default_factory=list)
    result_payload: dict[str, Any] | None = None
    stderr: str = ""
    exit_code: int | None = None


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class CursorAgentClient:
    def __init__(self, binary: str = "cursor") -> None:
        self.binary = binary
        self._active: dict[int, subprocess.Popen[str]] = {}

    def check_auth(self, timeout_seconds: int = 15) -> bool:
        """Return True if the cursor agent CLI is authenticated."""
        try:
            self.list_models(timeout_seconds=timeout_seconds)
            return True
        except CursorBinaryNotFoundError:
            raise
        except (RuntimeError, subprocess.TimeoutExpired):
            return False

    def create_chat(self, workspace_path: Path | None = None, timeout_seconds: int = 30) -> str:
        command = [self.binary, "agent", "create-chat"]
        if workspace_path is not None:
            command.extend(
                [
                    "--workspace",
                    str(workspace_path),
                    "--trust",
                    "--force",
                ]
            )
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CursorBinaryNotFoundError(_cursor_binary_missing_message(self.binary)) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Cursor Agent is not authenticated. "
                "Run `cursor agent` in a terminal to log in."
            ) from exc
        combined = (process.stdout or "") + (process.stderr or "")
        if AUTH_PROMPT_PATTERN in ANSI_ESCAPE_RE.sub("", combined):
            raise RuntimeError(
                "Cursor Agent is not authenticated. "
                "Run `cursor agent` in a terminal to log in."
            )
        if process.returncode != 0:
            stderr = process.stderr.strip()
            raise RuntimeError(f"cursor agent create-chat failed: {stderr}")
        chat_id = process.stdout.strip()
        if not chat_id:
            raise RuntimeError("cursor agent create-chat returned empty chat id")
        return chat_id

    def list_models(self, timeout_seconds: int = 20) -> list[str]:
        command = [self.binary, "agent", "models"]
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CursorBinaryNotFoundError(_cursor_binary_missing_message(self.binary)) from exc
        if process.returncode != 0:
            stderr = process.stderr.strip()
            raise RuntimeError(f"cursor agent models failed: {stderr}")
        parsed = _parse_model_ids(process.stdout)
        if not parsed:
            raise RuntimeError("cursor agent models returned no model ids")
        return parsed

    def run_prompt(
        self,
        chat_id: str,
        workspace_path: Path,
        prompt: str,
        model: str,
        timeout_seconds: int,
        keepalive_seconds: int,
        on_assistant_chunk: AssistantChunkCallback,
        on_keepalive: KeepaliveCallback | None = None,
        on_process_started: ProcessStartedCallback | None = None,
    ) -> tuple[int, AgentRunResult]:
        command = [
            self.binary,
            "agent",
            "--resume",
            chat_id,
            "--print",
            "--output-format",
            "stream-json",
            "--workspace",
            str(workspace_path),
            "--trust",
            "--force",
        ]
        if model and model.lower() != "auto":
            command.extend(["--model", model])
        command.append(prompt)
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise CursorBinaryNotFoundError(_cursor_binary_missing_message(self.binary)) from exc
        self._active[process.pid] = process
        if on_process_started is not None:
            on_process_started(process.pid)

        chunks: list[str] = []
        result_payload: dict[str, Any] | None = None
        timed_out = False
        auth_required = False
        started = time.monotonic()
        next_keepalive = started + keepalive_seconds

        try:
            while True:
                now = time.monotonic()
                if timeout_seconds > 0 and now - started > timeout_seconds:
                    timed_out = True
                    self.terminate_process(process.pid)
                    break

                if on_keepalive is not None and keepalive_seconds > 0 and now >= next_keepalive:
                    on_keepalive()
                    next_keepalive = now + keepalive_seconds

                assert process.stdout is not None
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue

                clean_line = ANSI_ESCAPE_RE.sub("", line).strip()
                if AUTH_PROMPT_PATTERN in clean_line:
                    auth_required = True
                    self.terminate_process(process.pid)
                    break

                parsed = _parse_event_line(line)
                if parsed is None:
                    continue

                if parsed.get("type") == "assistant":
                    message_text = _extract_assistant_text(parsed)
                    if message_text:
                        chunks.append(message_text)
                        on_assistant_chunk(message_text)
                elif parsed.get("type") == "result":
                    result_payload = parsed

            process.wait(timeout=2)
        finally:
            self._active.pop(process.pid, None)

        stderr = ""
        if process.stderr is not None:
            stderr = process.stderr.read().strip()

        if auth_required:
            return process.pid, AgentRunResult(
                status="auth_required",
                assistant_messages=chunks,
                stderr="Cursor Agent is not authenticated. "
                "Run `cursor agent` in a terminal to log in.",
                exit_code=process.returncode,
            )
        if timed_out:
            return process.pid, AgentRunResult(
                status="timeout",
                assistant_messages=chunks,
                result_payload=result_payload,
                stderr=stderr,
                exit_code=process.returncode,
            )
        if process.returncode != 0:
            return process.pid, AgentRunResult(
                status="failed",
                assistant_messages=chunks,
                result_payload=result_payload,
                stderr=stderr,
                exit_code=process.returncode,
            )
        if result_payload and result_payload.get("is_error"):
            return process.pid, AgentRunResult(
                status="failed",
                assistant_messages=chunks,
                result_payload=result_payload,
                stderr=str(result_payload.get("result", "")),
                exit_code=process.returncode,
            )
        return process.pid, AgentRunResult(
            status="completed",
            assistant_messages=chunks,
            result_payload=result_payload,
            stderr=stderr,
            exit_code=process.returncode,
        )

    def terminate_process(self, pid: int) -> None:
        process = self._active.get(pid)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            return
        if process_exists(pid):
            os.kill(pid, signal.SIGTERM)


def _parse_event_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _extract_assistant_text(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text", "")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts).strip()


def _strip_ansi_codes(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", value)


def _parse_model_ids(raw_output: str) -> list[str]:
    model_ids: list[str] = []
    for raw_line in raw_output.splitlines():
        line = _strip_ansi_codes(raw_line).strip()
        if not line or " - " not in line:
            continue
        match = MODEL_LINE_RE.match(line)
        if not match:
            continue
        model_id = match.group(1)
        if model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids
