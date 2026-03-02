"""Slacksor TUI application entry point."""

from collections.abc import Callable
from pathlib import Path

from textual.app import App

from tui.screens.dashboard import DashboardController, DashboardScreen


class SlacksorApp(App):
    """Main Slacksor TUI application."""

    TITLE = "Slacksor"
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = Path(__file__).parent / "dashboard.tcss"
    SCREENS = {"dashboard": DashboardScreen}

    def __init__(
        self,
        controller: DashboardController,
        on_shutdown: Callable[[], None],
    ) -> None:
        super().__init__()
        self._controller = controller
        self._on_shutdown = on_shutdown

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen(controller=self._controller))

    def on_unmount(self) -> None:
        self._on_shutdown()


def run(controller: DashboardController, on_shutdown: Callable[[], None]) -> None:
    """Run the Slacksor TUI application."""
    app = SlacksorApp(controller=controller, on_shutdown=on_shutdown)
    app.run()


if __name__ == "__main__":
    raise SystemExit("Run slacksor.py instead.")
