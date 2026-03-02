"""Dashboard screen and controller protocol for Slacksor TUI."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from tui.screens.add_project import AddProjectScreen, ProjectFormResult
from tui.screens.confirm import ConfirmScreen
from tui.screens.model_select import ModelSelectScreen, ModelSelectionResult
from tui.screens.sessions import SessionExplorerScreen
from tui.screens.type_confirm import TypeConfirmScreen

if TYPE_CHECKING:
    from textual.app import ComposeResult


@dataclass
class Project:
    """Project model."""

    id: str
    path: str
    channel: str
    model_override: str | None


@dataclass
class Session:
    """Active session model."""

    id: str
    project_id: str
    status: str


@runtime_checkable
class DashboardController(Protocol):
    """Abstract protocol for dashboard controller callbacks."""

    def get_projects(self) -> list[Project]:
        """Return list of projects."""
        ...

    def get_sessions(self) -> list[Session]:
        """Return list of active sessions."""
        ...

    def add_project(self, path: str, channel: str, model_override: str | None) -> None:
        """Add a new project."""
        ...

    def edit_project(self, project_id: str, path: str, channel: str, model_override: str | None) -> None:
        """Edit an existing project."""
        ...

    def delete_project(self, project_id: str) -> None:
        """Delete a project."""
        ...

    def kill_session(self, session_id: str) -> None:
        """Kill an active session."""
        ...

    def append_log(self, message: str) -> None:
        """Append a message to the log panel."""
        ...

    def get_default_model(self) -> str:
        """Return current default model."""
        ...

    def set_default_model(self, model: str) -> None:
        """Set default model."""
        ...

    def list_model_options(self) -> list[str]:
        """Return known model options."""
        ...

    def stop_workspace(self, workspace_path: str) -> bool:
        """Stop active session for workspace."""
        ...

    def drain_runtime_logs(self) -> list[str]:
        """Drain runtime logs collected from worker threads."""
        ...

    def get_project_paths(self) -> list[str]:
        ...

    def get_all_sessions(self, workspace_path: str | None = None) -> list[dict[str, Any]]:
        ...

    def clear_sessions(self, workspace_path: str | None = None) -> int:
        ...

    def clear_all(self) -> None:
        ...


class StubController:
    """Stub controller implementing DashboardController for development."""

    def get_projects(self) -> list[Project]:
        return [
            Project(id="1", path="/tmp/proj1", channel="#dev", model_override=None),
            Project(id="2", path="/tmp/proj2", channel="#ops", model_override="gpt-5"),
        ]

    def get_sessions(self) -> list[Session]:
        return [
            Session(id="s1", project_id="1", status="running"),
        ]

    def add_project(self, path: str, channel: str, model_override: str | None) -> None:
        del path, channel, model_override
        pass

    def edit_project(self, project_id: str, path: str, channel: str, model_override: str | None) -> None:
        del project_id, path, channel, model_override
        pass

    def delete_project(self, project_id: str) -> None:
        pass

    def kill_session(self, session_id: str) -> None:
        pass

    def append_log(self, message: str) -> None:
        pass

    def get_default_model(self) -> str:
        return "auto"

    def set_default_model(self, model: str) -> None:
        del model

    def list_model_options(self) -> list[str]:
        return ["auto", "gpt-5", "claude-sonnet-4.5", "gemini-2.5-pro"]

    def stop_workspace(self, workspace_path: str) -> bool:
        del workspace_path
        return True

    def drain_runtime_logs(self) -> list[str]:
        return []

    def get_project_paths(self) -> list[str]:
        return ["/tmp/proj1", "/tmp/proj2"]

    def get_all_sessions(self, workspace_path: str | None = None) -> list[dict[str, Any]]:
        return []

    def clear_sessions(self, workspace_path: str | None = None) -> int:
        return 0

    def clear_all(self) -> None:
        pass


class DashboardScreen(Screen):
    """Main dashboard screen with projects, sessions, and log panel."""

    BINDINGS = [
        ("a", "add_project", "Add"),
        ("e", "edit_project", "Edit"),
        ("d", "delete_project", "Delete"),
        ("k", "kill_session", "Kill"),
        ("s", "stop", "Stop"),
        ("m", "model", "Model"),
        ("c", "chat_sessions", "Chats"),
        ("x", "clear_db", "Clear DB"),
        ("h", "help", "Help"),
        ("q", "quit", "Quit"),
        ("tab", "focus_next", "Next"),
    ]

    def __init__(
        self,
        controller: DashboardController | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._controller: DashboardController = controller or StubController()
        self._project_row_keys: list[str] = []
        self._session_row_keys: list[str] = []

    def compose(self) -> "ComposeResult":
        yield Header()
        with Horizontal():
            with Vertical(id="left-panel"):
                yield Static("Projects", classes="panel-title")
                yield DataTable(id="projects-table", cursor_type="row")
                yield Static("Sessions", classes="panel-title")
                yield DataTable(id="sessions-table", cursor_type="row")
            with Vertical(id="log-panel"):
                yield Static("Log", classes="panel-title")
                yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_projects()
        self._refresh_sessions()
        self._log("Slacksor TUI started.")
        self.set_interval(0.5, self._poll_runtime_updates)

    def _log(self, message: str) -> None:
        self._write_log(message)
        self._controller.append_log(message)

    def _write_log(self, message: str) -> None:
        log_widget = self.query_one("#log", RichLog)
        log_widget.write(message)

    def _poll_runtime_updates(self) -> None:
        self._refresh_sessions()
        for entry in self._controller.drain_runtime_logs():
            self._write_log(entry)

    def _refresh_projects(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Path", "Channel", "Model Override")
        self._project_row_keys = []
        for p in self._controller.get_projects():
            table.add_row(p.path, p.channel, p.model_override or "none", key=p.id)
            self._project_row_keys.append(p.id)

    def _refresh_sessions(self) -> None:
        table = self.query_one("#sessions-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Project", "Status")
        self._session_row_keys = []
        projects = {p.id: p.path for p in self._controller.get_projects()}
        for s in self._controller.get_sessions():
            proj_path = projects.get(s.project_id, s.project_id)
            table.add_row(proj_path, s.status, key=s.id)
            self._session_row_keys.append(s.id)

    def action_add_project(self) -> None:
        self.app.push_screen(
            AddProjectScreen(model_options=self._controller.list_model_options()),
            self._on_project_submitted,
        )

    def action_edit_project(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index >= len(self._project_row_keys):
            return
        row_key = self._project_row_keys[row_index]
        projects = self._controller.get_projects()
        proj = next((p for p in projects if p.id == row_key), None)
        if proj is None:
            return
        self.app.push_screen(
            AddProjectScreen(
                path=proj.path,
                channel=proj.channel,
                model_override=proj.model_override,
                model_options=self._controller.list_model_options(),
                project_id=proj.id,
            ),
            self._on_project_submitted,
        )

    def action_delete_project(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        row_index = table.cursor_row
        if row_index is not None and row_index < len(self._project_row_keys):
            row_key = self._project_row_keys[row_index]
            projects = self._controller.get_projects()
            proj = next((p for p in projects if p.id == row_key), None)
            label = proj.path if proj else row_key
            self.app.push_screen(
                ConfirmScreen(message=f"Delete project '{label}'?"),
                lambda confirmed: self._on_delete_confirmed(confirmed, row_key),
            )

    def action_kill_session(self) -> None:
        table = self.query_one("#sessions-table", DataTable)
        row_index = table.cursor_row
        if row_index is not None and row_index < len(self._session_row_keys):
            row_key = self._session_row_keys[row_index]
            self.app.push_screen(
                ConfirmScreen(message=f"Kill session '{row_key}'?"),
                lambda confirmed: self._on_kill_confirmed(confirmed, row_key),
            )

    def _on_delete_confirmed(self, confirmed: bool, project_id: str) -> None:
        if confirmed:
            self._controller.delete_project(project_id)
            self._refresh_projects()
            self._refresh_sessions()
            self._log(f"Deleted project {project_id}")

    def _on_kill_confirmed(self, confirmed: bool, session_id: str) -> None:
        if confirmed:
            self._controller.kill_session(session_id)
            self._refresh_sessions()
            self._log(f"Killed session {session_id}")

    def action_model(self) -> None:
        options = self._controller.list_model_options()
        current = self._controller.get_default_model()
        if not options:
            self._log("No model options configured.")
            return
        self.app.push_screen(
            ModelSelectScreen(options=options, current_model=current, title="Select Default Model"),
            self._on_model_selected,
        )

    def action_chat_sessions(self) -> None:
        self.app.push_screen(SessionExplorerScreen(controller=self._controller))

    def action_clear_db(self) -> None:
        self.app.push_screen(
            TypeConfirmScreen(
                message="This will permanently delete ALL sessions, transcripts, and hook data.\nProjects and settings will be kept.",
                confirm_word="DELETE",
            ),
            self._on_clear_db_confirmed,
        )

    def _on_clear_db_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            count = self._controller.clear_sessions()
            self._refresh_projects()
            self._refresh_sessions()
            self._log(f"Cleared {count} session(s) from database.")

    def action_help(self) -> None:
        self._log("Commands: help, model, stop, exit, chats, clear-db.")

    def action_stop(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index >= len(self._project_row_keys):
            self._log("Select a project first.")
            return
        workspace_path = self._project_row_keys[row_index]
        stopped = self._controller.stop_workspace(workspace_path)
        if stopped:
            self._refresh_sessions()
            self._log(f"Stopped active session for {workspace_path}")
        else:
            self._log(f"No active session for {workspace_path}")

    def action_quit(self) -> None:
        self.app.exit()

    def _on_project_submitted(
        self,
        result: ProjectFormResult | None,
    ) -> None:
        if result is None:
            return
        if result.project_id is None:
            self._controller.add_project(result.path, result.channel, result.model_override)
            self._log(f"Added project: {result.path} -> {result.channel}")
        else:
            self._controller.edit_project(
                result.project_id,
                result.path,
                result.channel,
                result.model_override,
            )
            self._log(
                f"Edited project {result.project_id}: {result.path} -> {result.channel}"
            )
        self._refresh_projects()

    def _on_model_selected(self, result: ModelSelectionResult | None) -> None:
        if result is None:
            return
        self._controller.set_default_model(result.model)
        self._log(f"Default model set to {result.model}")
