from __future__ import annotations

from pathlib import Path

from db import Database
import transcript_watcher
from transcript_watcher import (
    TranscriptWatcher,
    _extract_text,
    _safe_parse_json,
    encode_workspace_path,
)


class FakeWebClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def chat_postMessage(self, channel: str, text: str, thread_ts: str | None = None) -> dict:
        ts = f"{len(self.posts) + 1}.000"
        payload = {"channel": channel, "text": text, "thread_ts": thread_ts, "ts": ts}
        self.posts.append(payload)
        return {"ts": ts}


def test_encode_workspace_path() -> None:
    encoded = encode_workspace_path("/Users/test/my-proj")
    assert encoded == "Users-test-my-proj"


def test_process_ide_transcript(database: Database, tmp_path: Path) -> None:
    workspace = str((tmp_path / "proj").resolve())
    encoded = encode_workspace_path(workspace)
    transcript_dir = tmp_path / ".cursor" / "projects" / encoded / "agent-transcripts" / "chat-1"
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "chat-1.jsonl"
    transcript_file.write_text(
        "\n".join(
            [
                '{"role":"user","message":{"content":[{"type":"text","text":"hello"}]}}',
                '{"role":"assistant","message":{"content":[{"type":"text","text":"world"}]}}',
            ]
        ),
        encoding="utf-8",
    )

    database.add_project(workspace, "proj", "C1")
    web = FakeWebClient()
    watcher = TranscriptWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_projects_root=tmp_path / ".cursor" / "projects",
    )
    watcher._process_transcript(transcript_file)
    assert len(web.posts) == 2
    assert web.posts[0]["thread_ts"] is None
    assert web.posts[1]["thread_ts"] == "1.000"


def test_skip_flat_cli_transcript(database: Database, tmp_path: Path) -> None:
    workspace = str((tmp_path / "proj").resolve())
    encoded = encode_workspace_path(workspace)
    transcript_dir = tmp_path / ".cursor" / "projects" / encoded / "agent-transcripts"
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "chat-1.jsonl"
    transcript_file.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"hello"}]}}',
        encoding="utf-8",
    )
    database.add_project(workspace, "proj", "C1")
    web = FakeWebClient()
    watcher = TranscriptWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_projects_root=tmp_path / ".cursor" / "projects",
    )
    watcher._process_transcript(transcript_file)
    assert web.posts == []


def test_process_flat_cli_transcript_for_mapped_session(database: Database, tmp_path: Path) -> None:
    workspace = str((tmp_path / "proj").resolve())
    encoded = encode_workspace_path(workspace)
    transcript_dir = tmp_path / ".cursor" / "projects" / encoded / "agent-transcripts"
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "chat-1.jsonl"
    transcript_file.write_text(
        "\n".join(
            [
                '{"role":"user","message":{"content":[{"type":"text","text":"hello from slack"}]}}',
                '{"role":"assistant","message":{"content":[{"type":"text","text":"answer from cursor"}]}}',
            ]
        ),
        encoding="utf-8",
    )
    database.add_project(workspace, "proj", "C1")
    database.get_or_create_session(workspace, "C1", "100.1", "chat-1")
    web = FakeWebClient()
    watcher = TranscriptWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_projects_root=tmp_path / ".cursor" / "projects",
    )
    watcher._process_transcript(transcript_file)
    assert len(web.posts) == 1
    assert web.posts[0]["text"] == "answer from cursor"
    assert web.posts[0]["thread_ts"] == "100.1"


def test_transcript_helpers(database: Database, tmp_path: Path) -> None:
    assert _safe_parse_json("not-json") is None
    assert _safe_parse_json('{"a":1}') == {"a": 1}
    assert _extract_text({"message": {"content": "bad"}}) == ""
    text = _extract_text(
        {"message": {"content": [{"type": "text", "text": "hello"}, {"type": "x", "text": "y"}]}}
    )
    assert text == "hello"

    web = FakeWebClient()
    watcher = TranscriptWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_projects_root=tmp_path / "missing-root",
    )
    watcher.start()
    watcher.stop()


def test_scan_and_run_loop(database: Database, tmp_path: Path, monkeypatch) -> None:
    workspace = str((tmp_path / "proj").resolve())
    encoded = encode_workspace_path(workspace)
    transcript_dir = tmp_path / ".cursor" / "projects" / encoded / "agent-transcripts" / "chat-2"
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "chat-2.jsonl"
    transcript_file.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"first"}]}}',
        encoding="utf-8",
    )
    database.add_project(workspace, "proj", "C1")
    web = FakeWebClient()
    watcher = TranscriptWatcher(
        db=database,
        web_client=web,  # type: ignore[arg-type]
        logger=lambda _: None,
        cursor_projects_root=tmp_path / ".cursor" / "projects",
    )

    watcher._scan_existing_files()
    assert web.posts

    calls = {"count": 0}

    def fake_watch(*args, **kwargs):
        del args, kwargs
        if calls["count"] == 0:
            calls["count"] += 1
            yield {(transcript_watcher.Change.modified, str(transcript_file))}
        else:
            watcher._stop_event.set()  # noqa: SLF001
            yield set()

    monkeypatch.setattr(transcript_watcher, "watch", fake_watch)
    watcher._run()
    assert calls["count"] == 1
