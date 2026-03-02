from __future__ import annotations

import time
from dataclasses import dataclass

from config import AppConfig
from cursor_agent import AgentRunResult
from db import Database
from session_manager import ActiveProcess, SessionManager, _format_for_slack, _split_for_slack, _store_token_usage


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str, str | None]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.removed_reactions: list[tuple[str, str, str]] = []

    def post_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.posts.append((channel_id, text, thread_ts))

    def add_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        self.reactions.append((channel_id, timestamp, emoji))

    def remove_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        self.removed_reactions.append((channel_id, timestamp, emoji))


@dataclass
class FakeResult:
    status: str
    stderr: str = ""
    result_payload: dict | None = None


class FakeCursor:
    def __init__(self) -> None:
        self.terminated: list[int] = []
        self.created = 0
        self.last_run_chat_id: str | None = None
        self.last_run_prompt: str | None = None

    def create_chat(self, workspace_path=None) -> str:
        del workspace_path
        self.created += 1
        return f"chat-{self.created}"

    def run_prompt(
        self,
        chat_id: str,
        workspace_path,
        prompt: str,
        model: str,
        timeout_seconds: int,
        keepalive_seconds: int,
        on_assistant_chunk,
        on_keepalive,
        on_process_started,
    ):
        self.last_run_chat_id = chat_id
        self.last_run_prompt = prompt
        del workspace_path, model, timeout_seconds, keepalive_seconds, on_keepalive
        on_process_started(101)
        on_assistant_chunk("hello")
        return 101, FakeResult(status="completed")

    def terminate_process(self, pid: int) -> None:
        self.terminated.append(pid)


def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


def test_handle_message_creates_session_and_posts(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager.handle_message("/tmp/proj", "C1", "100.1", "100.1", "fix this")

    def _done() -> bool:
        rows = database.list_sessions()
        return bool(rows) and rows[0]["status"] == "completed"

    _wait_until(_done)
    assert any("hello" in post[1] for post in slack.posts)
    assert ("C1", "100.1", "white_check_mark") in slack.reactions
    assert ("C1", "100.1", "hourglass_flowing_sand") in slack.reactions
    assert ("C1", "100.1", "eyes") in slack.removed_reactions


def test_handle_message_prepends_thread_context(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    context = "Context from this Slack thread:\n\nUser: /review\nAgent: Summary: 5 minor, 3 nits."
    manager.handle_message(
        "/tmp/proj", "C1", "100.1", "100.1", "how many minor?", thread_context=context
    )

    def _done() -> bool:
        return cursor.last_run_prompt is not None

    _wait_until(_done)
    assert cursor.last_run_prompt is not None
    assert cursor.last_run_prompt.startswith("Context from this Slack thread:")
    assert "User: /review" in cursor.last_run_prompt
    assert "how many minor?" in cursor.last_run_prompt
    assert cursor.last_run_prompt.endswith("how many minor?")


def test_kill_active(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    row = database.get_or_create_session("/tmp/proj", "C1", "100.1", "chat-1")
    database.set_session_running(int(row["id"]), workspace_path="/tmp/proj", pid=1000)
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager._active_by_workspace["/tmp/proj"] = ActiveProcess(  # noqa: SLF001
        workspace_path="/tmp/proj",
        session_id=int(row["id"]),
        thread_ts="100.1",
        pid=1000,
    )
    killed = manager.kill_active_for_workspace("/tmp/proj", "100.1")
    assert killed is True


def test_queue_depth_empty(database: Database, app_config: AppConfig) -> None:
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    assert manager.queue_depth() == 0
    assert manager.queue_depth("/tmp/proj") == 0


def test_queues_message_when_active(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager._active_by_workspace["/tmp/proj"] = ActiveProcess(  # noqa: SLF001
        workspace_path="/tmp/proj",
        session_id=1,
        thread_ts="100.1",
        pid=1000,
    )
    manager.handle_message("/tmp/proj", "C1", "100.2", "100.2", "another")
    assert any("Queued (position #1)" in text for _, text, _ in slack.posts)
    assert manager.queue_depth("/tmp/proj") == 1
    assert ("C1", "100.2", "clock1") in slack.reactions


def test_multiple_queued_messages_increment_position(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager._active_by_workspace["/tmp/proj"] = ActiveProcess(  # noqa: SLF001
        workspace_path="/tmp/proj",
        session_id=1,
        thread_ts="100.1",
        pid=1000,
    )
    manager.handle_message("/tmp/proj", "C1", "100.2", "100.2", "first queued")
    manager.handle_message("/tmp/proj", "C1", "100.3", "100.3", "second queued")
    assert manager.queue_depth("/tmp/proj") == 2
    queued_posts = [text for _, text, _ in slack.posts if "Queued" in text]
    assert "position #1" in queued_posts[0]
    assert "position #2" in queued_posts[1]


def test_stop_all_clears_queues(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager._active_by_workspace["/tmp/proj"] = ActiveProcess(  # noqa: SLF001
        workspace_path="/tmp/proj",
        session_id=1,
        thread_ts="100.1",
        pid=1000,
    )
    manager.handle_message("/tmp/proj", "C1", "100.2", "100.2", "queued msg")
    assert manager.queue_depth() == 1
    manager.stop_all_sessions()
    assert manager.queue_depth() == 0


def test_recover_orphans_marks_idle(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    row = database.get_or_create_session("/tmp/proj", "C1", "100.1", "chat-1")
    database.set_session_running(int(row["id"]), workspace_path="/tmp/proj", pid=1000)
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager.recover_orphans()
    assert cursor.terminated == [1000]
    updated = database.get_session("C1", "100.1")
    assert updated is not None
    assert updated["status"] == "idle"


def test_split_for_slack() -> None:
    chunks = _split_for_slack("abcdef", 2)
    assert chunks == ["ab", "cd", "ef"]


def test_format_for_slack_markdown() -> None:
    formatted = _format_for_slack(
        "## Header\n**bold** and __alt__ and [link](https://example.com)\n```python\n**nochange**\n```"
    )
    assert "*Header*" in formatted
    assert "*bold*" in formatted
    assert "*alt*" in formatted
    assert "<https://example.com|link>" in formatted
    assert "```python\n**nochange**\n```" in formatted


def test_create_chat_failure_posts_error(database: Database, app_config: AppConfig) -> None:
    class BrokenCursor(FakeCursor):
        def create_chat(self, workspace_path=None) -> str:
            del workspace_path
            raise RuntimeError("Workspace Trust Required")

    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = BrokenCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager.handle_message("/tmp/proj", "C1", "100.1", "100.1", "fix this")
    assert any("Could not start Cursor agent chat" in text for _, text, _ in slack.posts)


def test_handle_message_skips_stream_chunks_when_mirror_enabled(database: Database, app_config: AppConfig) -> None:
    mirrored_config = AppConfig(
        slack_bot_token=app_config.slack_bot_token,
        slack_app_token=app_config.slack_app_token,
        db_path=app_config.db_path,
        session_timeout_seconds=app_config.session_timeout_seconds,
        keepalive_seconds=app_config.keepalive_seconds,
        post_chunk_size=app_config.post_chunk_size,
        polling_interval_seconds=app_config.polling_interval_seconds,
        enable_ide_transcript_mirror=True,
        enable_cursor_hooks_sync=False,
    )
    database.add_project("/tmp/proj", "proj", "C1")
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, mirrored_config, logger=lambda _: None)
    manager.handle_message("/tmp/proj", "C1", "100.1", "100.1", "fix this")

    def _done() -> bool:
        rows = database.list_sessions()
        return bool(rows) and rows[0]["status"] == "completed"

    _wait_until(_done)
    assert all(text != "hello" for _, text, _ in slack.posts)


def test_store_token_usage_from_result_payload(database: Database) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    row = database.get_or_create_session("/tmp/proj", "C1", "100.1", "chat-1")
    session_id = int(row["id"])
    result = AgentRunResult(
        status="completed",
        result_payload={"usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}},
    )
    _store_token_usage(database, session_id, result)
    tokens = database.get_session_tokens(session_id)
    assert tokens == {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}


def test_store_token_usage_with_input_output_keys(database: Database) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    row = database.get_or_create_session("/tmp/proj", "C1", "100.1", "chat-1")
    session_id = int(row["id"])
    result = AgentRunResult(
        status="completed",
        result_payload={"usage": {"input_tokens": 150, "output_tokens": 75}},
    )
    _store_token_usage(database, session_id, result)
    tokens = database.get_session_tokens(session_id)
    assert tokens == {"prompt_tokens": 150, "completion_tokens": 75, "total_tokens": 225}


def test_store_token_usage_no_payload(database: Database) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    row = database.get_or_create_session("/tmp/proj", "C1", "100.1", "chat-1")
    session_id = int(row["id"])
    result = AgentRunResult(status="completed", result_payload=None)
    _store_token_usage(database, session_id, result)
    tokens = database.get_session_tokens(session_id)
    assert tokens == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_store_token_usage_no_token_data_in_payload(database: Database) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    row = database.get_or_create_session("/tmp/proj", "C1", "100.1", "chat-1")
    session_id = int(row["id"])
    result = AgentRunResult(
        status="completed",
        result_payload={"type": "result", "is_error": False},
    )
    _store_token_usage(database, session_id, result)
    tokens = database.get_session_tokens(session_id)
    assert tokens == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_handle_message_uses_hook_mapped_chat_id(database: Database, app_config: AppConfig) -> None:
    database.add_project("/tmp/proj", "proj", "C1")
    database.upsert_hook_conversation(
        "/tmp/proj",
        "conv-1",
        "C1",
        "100.1",
        cursor_chat_id="ide-chat-1",
    )
    slack = FakeSlack()
    cursor = FakeCursor()
    manager = SessionManager(database, cursor, slack, app_config, logger=lambda _: None)
    manager.handle_message("/tmp/proj", "C1", "100.1", "100.1", "continue from mobile")

    def _done() -> bool:
        rows = database.list_sessions()
        return bool(rows) and rows[0]["status"] == "completed"

    _wait_until(_done)
    assert cursor.created == 0
    assert cursor.last_run_chat_id == "ide-chat-1"
