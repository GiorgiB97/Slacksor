"""Model selector modal for Slacksor TUI."""

from dataclasses import dataclass

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select


@dataclass
class ModelSelectionResult:
    model: str


class ModelSelectScreen(ModalScreen[ModelSelectionResult | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        options: list[str],
        current_model: str,
        title: str = "Select Default Model",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._options = options
        self._current_model = current_model
        self._title = title

    def compose(self) -> "ModalScreen.ComposeResult":
        select_options = [(option, option) for option in self._options]
        initial = self._current_model if self._current_model in self._options else self._options[0]
        with Vertical(id="model-select-form"):
            yield Label(self._title, id="model-select-title")
            with Vertical(id="model-select-control"):
                yield Select(options=select_options, value=initial, id="model-select-input")
            with Horizontal(id="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            self.dismiss(None)
            return
        select_widget = self.query_one("#model-select-input", Select)
        value = select_widget.value
        if isinstance(value, str) and value:
            self.dismiss(ModelSelectionResult(model=value))
            return
        self.dismiss(None)
