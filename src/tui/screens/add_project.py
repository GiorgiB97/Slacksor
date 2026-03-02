"""Add/Edit project modal screen for Slacksor TUI."""

from dataclasses import dataclass

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select


@dataclass
class ProjectFormResult:
    """Result from add/edit project form."""

    project_id: str | None
    path: str
    channel: str
    model_override: str | None


class AddProjectScreen(ModalScreen[ProjectFormResult | None]):
    """Modal for adding or editing a project (path and channel)."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    _NO_OVERRIDE_VALUE = "__none__"

    def __init__(
        self,
        path: str = "",
        channel: str = "",
        model_override: str | None = None,
        model_options: list[str] | None = None,
        project_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._path = path
        self._channel = channel
        self._model_override = model_override
        self._model_options = model_options if model_options else []
        self._project_id = project_id

    def compose(self) -> "ModalScreen.ComposeResult":
        select_options = [("No override", self._NO_OVERRIDE_VALUE)]
        for option in self._model_options:
            select_options.append((option, option))
        if self._model_override and self._model_override not in self._model_options:
            select_options.append((self._model_override, self._model_override))
        selected_model = self._model_override or self._NO_OVERRIDE_VALUE
        with Vertical(id="form"):
            with Vertical(classes="form-field"):
                yield Label("Project path:")
                yield Input(
                    id="path-input",
                    placeholder="/path/to/project",
                    value=self._path,
                )
            with Vertical(classes="form-field"):
                yield Label("Channel:")
                yield Input(
                    id="channel-input",
                    placeholder="#channel",
                    value=self._channel,
                )
            with Vertical(classes="form-field"):
                yield Label("Override default model (optional):")
                yield Select(
                    options=select_options,
                    value=selected_model,
                    id="model-override-select",
                )
            with Horizontal(id="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#path-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            path_input = self.query_one("#path-input", Input)
            channel_input = self.query_one("#channel-input", Input)
            model_select = self.query_one("#model-override-select", Select)
            path = path_input.value.strip()
            channel = channel_input.value.strip()
            if path and channel:
                model_value = model_select.value
                model_override = None
                if isinstance(model_value, str) and model_value != self._NO_OVERRIDE_VALUE:
                    model_override = model_value
                result = ProjectFormResult(
                    project_id=self._project_id,
                    path=path,
                    channel=channel,
                    model_override=model_override,
                )
                self.dismiss(result)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "path-input":
            self.query_one("#channel-input", Input).focus()
        elif event.input.id == "channel-input":
            self.query_one("#model-override-select", Select).focus()
