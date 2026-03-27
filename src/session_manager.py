from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from config import AppConfig
from cursor_agent import AgentRunResult, CursorAgentClient
from db import Database


STOPPED_MESSAGE = "Agent stopped."


class SlackPoster(Protocol):
    def post_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        ...

    def add_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        ...

    def remove_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        ...


@dataclass
class ActiveProcess:
    workspace_path: str
    session_id: int
    thread_ts: str
    pid: int


@dataclass
class QueuedMessage:
    workspace_path: str
    channel_id: str
    thread_ts: str
    message_ts: str
    prompt: str
    model_override: str | None
    thread_context: str | None


class SessionManager:
    def __init__(
        self,
        db: Database,
        cursor_client: CursorAgentClient,
        slack: SlackPoster,
        config: AppConfig,
        logger: Callable[[str], None],
    ) -> None:
        self._db = db
        self._cursor = cursor_client
        self._slack = slack
        self._config = config
        self._logger = logger
        self._lock = threading.Lock()
        self._active_by_workspace: dict[str, ActiveProcess] = {}
        self._queues: dict[str, deque[QueuedMessage]] = {}

    def recover_orphans(self) -> None:
        running = self._db.list_running_sessions()
        for row in running:
            session_id = int(row["id"])
            pid = int(row["pid"] or 0)
            if pid > 0:
                try:
                    self._cursor.terminate_process(pid)
                    self._logger(f"Killed orphaned cursor process pid={pid}")
                except Exception as exc:
                    self._logger(f"Failed to terminate pid={pid}: {exc}")
            self._db.mark_session_status(session_id, "idle")

    def list_sessions(self) -> list[dict]:
        return self._db.list_sessions()

    def get_active_for_workspace(self, workspace_path: str) -> ActiveProcess | None:
        with self._lock:
            return self._active_by_workspace.get(workspace_path)

    def kill_active_for_workspace(self, workspace_path: str, request_thread_ts: str | None = None) -> bool:
        with self._lock:
            active = self._active_by_workspace.get(workspace_path)
        if active is None:
            return False
        if request_thread_ts is not None and request_thread_ts != active.thread_ts:
            return False
        if active.pid > 0:
            self._cursor.terminate_process(active.pid)
        self._db.mark_session_status(active.session_id, "idle")
        with self._lock:
            self._active_by_workspace.pop(workspace_path, None)
        return True

    def queue_depth(self, workspace_path: str | None = None) -> int:
        with self._lock:
            if workspace_path is not None:
                return len(self._queues.get(workspace_path, deque()))
            return sum(len(q) for q in self._queues.values())

    def _enqueue(
        self,
        workspace_path: str,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        prompt: str,
        model_override: str | None,
        thread_context: str | None,
    ) -> int:
        msg = QueuedMessage(
            workspace_path=workspace_path,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
            prompt=prompt,
            model_override=model_override,
            thread_context=thread_context,
        )
        with self._lock:
            if workspace_path not in self._queues:
                self._queues[workspace_path] = deque()
            self._queues[workspace_path].append(msg)
            position = len(self._queues[workspace_path])
        return position

    def _process_next_in_queue(self, workspace_path: str) -> None:
        with self._lock:
            queue = self._queues.get(workspace_path)
            if not queue:
                return
            msg = queue.popleft()
        self.handle_message(
            workspace_path=msg.workspace_path,
            channel_id=msg.channel_id,
            thread_ts=msg.thread_ts,
            message_ts=msg.message_ts,
            prompt=msg.prompt,
            model_override=msg.model_override,
            thread_context=msg.thread_context,
        )

    def handle_message(
        self,
        workspace_path: str,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        prompt: str,
        model_override: str | None = None,
        thread_context: str | None = None,
    ) -> None:
        effective_prompt = (thread_context + "\n\n" + prompt) if thread_context else prompt
        with self._lock:
            active = self._active_by_workspace.get(workspace_path)
        if active is not None:
            position = self._enqueue(
                workspace_path=workspace_path,
                channel_id=channel_id,
                thread_ts=thread_ts,
                message_ts=message_ts,
                prompt=prompt,
                model_override=model_override,
                thread_context=thread_context,
            )
            self._slack.add_reaction(channel_id, message_ts, "clock1")
            self._slack.post_message(
                channel_id,
                f"Queued (position #{position}). Will process when the current task finishes.",
                thread_ts=thread_ts,
            )
            return

        row = self._db.get_session(channel_id=channel_id, thread_ts=thread_ts)
        if row is None:
            mapped_hook_conversation = self._db.get_hook_conversation_by_thread(
                workspace_path=workspace_path,
                channel_id=channel_id,
                thread_ts=thread_ts,
            )
            if mapped_hook_conversation is not None and mapped_hook_conversation.get("cursor_chat_id"):
                row = self._db.get_or_create_session(
                    workspace_path=workspace_path,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    cursor_chat_id=str(mapped_hook_conversation["cursor_chat_id"]),
                )
            if row is not None:
                default_model = model_override or self._db.get_default_model()
                thread = threading.Thread(
                    target=self._run_prompt_worker,
                    args=(
                        workspace_path,
                        channel_id,
                        thread_ts,
                        message_ts,
                        int(row["id"]),
                        str(row["cursor_chat_id"]),
                        effective_prompt,
                        default_model,
                    ),
                    daemon=True,
                )
                with self._lock:
                    self._active_by_workspace[workspace_path] = ActiveProcess(
                        workspace_path=workspace_path,
                        session_id=int(row["id"]),
                        thread_ts=thread_ts,
                        pid=-1,
                    )
                thread.start()
                return
            try:
                chat_id = self._cursor.create_chat(workspace_path=Path(workspace_path))
            except Exception as exc:
                self._slack.post_message(
                    channel_id,
                    f"Could not start Cursor agent chat: {exc}",
                    thread_ts=thread_ts,
                )
                return
            row = self._db.get_or_create_session(
                workspace_path=workspace_path,
                channel_id=channel_id,
                thread_ts=thread_ts,
                cursor_chat_id=chat_id,
            )
        default_model = model_override or self._db.get_default_model()

        thread = threading.Thread(
            target=self._run_prompt_worker,
            args=(
                workspace_path,
                channel_id,
                thread_ts,
                message_ts,
                int(row["id"]),
                str(row["cursor_chat_id"]),
                effective_prompt,
                default_model,
            ),
            daemon=True,
        )
        with self._lock:
            self._active_by_workspace[workspace_path] = ActiveProcess(
                workspace_path=workspace_path,
                session_id=int(row["id"]),
                thread_ts=thread_ts,
                pid=-1,
            )
        thread.start()

    def shutdown(self) -> None:
        with self._lock:
            active_items = list(self._active_by_workspace.items())
        for workspace_path, active in active_items:
            if active.pid > 0:
                self._cursor.terminate_process(active.pid)
            self._db.mark_session_status(active.session_id, "idle")
            with self._lock:
                self._active_by_workspace.pop(workspace_path, None)

    def stop_workspace_session(self, workspace_path: str) -> bool:
        if self.kill_active_for_workspace(workspace_path):
            return True
        row = self._db.get_active_session_for_workspace(workspace_path)
        if row is None:
            return False
        pid = int(row["pid"] or 0)
        if pid > 0:
            self._cursor.terminate_process(pid)
        self._db.mark_session_status(int(row["id"]), "idle")
        return True

    def stop_all_sessions(self) -> int:
        running = self._db.list_running_sessions()
        count = 0
        for row in running:
            pid = int(row["pid"] or 0)
            if pid > 0:
                self._cursor.terminate_process(pid)
            self._db.mark_session_status(int(row["id"]), "idle")
            count += 1
        with self._lock:
            self._active_by_workspace.clear()
            self._queues.clear()
        return count

    def _run_prompt_worker(
        self,
        workspace_path: str,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        session_id: int,
        chat_id: str,
        prompt: str,
        model: str,
    ) -> None:
        posted: list[str] = []
        suppress_streaming_to_slack = (
            self._config.enable_ide_transcript_mirror and not self._config.enable_cursor_hooks_sync
        )

        def on_chunk(text: str) -> None:
            if suppress_streaming_to_slack:
                return
            formatted_text = _format_for_slack(text)
            for chunk in _split_for_slack(formatted_text, self._config.post_chunk_size):
                posted.append(chunk)
                self._slack.post_message(channel_id, chunk, thread_ts=thread_ts)

        def on_process_started(pid: int) -> None:
            with self._lock:
                self._active_by_workspace[workspace_path] = ActiveProcess(
                    workspace_path=workspace_path,
                    session_id=session_id,
                    thread_ts=thread_ts,
                    pid=pid,
                )
            self._db.set_session_running(
                session_id=session_id,
                workspace_path=workspace_path,
                pid=pid,
            )
            self._slack.remove_reaction(channel_id, message_ts, "eyes")
            self._slack.remove_reaction(channel_id, message_ts, "clock1")
            self._slack.add_reaction(channel_id, message_ts, "hourglass_flowing_sand")

        try:
            pid, result = self._cursor.run_prompt(
                chat_id=chat_id,
                workspace_path=Path(workspace_path),
                prompt=prompt,
                model=model,
                timeout_seconds=self._config.session_timeout_seconds,
                keepalive_seconds=self._config.keepalive_seconds,
                on_assistant_chunk=on_chunk,
                on_keepalive=None,
                on_process_started=on_process_started,
            )
            self._db.mark_session_status(session_id=session_id, status=result.status)
            _store_token_usage(self._db, session_id, result)
            self._slack.remove_reaction(channel_id, message_ts, "hourglass_flowing_sand")
            if result.status == "completed":
                self._slack.add_reaction(channel_id, message_ts, "white_check_mark")
            elif result.status == "timeout":
                self._slack.add_reaction(channel_id, message_ts, "x")
                self._slack.post_message(
                    channel_id,
                    "Agent timed out before completing.",
                    thread_ts=thread_ts,
                )
            elif result.status == "auth_required":
                self._slack.add_reaction(channel_id, message_ts, "lock")
                self._slack.post_message(
                    channel_id,
                    "Cursor Agent is not authenticated. "
                    "Run `cursor agent` in a terminal to log in, then retry your message.",
                    thread_ts=thread_ts,
                )
            else:
                self._slack.add_reaction(channel_id, message_ts, "x")
                error_text = result.stderr or "cursor agent failed."
                self._slack.post_message(channel_id, error_text, thread_ts=thread_ts)
        except Exception as exc:
            self._db.mark_session_status(session_id=session_id, status="failed")
            self._slack.remove_reaction(channel_id, message_ts, "hourglass_flowing_sand")
            self._slack.add_reaction(channel_id, message_ts, "x")
            self._slack.post_message(channel_id, f"Unhandled error: {exc}", thread_ts=thread_ts)
        finally:
            with self._lock:
                self._active_by_workspace.pop(workspace_path, None)
            self._logger(
                f"session={session_id} workspace={workspace_path} thread={thread_ts} messages={len(posted)}"
            )
            self._process_next_in_queue(workspace_path)


def _store_token_usage(db: Database, session_id: int, result: AgentRunResult) -> None:
    payload = result.result_payload
    if not isinstance(payload, dict):
        return
    usage = payload.get("usage") or payload.get("token_usage") or payload
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    if total == 0 and (prompt_tokens or completion_tokens):
        total = prompt_tokens + completion_tokens
    if total > 0:
        db.update_session_tokens(session_id, prompt_tokens, completion_tokens, total)


def _split_for_slack(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + limit])
        start += limit
    return chunks


MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
MARKDOWN_UNDER_BOLD_RE = re.compile(r"__(.+?)__")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
MARKDOWN_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)")


def _format_for_slack(text: str) -> str:
    parts = CODE_BLOCK_RE.split(text)
    if not parts:
        return text
    output: list[str] = []
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            output.append(part)
            continue
        normalized = MARKDOWN_BOLD_RE.sub(r"*\1*", part)
        normalized = MARKDOWN_UNDER_BOLD_RE.sub(r"*\1*", normalized)
        normalized = MARKDOWN_LINK_RE.sub(r"<\2|\1>", normalized)
        normalized = MARKDOWN_HEADER_RE.sub(r"*\2*", normalized)
        output.append(normalized)
    return "".join(output)
