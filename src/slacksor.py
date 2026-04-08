from __future__ import annotations

import argparse
from collections import deque
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from slack_bolt import App as SlackBoltApp
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from config import AppConfig, load_config
from cursor_hooks_sync import CursorHookEventWatcher, ensure_cursor_hook_files
from cursor_agent import CursorAgentClient, CursorBinaryNotFoundError, cursor_cli_binary_from_env
from db import Database
from keep_awake import create_inhibitor
from bridge_commands import (
    SUPPORTED_MODELS,
    bridge_help_text,
    model_help_text,
    validate_or_normalize_model,
)
from session_manager import SessionManager
from slack_handlers import SlackClientAdapter, SlackEventRouter
from transcript_watcher import TranscriptWatcher
from tui.app import run as run_tui_app
from tui.screens.dashboard import DashboardController, Project, Session


class RuntimeController(DashboardController):
    def __init__(
        self,
        db: Database,
        slack_client: SlackClientAdapter,
        sessions: SessionManager,
        cursor_client: CursorAgentClient,
        logger: logging.Logger,
    ) -> None:
        self._db = db
        self._slack = slack_client
        self._sessions = sessions
        self._cursor_client = cursor_client
        self._logger = logger
        self._runtime_logs: deque[str] = deque()
        self._runtime_logs_lock = threading.Lock()
        self._model_cache_ttl_seconds = 24 * 60 * 60 # 1 day

    def get_projects(self) -> list[Project]:
        projects: list[Project] = []
        for row in self._db.list_projects():
            projects.append(
                Project(
                    id=str(row["workspace_path"]),
                    path=str(row["workspace_path"]),
                    channel=f"#{row['channel_name']}",
                    model_override=(str(row["default_model_override"]) if row["default_model_override"] else None),
                )
            )
        return projects

    def get_sessions(self) -> list[Session]:
        sessions: list[Session] = []
        for row in self._db.list_running_sessions():
            sessions.append(
                Session(
                    id=str(row["id"]),
                    project_id=str(row["workspace_path"]),
                    status=str(row["status"]),
                )
            )
        return sessions

    def add_project(self, path: str, channel: str, model_override: str | None) -> None:
        workspace = str(Path(path).expanduser().resolve())
        channel_name = channel.strip().lstrip("#")
        if not channel_name:
            channel_name = Path(workspace).name.lower()
        channel_id = self._slack.ensure_channel(channel_name)
        self._db.add_project(
            workspace_path=workspace,
            channel_name=channel_name,
            channel_id=channel_id,
            default_model_override=model_override,
        )
        self._logger.info("Added project %s -> #%s", workspace, channel_name)

    def edit_project(self, project_id: str, path: str, channel: str, model_override: str | None) -> None:
        original_workspace = project_id
        updated_workspace = str(Path(path).expanduser().resolve())
        channel_name = channel.strip().lstrip("#") or Path(updated_workspace).name.lower()
        channel_id = self._slack.ensure_channel(channel_name)
        if original_workspace != updated_workspace:
            self._db.remove_project(original_workspace)
        self._db.add_project(
            workspace_path=updated_workspace,
            channel_name=channel_name,
            channel_id=channel_id,
            default_model_override=model_override,
        )
        self._logger.info("Edited project %s -> %s", original_workspace, updated_workspace)

    def delete_project(self, project_id: str) -> None:
        self._db.remove_project(project_id)
        self._logger.info("Deleted project %s", project_id)

    def kill_session(self, session_id: str) -> None:
        session_rows = self._db.list_sessions()
        target = next((row for row in session_rows if str(row["id"]) == session_id), None)
        if target is None:
            return
        self._sessions.stop_workspace_session(str(target["workspace_path"]))

    def append_log(self, message: str) -> None:
        self._logger.info("%s", message)

    def get_default_model(self) -> str:
        return self._db.get_default_model()

    def set_default_model(self, model: str) -> None:
        resolved = validate_or_normalize_model(model)
        self._db.set_default_model(resolved)
        self._logger.info("Default model changed to %s", resolved)

    def list_model_options(self) -> list[str]:
        cached = self._db.get_model_options_cache()
        if cached:
            return cached
        try:
            discovered = self._cursor_client.list_models()
            if discovered:
                self._db.set_model_options_cache(discovered, ttl_seconds=self._model_cache_ttl_seconds)
                return list(discovered)
        except Exception as exc:
            self._logger.warning("Model list refresh failed: %s", exc)
        stale = self._db.get_model_options_cache(include_expired=True)
        if stale:
            return stale
        return list(SUPPORTED_MODELS)

    def stop_workspace(self, workspace_path: str) -> bool:
        return self._sessions.stop_workspace_session(workspace_path)

    def get_project_paths(self) -> list[str]:
        return [str(row["workspace_path"]) for row in self._db.list_projects()]

    def get_all_sessions(self, workspace_path: str | None = None) -> list[dict[str, Any]]:
        if workspace_path is not None:
            return self._db.list_sessions_for_project(workspace_path)
        return self._db.list_sessions()

    def clear_sessions(self, workspace_path: str | None = None) -> int:
        self._sessions.stop_all_sessions()
        return self._db.clear_sessions(workspace_path)

    def clear_all(self) -> None:
        self._sessions.stop_all_sessions()
        self._db.clear_all()
        self._logger.info("Cleared entire database")

    def push_runtime_log(self, message: str) -> None:
        with self._runtime_logs_lock:
            self._runtime_logs.append(message)

    def drain_runtime_logs(self) -> list[str]:
        with self._runtime_logs_lock:
            values = list(self._runtime_logs)
            self._runtime_logs.clear()
        return values


class SlacksorRuntime:
    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._ui_log_sink: Callable[[str], None] | None = None
        self.db = Database(config.db_path)
        self.web = WebClient(token=config.slack_bot_token)
        self.slack_adapter = SlackClientAdapter(self.web, logger=self._emit_log)
        self.cursor = CursorAgentClient(binary=cursor_cli_binary_from_env())
        self.sessions = SessionManager(
            db=self.db,
            cursor_client=self.cursor,
            slack=self.slack_adapter,
            config=config,
            logger=self._emit_log,
        )
        self.router = SlackEventRouter(
            db=self.db,
            sessions=self.sessions,
            slack_client=self.slack_adapter,
            logger=self._emit_log,
            model_options_provider=self.cursor.list_models,
        )
        self.watcher = TranscriptWatcher(
            db=self.db,
            web_client=self.web,
            logger=self._emit_log,
            only_session_backed=config.enable_cursor_hooks_sync,
        )
        self.hook_watcher = CursorHookEventWatcher(
            db=self.db,
            web_client=self.web,
            logger=self._emit_log,
        )
        self._stop_event = threading.Event()
        self._socket_thread: threading.Thread | None = None
        self._socket_handler: SocketModeHandler | None = None
        self._sleep_inhibitor = create_inhibitor(logger=self._emit_log)

    def set_ui_log_sink(self, sink: Callable[[str], None] | None) -> None:
        self._ui_log_sink = sink

    def _emit_log(self, message: str) -> None:
        self.logger.info("%s", message)
        if self._ui_log_sink is not None:
            self._ui_log_sink(message)

    def start_listener(self) -> None:
        app = SlackBoltApp(token=self.config.slack_bot_token)

        @app.event("message")
        def _on_message(event: dict[str, Any], say: Any) -> None:
            del say
            self.router.handle_message_event(event)

        handler = SocketModeHandler(app, self.config.slack_app_token)
        self._socket_handler = handler

        def _run_socket() -> None:
            handler.start()

        self._socket_thread = threading.Thread(target=_run_socket, daemon=True)
        self._socket_thread.start()
        self.slack_adapter.set_presence("auto")
        self._sleep_inhibitor.activate()
        self._emit_log("Slack Socket Mode listener started")

    def maybe_start_transcript_mirror(self) -> None:
        if not self.config.enable_ide_transcript_mirror:
            self._emit_log("IDE transcript mirroring disabled")
            return
        if self.config.enable_cursor_hooks_sync:
            self._emit_log("IDE transcript mirroring skipped while Cursor hooks sync is enabled")
            return
        self.watcher.start()
        self._emit_log("IDE transcript mirroring enabled")

    def maybe_start_cursor_hooks_sync(self) -> None:
        if not self.config.enable_cursor_hooks_sync:
            self._emit_log("Cursor hooks sync disabled")
            return
        self.hook_watcher.start()
        self._emit_log("Cursor hooks sync enabled")

    def stop(self) -> None:
        self._stop_event.set()
        self._sleep_inhibitor.deactivate()
        self.slack_adapter.set_presence("away")
        self.sessions.shutdown()
        self.watcher.stop()
        self.hook_watcher.stop()
        if self._socket_handler is not None and hasattr(self._socket_handler, "close"):
            self._socket_handler.close()
        self.db.close()

    def ensure_cursor_auth(self) -> None:
        self._emit_log("Checking Cursor Agent authentication...")
        try:
            ok = self.cursor.check_auth()
        except CursorBinaryNotFoundError as exc:
            self._emit_log(str(exc))
            raise SystemExit(1) from exc
        if ok:
            self._emit_log("Cursor Agent authenticated")
            return
        self._emit_log(
            "Cursor Agent is NOT authenticated. "
            "Run 'cursor agent' in a terminal to log in, then restart slacksor."
        )
        raise SystemExit(1)

    def serve_forever(self) -> None:
        self.ensure_cursor_auth()
        self.sessions.recover_orphans()
        self.start_listener()
        self.maybe_start_cursor_hooks_sync()
        self.maybe_start_transcript_mirror()
        self._emit_log("slacksor serving in headless mode")
        try:
            while not self._stop_event.is_set():
                time.sleep(self.config.polling_interval_seconds)
        except KeyboardInterrupt:
            self._emit_log("Shutdown requested")
        finally:
            self.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slacksor")
    subparsers = parser.add_subparsers(dest="command")

    add_parser = subparsers.add_parser("add-project")
    add_parser.add_argument("workspace_path")
    add_parser.add_argument("--channel", default="")
    add_parser.add_argument("--model-override", default="")

    remove_parser = subparsers.add_parser("remove-project")
    remove_parser.add_argument("workspace_path")

    subparsers.add_parser("list-projects")
    subparsers.add_parser("serve")
    subparsers.add_parser("help")
    model_parser = subparsers.add_parser("model")
    model_parser.add_argument("model_name", nargs="?")
    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--workspace", default="")
    subparsers.add_parser("exit")
    clear_db_parser = subparsers.add_parser("clear-db")
    clear_db_parser.add_argument(
        "--all", action="store_true", dest="clear_everything",
        help="Clear everything including projects and settings",
    )
    clear_db_parser.add_argument("--workspace", default="")
    return parser


def _configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)
    return logging.getLogger("slacksor")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    logger = _configure_logging()
    ensure_cursor_hook_files(lambda message: logger.info("%s", message))
    config = load_config()
    runtime = SlacksorRuntime(config=config, logger=logger)
    controller = RuntimeController(
        db=runtime.db,
        slack_client=runtime.slack_adapter,
        sessions=runtime.sessions,
        cursor_client=runtime.cursor,
        logger=logger,
    )

    if args.command == "add-project":
        channel = args.channel or Path(args.workspace_path).name.lower()
        override = args.model_override.strip() or None
        controller.add_project(args.workspace_path, channel, override)
        return
    if args.command == "remove-project":
        workspace = str(Path(args.workspace_path).expanduser().resolve())
        controller.delete_project(workspace)
        return
    if args.command == "list-projects":
        for project in controller.get_projects():
            override = project.model_override or "none"
            print(f"{project.path} -> {project.channel} (override: {override})")
        return
    if args.command == "help":
        print(bridge_help_text(controller.get_default_model()))
        return
    if args.command == "model":
        if args.model_name:
            controller.set_default_model(args.model_name)
            print(f"Default model set to {controller.get_default_model()}")
        else:
            print(model_help_text(controller.get_default_model(), controller.list_model_options()))
        return
    if args.command == "stop":
        if args.workspace:
            workspace = str(Path(args.workspace).expanduser().resolve())
            stopped = controller.stop_workspace(workspace)
            if stopped:
                print(f"Stopped active session for {workspace}")
            else:
                print(f"No active session for {workspace}")
            return
        stopped_count = runtime.sessions.stop_all_sessions()
        print(f"Stopped {stopped_count} active session(s).")
        return
    if args.command == "exit":
        stopped_count = runtime.sessions.stop_all_sessions()
        print(f"Stopped {stopped_count} active session(s).")
        return
    if args.command == "clear-db":
        if args.clear_everything:
            confirm = input('This will delete ALL data. Type "DELETE" to confirm: ')
            if confirm.strip() != "DELETE":
                print("Aborted.")
                return
            controller.clear_all()
            print("Cleared all database data.")
        else:
            workspace = ""
            if hasattr(args, "workspace") and args.workspace:
                workspace = str(Path(args.workspace).expanduser().resolve())
            ws = workspace or None
            count = controller.clear_sessions(ws)
            scope = f" for {workspace}" if ws else ""
            print(f"Cleared {count} session(s){scope}.")
        return
    if args.command == "serve":
        runtime.serve_forever()
        return

    runtime.ensure_cursor_auth()
    runtime.sessions.recover_orphans()
    runtime.set_ui_log_sink(controller.push_runtime_log)
    runtime.start_listener()
    runtime.maybe_start_cursor_hooks_sync()
    runtime.maybe_start_transcript_mirror()
    run_tui_app(controller=controller, on_shutdown=runtime.stop)


if __name__ == "__main__":
    main()
