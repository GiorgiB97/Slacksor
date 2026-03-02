"""Chat session explorer screen for Slacksor TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Select, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


@runtime_checkable
class SessionExplorerController(Protocol):
    def get_project_paths(self) -> list[str]:
        ...

    def get_all_sessions(self, workspace_path: str | None = None) -> list[dict[str, Any]]:
        ...


@dataclass
class SessionRow:
    id: str
    workspace_path: str
    cursor_chat_id: str
    status: str
    created_at: str
    last_active_at: str
    thread_ts: str


_ALL_PROJECTS_VALUE = "__all__"


class SessionExplorerScreen(Screen):
    """Browse chat sessions grouped by project."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("q", "go_back", "Back"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        controller: SessionExplorerController,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._controller = controller

    def compose(self) -> "ComposeResult":
        yield Header()
        with Vertical(id="session-explorer"):
            with Horizontal(id="session-filter-bar"):
                yield Static("Project: ", id="session-filter-label")
                yield Select(
                    options=[("All projects", _ALL_PROJECTS_VALUE)],
                    value=_ALL_PROJECTS_VALUE,
                    id="session-project-filter",
                )
            yield DataTable(id="session-explorer-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_project_filter()
        self._refresh_sessions()

    def _populate_project_filter(self) -> None:
        select = self.query_one("#session-project-filter", Select)
        paths = self._controller.get_project_paths()
        options: list[tuple[str, str]] = [("All projects", _ALL_PROJECTS_VALUE)]
        for p in paths:
            options.append((p, p))
        select.set_options(options)

    def _selected_workspace(self) -> str | None:
        select = self.query_one("#session-project-filter", Select)
        value = select.value
        if not isinstance(value, str) or value == _ALL_PROJECTS_VALUE:
            return None
        return value

    def _refresh_sessions(self) -> None:
        table = self.query_one("#session-explorer-table", DataTable)
        table.clear(columns=True)
        table.add_columns("ID", "Project", "Chat ID", "Status", "Created", "Last Active")
        workspace = self._selected_workspace()
        rows = self._controller.get_all_sessions(workspace_path=workspace)
        for row in rows:
            chat_id = str(row.get("cursor_chat_id", ""))
            short_chat_id = chat_id[:12] + "..." if len(chat_id) > 15 else chat_id
            project_path = str(row.get("workspace_path", ""))
            short_path = project_path.rsplit("/", 1)[-1] if "/" in project_path else project_path
            table.add_row(
                str(row.get("id", "")),
                short_path,
                short_chat_id,
                str(row.get("status", "")),
                str(row.get("created_at", ""))[:19],
                str(row.get("last_active_at", ""))[:19],
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "session-project-filter":
            self._refresh_sessions()

    def action_refresh(self) -> None:
        self._refresh_sessions()

    def action_go_back(self) -> None:
        self.app.pop_screen()
