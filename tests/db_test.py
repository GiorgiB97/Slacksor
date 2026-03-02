from __future__ import annotations

import time

from db import Database


def test_add_and_get_project(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1", default_model_override="gpt-5")
    row = database.get_project_by_channel_id("C1")
    assert row is not None
    assert row["workspace_path"] == "/tmp/a"
    assert row["channel_name"] == "proj-a"
    assert row["default_model_override"] == "gpt-5"
    row_mixed_case = database.get_project_by_workspace("/TMP/A")
    assert row_mixed_case is not None
    assert row_mixed_case["workspace_path"] == "/tmp/a"


def test_session_lifecycle(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    row = database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    database.set_session_running(int(row["id"]), workspace_path="/tmp/a", pid=1234)
    active = database.get_active_session_for_workspace("/tmp/a")
    assert active is not None
    assert active["pid"] == 1234
    database.mark_session_status(int(row["id"]), "completed")
    active_after = database.get_active_session_for_workspace("/tmp/a")
    assert active_after is None


def test_transcript_state_upsert(database: Database) -> None:
    database.upsert_transcript_state("f.jsonl", "/tmp/a", "C1", "1.2", 3)
    state = database.get_transcript_state("f.jsonl")
    assert state is not None
    assert state["last_line_read"] == 3
    database.upsert_transcript_state("f.jsonl", "/tmp/a", "C1", "1.2", 10)
    updated = database.get_transcript_state("f.jsonl")
    assert updated is not None
    assert updated["last_line_read"] == 10


def test_remove_project_cascades_sessions(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    database.remove_project("/tmp/a")
    assert database.get_project_by_workspace("/tmp/a") is None
    assert database.list_sessions() == []


def test_model_options_cache_with_expiration(database: Database) -> None:
    now_ts = time.time()
    database.set_model_options_cache(["auto", "gpt-5.3-codex"], ttl_seconds=60, now_ts=now_ts)
    assert database.get_model_options_cache(now_ts=now_ts + 30) == ["auto", "gpt-5.3-codex"]
    assert database.get_model_options_cache(now_ts=now_ts + 61) is None
    assert database.get_model_options_cache(include_expired=True, now_ts=now_ts + 61) == [
        "auto",
        "gpt-5.3-codex",
    ]


def test_session_lookup_by_cursor_chat_id(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    row = database.get_session_by_cursor_chat_id("/tmp/a", "chat-1")
    assert row is not None
    assert row["thread_ts"] == "111.1"


def test_hook_conversation_upsert(database: Database) -> None:
    database.upsert_hook_conversation("/tmp/a", "conv-1", "C1", "100.1", cursor_chat_id="chat-a")
    row = database.get_hook_conversation("/tmp/a", "conv-1")
    assert row is not None
    assert row["thread_ts"] == "100.1"
    assert row["cursor_chat_id"] == "chat-a"
    by_thread = database.get_hook_conversation_by_thread("/tmp/a", "C1", "100.1")
    assert by_thread is not None
    assert by_thread["conversation_id"] == "conv-1"

    database.upsert_hook_conversation("/tmp/a", "conv-1", "C1", "100.2")
    updated = database.get_hook_conversation("/tmp/a", "conv-1")
    assert updated is not None
    assert updated["thread_ts"] == "100.2"
    assert updated["cursor_chat_id"] == "chat-a"


def test_clear_sessions_all(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    database.add_project("/tmp/b", "proj-b", "C2")
    database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    database.get_or_create_session("/tmp/b", "C2", "222.1", "chat-2")
    count = database.clear_sessions()
    assert count == 2
    assert database.list_sessions() == []
    assert len(database.list_projects()) == 2


def test_clear_sessions_per_workspace(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    database.add_project("/tmp/b", "proj-b", "C2")
    database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    database.get_or_create_session("/tmp/b", "C2", "222.1", "chat-2")
    count = database.clear_sessions(workspace_path="/tmp/a")
    assert count == 1
    remaining = database.list_sessions()
    assert len(remaining) == 1
    assert remaining[0]["workspace_path"] == "/tmp/b"


def test_clear_all(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    database.upsert_transcript_state("f.jsonl", "/tmp/a", "C1", "1.2", 3)
    database.upsert_hook_conversation("/tmp/a", "conv-1", "C1", "100.1")
    database.set_default_model("gpt-5")
    database.clear_all()
    assert database.list_projects() == []
    assert database.list_sessions() == []
    assert database.get_transcript_state("f.jsonl") is None
    assert database.get_hook_conversation("/tmp/a", "conv-1") is None
    assert database.get_default_model() == "gpt-5"


def test_update_and_get_session_tokens(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    row = database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    session_id = int(row["id"])
    tokens = database.get_session_tokens(session_id)
    assert tokens == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    database.update_session_tokens(session_id, 100, 50, 150)
    tokens = database.get_session_tokens(session_id)
    assert tokens == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_get_session_tokens_missing_session(database: Database) -> None:
    tokens = database.get_session_tokens(999)
    assert tokens == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_get_workspace_token_totals(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    s1 = database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    s2 = database.get_or_create_session("/tmp/a", "C1", "111.2", "chat-2")
    database.update_session_tokens(int(s1["id"]), 100, 50, 150)
    database.update_session_tokens(int(s2["id"]), 200, 80, 280)
    totals = database.get_workspace_token_totals("/tmp/a")
    assert totals == {"prompt_tokens": 300, "completion_tokens": 130, "total_tokens": 430}


def test_get_workspace_token_totals_empty(database: Database) -> None:
    totals = database.get_workspace_token_totals("/tmp/nonexistent")
    assert totals == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_list_sessions_for_project(database: Database) -> None:
    database.add_project("/tmp/a", "proj-a", "C1")
    database.add_project("/tmp/b", "proj-b", "C2")
    database.get_or_create_session("/tmp/a", "C1", "111.1", "chat-1")
    database.get_or_create_session("/tmp/a", "C1", "111.2", "chat-2")
    database.get_or_create_session("/tmp/b", "C2", "222.1", "chat-3")
    rows = database.list_sessions_for_project("/tmp/a")
    assert len(rows) == 2
    assert all(r["workspace_path"] == "/tmp/a" for r in rows)
    rows_b = database.list_sessions_for_project("/tmp/b")
    assert len(rows_b) == 1
