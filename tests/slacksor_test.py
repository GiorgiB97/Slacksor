from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import AppConfig
from db import Database
import slacksor
from slacksor import RuntimeController, SlacksorRuntime, _build_parser, main


class FakeSlackAdapter:
    def __init__(self) -> None:
        self.channels: dict[str, str] = {}
        self.presence_calls: list[str] = []

    def ensure_channel(self, channel_name: str) -> str:
        channel_id = f"C_{channel_name}"
        self.channels[channel_name] = channel_id
        return channel_id

    def set_presence(self, status: str) -> None:
        self.presence_calls.append(status)


class FakeSessions:
    def __init__(self) -> None:
        self.killed: list[str] = []

    def kill_active_for_workspace(self, workspace_path: str) -> None:
        self.killed.append(workspace_path)

    def stop_workspace_session(self, workspace_path: str) -> bool:
        self.killed.append(workspace_path)
        return True


class FakeCursorClient:
    def __init__(self) -> None:
        self.models = ["auto", "gpt-5.3-codex"]
        self.calls = 0

    def list_models(self) -> list[str]:
        self.calls += 1
        return list(self.models)


def test_build_parser_accepts_commands() -> None:
    parser = _build_parser()
    parsed = parser.parse_args(["add-project", "/tmp/a", "--channel", "proj-a"])
    assert parsed.command == "add-project"
    assert parsed.workspace_path == "/tmp/a"
    assert parsed.channel == "proj-a"
    parsed_model = parser.parse_args(["model", "gpt-5"])
    assert parsed_model.command == "model"
    assert parsed_model.model_name == "gpt-5"


def test_runtime_controller_project_crud(database: Database) -> None:
    slack = FakeSlackAdapter()
    sessions = FakeSessions()
    cursor = FakeCursorClient()
    controller = RuntimeController(
        db=database,
        slack_client=slack,  # type: ignore[arg-type]
        sessions=sessions,  # type: ignore[arg-type]
        cursor_client=cursor,  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )
    controller.add_project("/tmp/a", "my-channel", None)
    projects = controller.get_projects()
    assert len(projects) == 1
    assert projects[0].channel == "#my-channel"
    controller.edit_project(projects[0].id, "/tmp/a", "my-channel-2", "gpt-5")
    projects_after = controller.get_projects()
    assert projects_after[0].channel == "#my-channel-2"
    assert controller.list_model_options() == ["auto", "gpt-5.3-codex"]
    assert cursor.calls == 1
    assert controller.list_model_options() == ["auto", "gpt-5.3-codex"]
    assert cursor.calls == 1
    controller.delete_project(projects_after[0].id)
    assert controller.get_projects() == []


def test_runtime_start_listener_and_stop(tmp_path: Path, monkeypatch) -> None:
    class FakeWebClient:
        def __init__(self, token: str) -> None:
            self.token = token
            self.presence_calls: list[str] = []

        def users_setPresence(self, presence: str) -> None:
            self.presence_calls.append(presence)

    class FakeBoltApp:
        def __init__(self, token: str) -> None:
            self.token = token
            self.handlers: dict[str, Any] = {}

        def event(self, name: str):
            def _decorator(func):
                self.handlers[name] = func
                return func

            return _decorator

    class FakeSocketHandler:
        def __init__(self, app: FakeBoltApp, app_token: str) -> None:
            self.app = app
            self.app_token = app_token
            self.started = False

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(slacksor, "WebClient", FakeWebClient)
    monkeypatch.setattr(slacksor, "SlackBoltApp", FakeBoltApp)
    monkeypatch.setattr(slacksor, "SocketModeHandler", FakeSocketHandler)
    config = AppConfig(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        db_path=tmp_path / "slacksor.db",
        session_timeout_seconds=5,
        keepalive_seconds=1,
        post_chunk_size=100,
        polling_interval_seconds=0.01,
        enable_ide_transcript_mirror=False,
        enable_cursor_hooks_sync=False,
    )
    runtime = SlacksorRuntime(config=config, logger=logging.getLogger("test"))
    runtime.start_listener()
    assert runtime.web.presence_calls == ["auto"]
    runtime.stop()
    assert runtime.web.presence_calls == ["auto", "away"]


def test_main_command_routes(monkeypatch, tmp_path: Path) -> None:
    config = AppConfig(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        db_path=tmp_path / "slacksor.db",
        session_timeout_seconds=5,
        keepalive_seconds=1,
        post_chunk_size=100,
        polling_interval_seconds=0.01,
        enable_ide_transcript_mirror=False,
        enable_cursor_hooks_sync=False,
    )

    class FakeWatcher:
        def __init__(self) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

    class FakeRuntime:
        latest = None

        def __init__(self, config: AppConfig, logger) -> None:
            del config, logger
            self.db = object()
            self.slack_adapter = object()
            self.cursor = FakeCursorClient()
            self.sessions = type(
                "Sessions",
                (),
                {
                    "recover_orphans": lambda self: None,
                    "stop_all_sessions": lambda self: 0,
                    "stop_workspace_session": lambda self, workspace_path: True,
                },
            )()
            self.watcher = FakeWatcher()
            self.started = False
            self.served = False
            self.stopped = False
            FakeRuntime.latest = self

        def start_listener(self) -> None:
            self.started = True

        def set_ui_log_sink(self, sink) -> None:
            self.sink = sink

        def serve_forever(self) -> None:
            self.served = True

        def maybe_start_transcript_mirror(self) -> None:
            self.watcher.start()

        def maybe_start_cursor_hooks_sync(self) -> None:
            return

        def stop(self) -> None:
            self.stopped = True

    class FakeController:
        latest = None

        def __init__(self, db, slack_client, sessions, cursor_client, logger) -> None:
            del db, slack_client, sessions, cursor_client, logger
            self.added = None
            self.deleted = None
            self.logs: list[str] = []
            FakeController.latest = self

        def add_project(self, workspace_path: str, channel: str, model_override: str | None) -> None:
            self.added = (workspace_path, channel, model_override)

        def delete_project(self, workspace_path: str) -> None:
            self.deleted = workspace_path

        def get_projects(self):
            return [type("P", (), {"path": "/tmp/a", "channel": "#a", "model_override": None})()]

        def get_default_model(self):
            return "auto"

        def set_default_model(self, model: str) -> None:
            self.model = model

        def stop_workspace(self, workspace_path: str) -> bool:
            del workspace_path
            return True

        def push_runtime_log(self, message: str) -> None:
            self.logs.append(message)

    ran_tui = {"value": False}

    def fake_run_tui_app(controller, on_shutdown) -> None:
        del controller
        ran_tui["value"] = True
        on_shutdown()

    monkeypatch.setattr(slacksor, "load_config", lambda: config)
    monkeypatch.setattr(slacksor, "ensure_cursor_hook_files", lambda logger, cursor_home=None: None)
    monkeypatch.setattr(slacksor, "SlacksorRuntime", FakeRuntime)
    monkeypatch.setattr(slacksor, "RuntimeController", FakeController)
    monkeypatch.setattr(slacksor, "run_tui_app", fake_run_tui_app)

    monkeypatch.setattr("sys.argv", ["slacksor.py", "add-project", "/tmp/a", "--channel", "chan"])
    main()
    assert FakeController.latest.added == ("/tmp/a", "chan", None)

    monkeypatch.setattr("sys.argv", ["slacksor.py", "remove-project", "/tmp/a"])
    main()
    assert FakeController.latest.deleted == str(Path("/tmp/a").resolve())

    monkeypatch.setattr("sys.argv", ["slacksor.py", "list-projects"])
    main()

    monkeypatch.setattr("sys.argv", ["slacksor.py", "serve"])
    main()
    assert FakeRuntime.latest.served is True

    monkeypatch.setattr("sys.argv", ["slacksor.py"])
    main()
    assert ran_tui["value"] is True
