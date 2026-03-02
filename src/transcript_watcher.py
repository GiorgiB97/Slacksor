from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from slack_sdk import WebClient
from watchfiles import Change, watch

from db import Database


def encode_workspace_path(workspace_path: str) -> str:
    cleaned = str(Path(workspace_path).resolve())
    stripped = cleaned.lstrip("/")
    return stripped.replace("/", "-")


def _extract_text(payload: dict) -> str:
    message = payload.get("message", {})
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


class TranscriptWatcher:
    def __init__(
        self,
        db: Database,
        web_client: WebClient,
        logger: Callable[[str], None],
        cursor_projects_root: Path | None = None,
        only_session_backed: bool = False,
    ) -> None:
        self._db = db
        self._web = web_client
        self._logger = logger
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._projects_root = cursor_projects_root or Path.home() / ".cursor" / "projects"
        self._watch_started_at_epoch: float | None = None
        self._only_session_backed = only_session_backed

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        if not self._projects_root.exists():
            return
        self._watch_started_at_epoch = time.time()
        self._scan_existing_files()
        for changes in watch(
            self._projects_root,
            recursive=True,
            stop_event=self._stop_event,
            yield_on_timeout=True,
            rust_timeout=1000,
            watch_filter=_jsonl_watch_filter,
        ):
            if self._stop_event.is_set():
                return
            if not changes:
                continue
            for _, file_path in changes:
                path = Path(file_path)
                if path.suffix != ".jsonl":
                    continue
                self._process_transcript(path)

    def _scan_existing_files(self) -> None:
        for path in self._projects_root.glob("*/agent-transcripts/**/*.jsonl"):
            self._process_transcript(path)

    def _process_transcript(self, file_path: Path) -> None:
        if "/subagents/" in str(file_path):
            return
        parts = file_path.parts
        if "agent-transcripts" not in parts:
            return

        encoded = None
        for index, part in enumerate(parts):
            if part == "agent-transcripts" and index > 0:
                encoded = parts[index - 1]
                break
        if encoded is None:
            return

        workspace_path = self._workspace_for_encoded(encoded)
        if workspace_path is None:
            return

        project = self._db.get_project_by_workspace(workspace_path)
        if project is None or not project.get("channel_id"):
            return
        channel_id = str(project["channel_id"])
        is_flat_transcript = file_path.parent.name == "agent-transcripts"
        chat_id = file_path.stem
        session = self._db.get_session_by_cursor_chat_id(workspace_path, chat_id)
        if is_flat_transcript and session is None:
            # Flat transcript files generally come from CLI runs. Ignore them unless
            # they map to an existing Slack thread session.
            return
        if session is not None:
            channel_id = str(session["channel_id"])
        if self._only_session_backed and session is None:
            return
        state = self._db.get_transcript_state(str(file_path))
        last_read = int(state["last_line_read"]) if state else 0
        if state and state.get("thread_ts"):
            thread_ts = str(state["thread_ts"])
        elif session is not None:
            thread_ts = str(session["thread_ts"])
        else:
            thread_ts = None

        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return

        if state is None and self._is_preexisting_transcript(file_path):
            self._db.upsert_transcript_state(
                transcript_file=str(file_path),
                workspace_path=workspace_path,
                channel_id=channel_id,
                thread_ts=thread_ts,
                last_line_read=len(lines),
            )
            return

        for line_number, line in enumerate(lines, start=1):
            if line_number <= last_read:
                continue
            parsed = _safe_parse_json(line)
            if parsed is None:
                continue
            role = parsed.get("role")
            text = _extract_text(parsed)
            if role not in {"user", "assistant"} or not text:
                continue
            if session is not None and role == "user":
                # Slack already contains the user's original message for
                # session-backed transcripts.
                last_read = line_number
                self._db.upsert_transcript_state(
                    transcript_file=str(file_path),
                    workspace_path=workspace_path,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    last_line_read=last_read,
                )
                continue
            if thread_ts is None and role == "user":
                response = self._web.chat_postMessage(channel=channel_id, text=text)
                thread_ts = str(response["ts"])
            elif thread_ts is not None:
                self._web.chat_postMessage(channel=channel_id, text=text, thread_ts=thread_ts)
            last_read = line_number
            self._db.upsert_transcript_state(
                transcript_file=str(file_path),
                workspace_path=workspace_path,
                channel_id=channel_id,
                thread_ts=thread_ts,
                last_line_read=last_read,
            )

    def _workspace_for_encoded(self, encoded: str) -> str | None:
        target = encoded.lower()
        for project in self._db.list_projects():
            workspace_path = str(project["workspace_path"])
            if encode_workspace_path(workspace_path).lower() == target:
                return workspace_path
        return None

    def _is_preexisting_transcript(self, file_path: Path) -> bool:
        if self._watch_started_at_epoch is None:
            return False
        try:
            modified_at = file_path.stat().st_mtime
        except FileNotFoundError:
            return False
        return modified_at < self._watch_started_at_epoch


def _safe_parse_json(line: str) -> dict | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None

def _jsonl_watch_filter(change: Change, path: str) -> bool:
    del change
    return path.endswith(".jsonl")
