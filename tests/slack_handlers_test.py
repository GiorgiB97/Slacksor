from __future__ import annotations

import slack_handlers
from db import Database
from slack_handlers import SlackClientAdapter, SlackEventRouter, _format_uptime


class FakeSessions:
    def __init__(self) -> None:
        self.handled: list[tuple[str, str, str, str, str, str | None, str | None]] = []
        self.killed: list[tuple[str, str | None]] = []
        self.active_thread: str | None = None

    def get_active_for_workspace(self, workspace_path: str):
        if self.active_thread is None:
            return None
        return type("Active", (), {"thread_ts": self.active_thread, "workspace_path": workspace_path})

    def queue_depth(self, workspace_path: str | None = None) -> int:
        del workspace_path
        return 0

    def kill_active_for_workspace(self, workspace_path: str, request_thread_ts: str | None = None) -> bool:
        self.killed.append((workspace_path, request_thread_ts))
        return True

    def stop_workspace_session(self, workspace_path: str) -> bool:
        self.killed.append((workspace_path, None))
        return False

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
        self.handled.append(
            (workspace_path, channel_id, thread_ts, message_ts, prompt, model_override, thread_context)
        )


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str, str | None]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.removed_reactions: list[tuple[str, str, str]] = []
        self.thread_replies: list[dict] = []
        self.presence: str | None = None

    def post_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.posts.append((channel_id, text, thread_ts))

    def add_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        self.reactions.append((channel_id, timestamp, emoji))

    def remove_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        self.removed_reactions.append((channel_id, timestamp, emoji))

    def get_thread_replies(self, channel_id: str, thread_ts: str) -> list[dict]:
        return list(self.thread_replies)

    def set_presence(self, status: str) -> None:
        self.presence = status


def test_router_routes_normal_message(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "hello", "ts": "10.1", "thread_ts": "10.1"}
    )
    assert sessions.handled == [("/tmp/a", "C1", "10.1", "10.1", "hello", None, None)]
    assert ("C1", "10.1", "eyes") in slack.reactions


def test_router_filters_bot_messages(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "hello", "ts": "10.1", "bot_id": "B1"})
    assert sessions.handled == []


def test_router_stop_only_active_thread(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "stop", "ts": "10.1", "thread_ts": "10.1"})
    assert sessions.killed == [("/tmp/a", "10.1")]
    assert slack.posts


def test_router_routes_to_handler_when_active(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "keep going", "ts": "10.2", "thread_ts": "10.1"}
    )
    assert len(sessions.handled) == 1
    assert sessions.handled[0][4] == "keep going"


def test_router_help_and_model_commands(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(
        database,
        sessions,
        slack,
        logger=lambda _: None,
        model_options_provider=lambda: ["auto", "opus-4.6-thinking", "gpt-5.3-codex"],
    )
    router.handle_message_event({"channel": "C1", "text": "help", "ts": "10.1"})
    router.handle_message_event({"channel": "C1", "text": "model", "ts": "10.2"})
    router.handle_message_event({"channel": "C1", "text": "model gpt-5", "ts": "10.3"})
    assert any("show this help" in text for _, text, _ in slack.posts)
    assert any("Current model" in text for _, text, _ in slack.posts)
    assert any("opus-4.6-thinking" in text for _, text, _ in slack.posts)
    assert any("Default model updated" in text for _, text, _ in slack.posts)


def test_router_passes_project_model_override(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1", default_model_override="gpt-5")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "run task", "ts": "11.1"})
    assert sessions.handled == [("/tmp/a", "C1", "11.1", "11.1", "run task", "gpt-5", None)]


def test_router_uses_voice_transcription_as_prompt(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {
            "channel": "C1",
            "text": "",
            "ts": "11.2",
            "files": [{"mimetype": "audio/m4a", "transcription": {"text": "summarize latest PR"}}],
        }
    )
    assert sessions.handled == [("/tmp/a", "C1", "11.2", "11.2", "summarize latest PR", None, None)]
    assert ("C1", "11.2", "eyes") in slack.reactions


def test_router_prefers_voice_transcription_over_message_text(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {
            "channel": "C1",
            "text": "voice message",
            "ts": "11.3",
            "files": [{"mimetype": "audio/m4a", "transcription_text": "show recent commits"}],
        }
    )
    assert sessions.handled == [("/tmp/a", "C1", "11.3", "11.3", "show recent commits", None, None)]


def test_router_model_command_uses_db_cached_models(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    database.set_model_options_cache(["auto", "opus-4.6-thinking"], ttl_seconds=300)
    sessions = FakeSessions()
    slack = FakeSlack()

    def failing_provider() -> list[str]:
        raise RuntimeError("should not fetch")

    router = SlackEventRouter(
        database,
        sessions,
        slack,
        logger=lambda _: None,
        model_options_provider=failing_provider,
    )
    router.handle_message_event({"channel": "C1", "text": "model", "ts": "11.4"})
    assert any("opus-4.6-thinking" in text for _, text, _ in slack.posts)


def test_router_stop_when_idle(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "stop", "ts": "10.1"})
    assert any("No active agent session" in text for _, text, _ in slack.posts)


class FakeWeb:
    def __init__(self) -> None:
        self.joined: list[str] = []
        self.created: list[str] = []
        self.channels = [{"id": "C1", "name": "existing"}]

    def conversations_list(self, types: str, exclude_archived: bool, limit: int, cursor=None):
        del types, exclude_archived, limit, cursor
        return {"channels": self.channels, "response_metadata": {"next_cursor": ""}}

    def conversations_join(self, channel: str) -> None:
        self.joined.append(channel)

    def conversations_create(self, name: str):
        self.created.append(name)
        return {"channel": {"id": "C_NEW"}}


def test_get_thread_replies_returns_sorted_messages() -> None:
    class WebWithReplies:
        def conversations_replies(self, channel: str, ts: str, limit: int, cursor: str | None = None):
            del channel, ts, limit
            if cursor:
                return {"messages": [], "response_metadata": {"next_cursor": ""}}
            return {
                "messages": [
                    {"ts": "10.2", "user": "U1", "text": "second"},
                    {"ts": "10.0", "user": None, "bot_id": "B1", "text": "first"},
                ],
                "response_metadata": {"next_cursor": ""},
            }

    adapter = SlackClientAdapter(WebWithReplies())  # type: ignore[arg-type]
    replies = adapter.get_thread_replies("C1", "10.0")
    assert len(replies) == 2
    assert replies[0]["ts"] == "10.0"
    assert replies[0]["text"] == "first"
    assert replies[1]["ts"] == "10.2"
    assert replies[1]["text"] == "second"


def test_ensure_channel_existing() -> None:
    fake = FakeWeb()
    adapter = SlackClientAdapter(fake)  # type: ignore[arg-type]
    channel_id = adapter.ensure_channel("existing")
    assert channel_id == "C1"
    assert fake.joined == ["C1"]


def test_ensure_channel_create_new() -> None:
    fake = FakeWeb()
    fake.channels = []
    adapter = SlackClientAdapter(fake)  # type: ignore[arg-type]
    channel_id = adapter.ensure_channel("newchan")
    assert channel_id == "C_NEW"
    assert fake.created == ["newchan"]


def test_router_passes_thread_context_when_replies_exist(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    slack.thread_replies = [
        {"ts": "10.0", "user": "U1", "bot_id": None, "text": "/review"},
        {"ts": "10.1", "user": None, "bot_id": "B1", "text": "Summary of the review: 5 minor, 3 nits."},
    ]
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "how many minor issues?", "ts": "10.2", "thread_ts": "10.0"}
    )
    assert len(sessions.handled) == 1
    _, _, _, _, prompt, _, thread_context = sessions.handled[0]
    assert thread_context is not None
    assert "Context from this Slack thread" in thread_context
    assert "User: /review" in thread_context
    assert "Agent: Summary of the review" in thread_context
    assert prompt == "how many minor issues?"


def test_router_excludes_current_message_from_thread_context(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    slack.thread_replies = [
        {"ts": "10.0", "user": "U1", "bot_id": None, "text": "first"},
        {"ts": "10.2", "user": "U1", "bot_id": None, "text": "current message"},
    ]
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "current message", "ts": "10.2", "thread_ts": "10.0"}
    )
    _, _, _, _, _, _, thread_context = sessions.handled[0]
    assert thread_context is not None
    assert "User: first" in thread_context
    assert "User: current message" not in thread_context


def test_router_shell_command_runs_subprocess(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "!echo hello world", "ts": "10.1"}
    )
    assert sessions.handled == []
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "$ echo hello world" in text
    assert "hello world" in text
    assert text.startswith("```")


def test_router_shell_command_shows_exit_code_on_failure(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "!exit 42", "ts": "10.1"}
    )
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "exit code: 42" in text


def test_router_shell_command_not_blocked_by_active_session(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "!echo still works", "ts": "10.2", "thread_ts": "10.1"}
    )
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "still works" in text
    assert not any("Agent busy" in t for _, t, _ in slack.posts)


def test_router_shell_command_no_output(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "!true", "ts": "10.1"}
    )
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "(no output)" in text


def test_router_shell_command_truncates_long_output(database: Database, tmp_path, monkeypatch) -> None:
    import slack_handlers as sh
    monkeypatch.setattr(sh, "SHELL_OUTPUT_MAX_CHARS", 50)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    long_text = "x" * 200
    router.handle_message_event(
        {"channel": "C1", "text": f"!echo {long_text}", "ts": "10.1"}
    )
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "... (truncated)" in text


def test_router_shell_command_timeout(database: Database, tmp_path, monkeypatch) -> None:
    import slack_handlers as sh
    monkeypatch.setattr(sh, "SHELL_COMMAND_TIMEOUT_SECONDS", 1)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "!sleep 10", "ts": "10.1"}
    )
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "timed out" in text


def test_router_bare_bang_routed_as_prompt(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "!", "ts": "10.1"}
    )
    assert len(sessions.handled) == 1
    assert sessions.handled[0][4] == "!"


def test_router_enriches_prompt_with_slash_command(database: Database, tmp_path) -> None:
    cmd_dir = tmp_path / ".cursor" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("# review command")
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "/review my code", "ts": "10.1"}
    )
    assert len(sessions.handled) == 1
    prompt = sessions.handled[0][4]
    assert "Use following cursor command '/review'" in prompt
    assert str(cmd_dir / "review.md") in prompt


def test_router_slash_command_not_found_passes_original(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "/nonexistent do something", "ts": "10.1"}
    )
    assert len(sessions.handled) == 1
    prompt = sessions.handled[0][4]
    assert prompt == "/nonexistent do something"


def test_router_slash_command_mixed_with_text(database: Database, tmp_path) -> None:
    cmd_dir = tmp_path / ".cursor" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "tests.md").write_text("# tests command")
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "please run /tests on the auth module", "ts": "10.1"}
    )
    assert len(sessions.handled) == 1
    prompt = sessions.handled[0][4]
    assert prompt.startswith("please run /tests on the auth module\n\n")
    assert "Use following cursor command '/tests'" in prompt


def test_format_uptime() -> None:
    assert _format_uptime(0) == "0s"
    assert _format_uptime(59) == "59s"
    assert _format_uptime(60) == "1m 0s"
    assert _format_uptime(3661) == "1h 1m 1s"
    assert _format_uptime(90061) == "1d 1h 1m 1s"


def test_router_ping_responds_with_status(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "ping", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "Pong!" in text
    assert "Uptime:" in text
    assert "Active sessions:" in text
    assert "Queued messages:" in text


def test_router_ping_works_when_active(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "ping", "ts": "10.2", "thread_ts": "10.1"}
    )
    assert len(slack.posts) == 1
    assert "Pong!" in slack.posts[0][1]
    assert len(sessions.handled) == 0


def test_router_branch_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "branch", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "```" in text
    assert ">>>" in text
    assert len(sessions.handled) == 0


def test_router_branch_command_not_git_repo(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "branch", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "failed" in text.lower() or "not a git" in text.lower() or "fatal" in text.lower()


def test_router_status_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "status", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "```" in text
    assert "git status" in text
    assert len(sessions.handled) == 0


def test_router_status_not_blocked_by_active_session(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "status", "ts": "10.2", "thread_ts": "10.1"}
    )
    assert len(slack.posts) == 1
    assert "git status" in slack.posts[0][1]
    assert len(sessions.handled) == 0


def test_router_branch_not_blocked_by_active_session(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "branch", "ts": "10.2", "thread_ts": "10.1"}
    )
    assert len(slack.posts) == 1
    assert "```" in slack.posts[0][1]
    assert len(sessions.handled) == 0


def test_router_diff_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "hello.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add hello"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "hello.txt").write_text("hello world\nchanged line\n")
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "diff", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "hello.txt" in text
    assert "Unstaged changes" in text
    assert len(sessions.handled) == 0


def test_router_diff_command_staged_changes(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "diff", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "a.py" in text
    assert "Staged changes" in text


def test_router_diff_command_no_changes(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "diff", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "No changes detected" in text


def test_router_diff_not_blocked_by_active_session(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    sessions.active_thread = "10.1"
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event(
        {"channel": "C1", "text": "diff", "ts": "10.2", "thread_ts": "10.1"}
    )
    assert len(slack.posts) == 1
    assert len(sessions.handled) == 0


def test_set_presence_calls_web_client() -> None:
    class WebWithPresence:
        def __init__(self) -> None:
            self.presence_calls: list[str] = []

        def users_setPresence(self, presence: str) -> None:
            self.presence_calls.append(presence)

    web = WebWithPresence()
    adapter = SlackClientAdapter(web)  # type: ignore[arg-type]
    adapter.set_presence("auto")
    adapter.set_presence("away")
    assert web.presence_calls == ["auto", "away"]


def test_set_presence_logs_error_on_failure(monkeypatch) -> None:
    class FakeSlackApiError(Exception):
        def __init__(self) -> None:
            self.response = {"error": "not_allowed"}

    class FailingWeb:
        def users_setPresence(self, presence: str) -> None:
            del presence
            raise FakeSlackApiError()

    monkeypatch.setattr(slack_handlers, "SlackApiError", FakeSlackApiError)
    logs: list[str] = []
    adapter = SlackClientAdapter(FailingWeb(), logger=logs.append)  # type: ignore[arg-type]
    adapter.set_presence("auto")
    assert len(logs) == 1
    assert "not_allowed" in logs[0]


def test_set_presence_missing_scope_disables_future_calls(monkeypatch) -> None:
    class FakeSlackApiError(Exception):
        def __init__(self) -> None:
            self.response = {"error": "missing_scope"}

    class FailingWeb:
        def __init__(self) -> None:
            self.call_count = 0

        def users_setPresence(self, presence: str) -> None:
            del presence
            self.call_count += 1
            raise FakeSlackApiError()

    monkeypatch.setattr(slack_handlers, "SlackApiError", FakeSlackApiError)
    logs: list[str] = []
    web = FailingWeb()
    adapter = SlackClientAdapter(web, logger=logs.append)  # type: ignore[arg-type]
    adapter.set_presence("auto")
    adapter.set_presence("away")
    assert web.call_count == 1
    assert len(logs) == 1
    assert "users:write" in logs[0]


def test_router_checkout_creates_branch(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "checkout new-feature", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "new-feature" in text
    assert len(sessions.handled) == 0


def test_router_checkout_switches_existing_branch(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "branch", "existing-branch"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "checkout existing-branch", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "existing-branch" in text


def test_router_checkout_no_branch_shows_usage(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "checkout", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "Usage" in text


def test_router_stash_list_empty(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "stash", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "empty" in text.lower()


def test_router_stash_list_with_entries(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "file.txt").write_text("content")
    subprocess.run(["git", "add", "file.txt"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "stash"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "stash", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "```" in text
    assert "stash@{0}" in text


def test_router_pull_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "pull", "ts": "10.1"})
    assert len(slack.posts) == 1
    assert len(sessions.handled) == 0


def test_router_ls_command(database: Database, tmp_path) -> None:
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "ls", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "```" in text
    assert "ls -la" in text
    assert len(sessions.handled) == 0


def test_router_dir_command(database: Database) -> None:
    database.add_project("/tmp/myproject", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "dir", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "/tmp/myproject" in text
    assert len(sessions.handled) == 0


def test_router_log_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "log", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "```" in text
    assert "init" in text
    assert len(sessions.handled) == 0


def test_router_last_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "last", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "```" in text
    assert len(sessions.handled) == 0


def test_router_whoami_command(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "whoami", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "Test User" in text
    assert "test@example.com" in text


def test_router_blame_no_file_shows_usage(database: Database) -> None:
    database.add_project("/tmp/a", "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "blame", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "Usage" in text


def test_router_blame_with_file(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "hello.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "hello.py"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add hello"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "blame hello.py", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "hello.py" in text


def test_router_conflicts_none(database: Database, tmp_path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    database.add_project(str(tmp_path), "a", "C1")
    sessions = FakeSessions()
    slack = FakeSlack()
    router = SlackEventRouter(database, sessions, slack, logger=lambda _: None)
    router.handle_message_event({"channel": "C1", "text": "conflicts", "ts": "10.1"})
    assert len(slack.posts) == 1
    _, text, _ = slack.posts[0]
    assert "No merge conflicts" in text


def test_remove_reaction_non_json_error_does_not_raise(monkeypatch) -> None:
    class FakeSlackApiError(Exception):
        def __init__(self) -> None:
            self.response = object()

    class BrokenWeb(FakeWeb):
        def reactions_remove(self, channel: str, timestamp: str, name: str) -> None:
            del channel, timestamp, name
            raise FakeSlackApiError()

    monkeypatch.setattr(slack_handlers, "SlackApiError", FakeSlackApiError)
    adapter = SlackClientAdapter(BrokenWeb(), logger=lambda _: None)  # type: ignore[arg-type]
    adapter.remove_reaction("C1", "10.1", "eyes")
