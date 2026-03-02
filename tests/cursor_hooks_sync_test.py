from __future__ import annotations

import json
from pathlib import Path

from cursor_hooks_sync import (
    AGENT_RESPONSE_PREFIX,
    CursorHookEventWatcher,
    USER_PROMPT_PREFIX,
    ensure_cursor_hook_files,
    hook_events_path,
    hook_script_path,
    hooks_config_path,
)
from db import Database


class FakeWebClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, str | None]] = []

    def chat_postMessage(self, channel: str, text: str, thread_ts: str | None = None) -> dict[str, str]:
        ts = f"{len(self.posts) + 1}.000"
        payload = {
            "channel": channel,
            "text": text,
            "thread_ts": thread_ts,
            "ts": ts,
        }
        self.posts.append(payload)
        return {"ts": ts}


def test_ensure_cursor_hook_files_creates_missing_files(tmp_path: Path) -> None:
    messages: list[str] = []
    ensure_cursor_hook_files(messages.append, cursor_home=tmp_path)

    config_path = hooks_config_path(tmp_path)
    script_path = hook_script_path(tmp_path)
    assert config_path.exists()
    assert script_path.exists()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["version"] == 1
    assert "beforeSubmitPrompt" in config["hooks"]
    assert "afterAgentResponse" in config["hooks"]
    expected_script = str(hook_script_path(tmp_path))
    assert config["hooks"]["beforeSubmitPrompt"][0]["command"] == f"python3 {expected_script}"
    assert config["hooks"]["afterAgentResponse"][0]["command"] == f"python3 {expected_script}"
    assert any("Created Cursor hooks config" in message for message in messages)


def test_ensure_cursor_hook_files_does_not_overwrite_existing(tmp_path: Path) -> None:
    config_path = hooks_config_path(tmp_path)
    script_path = hook_script_path(tmp_path)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"version": 1, "hooks": {}}\n', encoding="utf-8")
    script_path.write_text("#!/usr/bin/env python3\nprint('existing')\n", encoding="utf-8")

    ensure_cursor_hook_files(lambda _: None, cursor_home=tmp_path)

    assert config_path.read_text(encoding="utf-8") == '{"version": 1, "hooks": {}}\n'
    assert "existing" in script_path.read_text(encoding="utf-8")


def test_ensure_cursor_hook_files_updates_stale_managed_script(tmp_path: Path) -> None:
    script_path = hook_script_path(tmp_path)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "events_path = Path('.cursor/slacksor-hook-events.jsonl')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    ensure_cursor_hook_files(lambda _: None, cursor_home=tmp_path)

    updated_script = script_path.read_text(encoding="utf-8")
    assert 'payload.get("transcript_path"' in updated_script


def test_hook_event_watcher_posts_and_threads(database: Database, tmp_path: Path) -> None:
    workspace_path = str(tmp_path.resolve())
    database.add_project(workspace_path, "proj", "C1")
    web = FakeWebClient()
    watcher = CursorHookEventWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_home=tmp_path,
    )
    watcher._process_event(  # noqa: SLF001
        {
            "hook_event_name": "beforeSubmitPrompt",
            "conversation_id": "conv-1",
            "workspace_roots": [workspace_path],
            "transcript_path": f"{workspace_path}/.cursor/agent-transcripts/ide-chat-1.jsonl",
            "prompt": "Hello from Cursor",
        }
    )
    watcher._process_event(  # noqa: SLF001
        {
            "hook_event_name": "afterAgentResponse",
            "conversation_id": "conv-1",
            "workspace_roots": [workspace_path],
            "transcript_path": f"{workspace_path}/.cursor/agent-transcripts/ide-chat-1.jsonl",
            "text": "Hello from assistant",
        }
    )

    assert len(web.posts) == 2
    assert web.posts[0]["text"] == USER_PROMPT_PREFIX + "Hello from Cursor"
    assert web.posts[0]["thread_ts"] is None
    assert web.posts[1]["text"] == AGENT_RESPONSE_PREFIX + "Hello from assistant"
    assert web.posts[1]["thread_ts"] == "1.000"
    mapping = database.get_hook_conversation(workspace_path, "conv-1")
    assert mapping is not None
    assert mapping["thread_ts"] == "1.000"
    assert mapping["cursor_chat_id"] == "ide-chat-1"


def test_hook_event_watcher_handles_workspace_case_mismatch(database: Database, tmp_path: Path) -> None:
    actual_workspace = str(tmp_path.resolve())
    database.add_project(actual_workspace.upper(), "proj", "C1")
    web = FakeWebClient()
    watcher = CursorHookEventWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_home=tmp_path,
    )
    watcher._process_event(  # noqa: SLF001
        {
            "hook_event_name": "beforeSubmitPrompt",
            "conversation_id": "conv-case",
            "workspace_roots": [actual_workspace.lower()],
            "prompt": "case test",
        }
    )
    assert len(web.posts) == 1
    assert web.posts[0]["text"] == USER_PROMPT_PREFIX + "case test"
    mapping = database.get_hook_conversation(actual_workspace.upper(), "conv-case")
    assert mapping is not None
    assert mapping["channel_id"] == "C1"


def test_ensure_cursor_hook_files_fixes_relative_paths(tmp_path: Path) -> None:
    config_path = hooks_config_path(tmp_path)
    script_path = hook_script_path(tmp_path)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("slacksor-hook-events.jsonl\n" + 'payload.get("transcript_path"', encoding="utf-8")
    stale_config = {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [{"command": "python3 ./hooks/slacksor_sync.py"}],
            "afterAgentResponse": [{"command": "python3 ./hooks/slacksor_sync.py"}],
        },
    }
    config_path.write_text(json.dumps(stale_config, indent=2) + "\n", encoding="utf-8")

    messages: list[str] = []
    ensure_cursor_hook_files(messages.append, cursor_home=tmp_path)

    updated = json.loads(config_path.read_text(encoding="utf-8"))
    expected_command = f"python3 {script_path}"
    assert updated["hooks"]["beforeSubmitPrompt"][0]["command"] == expected_command
    assert updated["hooks"]["afterAgentResponse"][0]["command"] == expected_command
    assert any("Fixed relative hook paths" in m for m in messages)


def test_ensure_cursor_hook_files_leaves_absolute_paths_alone(tmp_path: Path) -> None:
    config_path = hooks_config_path(tmp_path)
    script_path = hook_script_path(tmp_path)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("slacksor-hook-events.jsonl\n" + 'payload.get("transcript_path"', encoding="utf-8")
    correct_config = {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [{"command": f"python3 {script_path}"}],
            "afterAgentResponse": [{"command": f"python3 {script_path}"}],
        },
    }
    config_path.write_text(json.dumps(correct_config, indent=2) + "\n", encoding="utf-8")

    messages: list[str] = []
    ensure_cursor_hook_files(messages.append, cursor_home=tmp_path)

    assert not any("Fixed" in m for m in messages)


def test_hook_event_file_path_constant_under_cursor_dir() -> None:
    assert hook_events_path(Path("/tmp/.cursor-test")).name == "slacksor-hook-events.jsonl"
