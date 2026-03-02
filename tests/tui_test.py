from __future__ import annotations

import asyncio

from textual.widgets import Button, DataTable, Input, Select

from tui.app import SlacksorApp
from tui.screens.confirm import ConfirmScreen
from tui.screens.dashboard import DashboardController, Project, Session


class FakeController(DashboardController):
    def __init__(self) -> None:
        self.projects = [Project(id="p1", path="/tmp/proj", channel="#proj", model_override=None)]
        self.sessions = [Session(id="s1", project_id="p1", status="running")]
        self.logs: list[str] = []
        self.added: list[tuple[str, str, str | None]] = []
        self.edited: list[tuple[str, str, str, str | None]] = []
        self.deleted: list[str] = []
        self.killed: list[str] = []
        self.model = "auto"

    def get_projects(self) -> list[Project]:
        return self.projects

    def get_sessions(self) -> list[Session]:
        return self.sessions

    def add_project(self, path: str, channel: str, model_override: str | None) -> None:
        self.added.append((path, channel, model_override))

    def edit_project(self, project_id: str, path: str, channel: str, model_override: str | None) -> None:
        self.edited.append((project_id, path, channel, model_override))

    def delete_project(self, project_id: str) -> None:
        self.deleted.append(project_id)

    def kill_session(self, session_id: str) -> None:
        self.killed.append(session_id)

    def append_log(self, message: str) -> None:
        self.logs.append(message)

    def get_default_model(self) -> str:
        return self.model

    def set_default_model(self, model: str) -> None:
        self.model = model

    def list_model_options(self) -> list[str]:
        return ["auto", "gpt-5", "claude-sonnet-4.5"]

    def stop_workspace(self, workspace_path: str) -> bool:
        del workspace_path
        return True

    def drain_runtime_logs(self) -> list[str]:
        return []


def test_dashboard_tables_render() -> None:
    async def _run() -> None:
        controller = FakeController()
        app = SlacksorApp(controller=controller, on_shutdown=lambda: None)
        async with app.run_test() as pilot:
            del pilot
            dashboard = app.screen
            projects = dashboard.query_one("#projects-table", DataTable)
            sessions = dashboard.query_one("#sessions-table", DataTable)
            assert projects.row_count == 1
            assert sessions.row_count == 1

    asyncio.run(_run())


def test_add_project_modal_submission() -> None:
    async def _run() -> None:
        controller = FakeController()
        app = SlacksorApp(controller=controller, on_shutdown=lambda: None)
        async with app.run_test() as pilot:
            dashboard = app.screen
            dashboard.action_add_project()
            await pilot.pause()
            modal = app.screen
            path_input = modal.query_one("#path-input", Input)
            channel_input = modal.query_one("#channel-input", Input)
            override_select = modal.query_one("#model-override-select", Select)
            path_input.value = "/tmp/new-proj"
            channel_input.value = "new-chan"
            override_select.value = "gpt-5"
            save_button = modal.query_one("#save")
            save_button.press()
            await pilot.pause()
            assert controller.added == [("/tmp/new-proj", "new-chan", "gpt-5")]

    asyncio.run(_run())


def test_dashboard_edit_delete_kill() -> None:
    async def _run() -> None:
        controller = FakeController()
        app = SlacksorApp(controller=controller, on_shutdown=lambda: None)
        async with app.run_test() as pilot:
            dashboard = app.screen
            projects = dashboard.query_one("#projects-table", DataTable)
            sessions = dashboard.query_one("#sessions-table", DataTable)
            projects.move_cursor(row=0, column=0)
            sessions.move_cursor(row=0, column=0)
            dashboard.action_edit_project()
            await pilot.pause()
            modal = app.screen
            path_input = modal.query_one("#path-input", Input)
            channel_input = modal.query_one("#channel-input", Input)
            override_select = modal.query_one("#model-override-select", Select)
            path_input.value = "/tmp/proj-edit"
            channel_input.value = "proj-edit"
            override_select.value = "auto"
            modal.query_one("#save").press()
            await pilot.pause()
            dashboard = app.screen
            projects = dashboard.query_one("#projects-table", DataTable)
            sessions = dashboard.query_one("#sessions-table", DataTable)
            projects.move_cursor(row=0, column=0)
            sessions.move_cursor(row=0, column=0)

            dashboard.action_delete_project()
            await pilot.pause()
            confirm = app.screen
            assert isinstance(confirm, ConfirmScreen)
            confirm.query_one("#confirm-yes", Button).press()
            await pilot.pause()

            dashboard = app.screen
            sessions = dashboard.query_one("#sessions-table", DataTable)
            sessions.move_cursor(row=0, column=0)
            dashboard.action_kill_session()
            await pilot.pause()
            confirm = app.screen
            assert isinstance(confirm, ConfirmScreen)
            confirm.query_one("#confirm-yes", Button).press()
            await pilot.pause()

            dashboard = app.screen
            dashboard.action_model()
            await pilot.pause()
            model_modal = app.screen
            model_select = model_modal.query_one("#model-select-input", Select)
            model_select.value = "gpt-5"
            model_modal.query_one("#save").press()
            await pilot.pause()
            dashboard.action_help()
            dashboard.action_stop()
            dashboard.action_quit()
            assert controller.edited
            assert controller.deleted
            assert controller.killed

    asyncio.run(_run())


def test_delete_project_cancel_does_nothing() -> None:
    async def _run() -> None:
        controller = FakeController()
        app = SlacksorApp(controller=controller, on_shutdown=lambda: None)
        async with app.run_test() as pilot:
            dashboard = app.screen
            projects = dashboard.query_one("#projects-table", DataTable)
            projects.move_cursor(row=0, column=0)

            dashboard.action_delete_project()
            await pilot.pause()
            confirm = app.screen
            assert isinstance(confirm, ConfirmScreen)
            confirm.query_one("#confirm-no", Button).press()
            await pilot.pause()

            assert controller.deleted == []

    asyncio.run(_run())


def test_kill_session_cancel_does_nothing() -> None:
    async def _run() -> None:
        controller = FakeController()
        app = SlacksorApp(controller=controller, on_shutdown=lambda: None)
        async with app.run_test() as pilot:
            dashboard = app.screen
            sessions = dashboard.query_one("#sessions-table", DataTable)
            sessions.move_cursor(row=0, column=0)

            dashboard.action_kill_session()
            await pilot.pause()
            confirm = app.screen
            assert isinstance(confirm, ConfirmScreen)
            confirm.query_one("#confirm-no", Button).press()
            await pilot.pause()

            assert controller.killed == []

    asyncio.run(_run())


def test_confirm_screen_escape_dismisses() -> None:
    async def _run() -> None:
        controller = FakeController()
        app = SlacksorApp(controller=controller, on_shutdown=lambda: None)
        async with app.run_test() as pilot:
            dashboard = app.screen
            projects = dashboard.query_one("#projects-table", DataTable)
            projects.move_cursor(row=0, column=0)

            dashboard.action_delete_project()
            await pilot.pause()
            confirm = app.screen
            assert isinstance(confirm, ConfirmScreen)
            confirm.action_cancel()
            await pilot.pause()

            assert controller.deleted == []

    asyncio.run(_run())
