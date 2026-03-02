from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from slack_sdk import WebClient
from watchfiles import watch

from db import Database


USER_PROMPT_PREFIX = "User: "
AGENT_RESPONSE_PREFIX = "Agent: "


def _cursor_home_path(cursor_home: Path | None = None) -> Path:
    if cursor_home is not None:
        return Path(cursor_home).expanduser().resolve()
    return (Path.home() / ".cursor").resolve()


def hooks_config_path(cursor_home: Path | None = None) -> Path:
    return _cursor_home_path(cursor_home) / "hooks.json"


def hook_script_path(cursor_home: Path | None = None) -> Path:
    return _cursor_home_path(cursor_home) / "hooks" / "slacksor_sync.py"


def hook_events_path(cursor_home: Path | None = None) -> Path:
    return _cursor_home_path(cursor_home) / "slacksor-hook-events.jsonl"

HOOK_SCRIPT_CONTENT = """#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def main() -> None:
    payload = json.load(sys.stdin)
    event_name = str(payload.get("hook_event_name", "")).strip()
    if event_name not in {"beforeSubmitPrompt", "afterAgentResponse"}:
        print("{}")
        return

    conversation_id = str(payload.get("conversation_id", "")).strip()
    if not conversation_id:
        print("{}")
        return

    record = {
        "hook_event_name": event_name,
        "conversation_id": conversation_id,
        "workspace_roots": payload.get("workspace_roots", []),
        "transcript_path": payload.get("transcript_path", ""),
        "prompt": payload.get("prompt", ""),
        "text": payload.get("text", ""),
        "timestamp_ms": int(time.time() * 1000),
    }

    events_path = Path.home() / ".cursor" / "slacksor-hook-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\\n")

    print("{}")


if __name__ == "__main__":
    main()
"""


def ensure_cursor_hook_files(
    logger: Callable[[str], None],
    cursor_home: Path | None = None,
) -> None:
    resolved_cursor_home = _cursor_home_path(cursor_home)
    resolved_hooks_json_path = hooks_config_path(resolved_cursor_home)
    resolved_hook_script_path = hook_script_path(resolved_cursor_home)

    resolved_hook_script_path.parent.mkdir(parents=True, exist_ok=True)

    if not resolved_hook_script_path.exists():
        resolved_hook_script_path.write_text(HOOK_SCRIPT_CONTENT, encoding="utf-8")
        resolved_hook_script_path.chmod(0o755)
        logger("Created Cursor hook script at .cursor/hooks/slacksor_sync.py")
    else:
        existing = resolved_hook_script_path.read_text(encoding="utf-8")
        is_slacksor_managed = "slacksor-hook-events.jsonl" in existing
        is_stale_managed = is_slacksor_managed and 'payload.get("transcript_path"' not in existing
        if is_stale_managed:
            resolved_hook_script_path.write_text(HOOK_SCRIPT_CONTENT, encoding="utf-8")
            resolved_hook_script_path.chmod(0o755)
            logger("Updated Cursor hook script at .cursor/hooks/slacksor_sync.py")

    script_abs = str(resolved_hook_script_path)
    expected_command = f"python3 {script_abs}"

    if not resolved_hooks_json_path.exists():
        hooks_config = _build_hooks_config(expected_command)
        resolved_hooks_json_path.write_text(json.dumps(hooks_config, indent=2) + "\n", encoding="utf-8")
        logger("Created Cursor hooks config at ~/.cursor/hooks.json")
    else:
        _fix_relative_hook_paths(resolved_hooks_json_path, expected_command, logger)


def _build_hooks_config(command: str) -> dict[str, Any]:
    return {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [{"command": command}],
            "afterAgentResponse": [{"command": command}],
        },
    }


def _fix_relative_hook_paths(
    hooks_json_path: Path,
    expected_command: str,
    logger: Callable[[str], None],
) -> None:
    try:
        raw = hooks_json_path.read_text(encoding="utf-8")
    except Exception:
        return
    if "./hooks/slacksor_sync.py" not in raw:
        return
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return
    hooks = config.get("hooks", {})
    changed = False
    for hook_list in hooks.values():
        if not isinstance(hook_list, list):
            continue
        for entry in hook_list:
            if not isinstance(entry, dict):
                continue
            cmd = entry.get("command", "")
            if "./hooks/slacksor_sync.py" in cmd:
                entry["command"] = expected_command
                changed = True
    if changed:
        hooks_json_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        logger("Fixed relative hook paths in ~/.cursor/hooks.json")


def _resolve_workspace_path(event_payload: dict[str, Any], fallback_workspace_path: str) -> str:
    workspace_roots = event_payload.get("workspace_roots")
    if isinstance(workspace_roots, list) and workspace_roots:
        first = workspace_roots[0]
        if isinstance(first, str) and first.strip():
            return str(Path(first).expanduser().resolve())
    return fallback_workspace_path


def _extract_cursor_chat_id(event_payload: dict[str, Any], conversation_id: str) -> str | None:
    transcript_path_value = event_payload.get("transcript_path")
    if isinstance(transcript_path_value, str) and transcript_path_value.strip():
        transcript_path = Path(transcript_path_value.strip())
        if transcript_path.suffix == ".jsonl" and transcript_path.stem:
            return transcript_path.stem
    if conversation_id:
        return conversation_id
    return None


class CursorHookEventWatcher:
    def __init__(
        self,
        db: Database,
        web_client: WebClient,
        logger: Callable[[str], None],
        cursor_home: Path | None = None,
    ) -> None:
        self._db = db
        self._web = web_client
        self._logger = logger
        self._workspace_root = Path.cwd().resolve()
        self._events_file = hook_events_path(cursor_home)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_line_read = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._prime_existing_lines()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _prime_existing_lines(self) -> None:
        try:
            lines = self._events_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            self._last_line_read = 0
            return
        self._last_line_read = len(lines)

    def _run(self) -> None:
        self._events_file.parent.mkdir(parents=True, exist_ok=True)
        for _ in watch(
            self._events_file.parent,
            recursive=False,
            stop_event=self._stop_event,
            yield_on_timeout=True,
            rust_timeout=1000,
        ):
            if self._stop_event.is_set():
                return
            self._process_events_file()

    def _process_events_file(self) -> None:
        try:
            lines = self._events_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return

        if self._last_line_read > len(lines):
            self._last_line_read = 0

        for line_number, line in enumerate(lines, start=1):
            if line_number <= self._last_line_read:
                continue
            parsed = _safe_parse_json(line)
            if parsed is None:
                self._last_line_read = line_number
                continue
            self._process_event(parsed)
            self._last_line_read = line_number

    def _process_event(self, event_payload: dict[str, Any]) -> None:
        event_name = str(event_payload.get("hook_event_name", "")).strip()
        conversation_id = str(event_payload.get("conversation_id", "")).strip()
        if event_name not in {"beforeSubmitPrompt", "afterAgentResponse"}:
            return
        if not conversation_id:
            return

        workspace_path = _resolve_workspace_path(event_payload, str(self._workspace_root))
        cursor_chat_id = _extract_cursor_chat_id(event_payload, conversation_id)
        project = self._db.get_project_by_workspace(workspace_path)
        if project is None or not project.get("channel_id"):
            return
        workspace_path = str(project["workspace_path"])
        channel_id = str(project["channel_id"])

        mapping = self._db.get_hook_conversation(workspace_path, conversation_id)
        if event_name == "beforeSubmitPrompt":
            prompt = str(event_payload.get("prompt", "")).strip()
            if not prompt:
                return
            if mapping is None:
                response = self._web.chat_postMessage(
                    channel=channel_id, text=USER_PROMPT_PREFIX + prompt
                )
                thread_ts = str(response["ts"])
                self._db.upsert_hook_conversation(
                    workspace_path,
                    conversation_id,
                    channel_id,
                    thread_ts,
                    cursor_chat_id=cursor_chat_id,
                )
                return
            self._db.upsert_hook_conversation(
                workspace_path,
                conversation_id,
                str(mapping["channel_id"]),
                str(mapping["thread_ts"]),
                cursor_chat_id=cursor_chat_id,
            )
            self._web.chat_postMessage(
                channel=str(mapping["channel_id"]),
                text=USER_PROMPT_PREFIX + prompt,
                thread_ts=str(mapping["thread_ts"]),
            )
            return

        assistant_text = str(event_payload.get("text", "")).strip()
        if not assistant_text:
            return
        if mapping is None:
            response = self._web.chat_postMessage(
                channel=channel_id, text=AGENT_RESPONSE_PREFIX + assistant_text
            )
            thread_ts = str(response["ts"])
            self._db.upsert_hook_conversation(
                workspace_path,
                conversation_id,
                channel_id,
                thread_ts,
                cursor_chat_id=cursor_chat_id,
            )
            return
        self._db.upsert_hook_conversation(
            workspace_path,
            conversation_id,
            str(mapping["channel_id"]),
            str(mapping["thread_ts"]),
            cursor_chat_id=cursor_chat_id,
        )
        self._web.chat_postMessage(
            channel=str(mapping["channel_id"]),
            text=AGENT_RESPONSE_PREFIX + assistant_text,
            thread_ts=str(mapping["thread_ts"]),
        )


def _safe_parse_json(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None
