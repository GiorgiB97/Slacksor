"""Type-to-confirm modal screen for destructive actions."""

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class TypeConfirmScreen(ModalScreen[bool]):
    """Modal that requires typing a confirmation word to proceed."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        message: str,
        confirm_word: str = "DELETE",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._message = message
        self._confirm_word = confirm_word

    def compose(self) -> "ModalScreen.ComposeResult":
        with Vertical(id="type-confirm-dialog"):
            yield Static(self._message, id="type-confirm-message")
            yield Static(
                f'Type "{self._confirm_word}" to confirm:',
                id="type-confirm-hint",
            )
            yield Input(id="type-confirm-input", placeholder=self._confirm_word)
            with Horizontal(id="type-confirm-buttons"):
                yield Button(
                    "Confirm", variant="error", id="type-confirm-yes", disabled=True
                )
                yield Button("Cancel", variant="default", id="type-confirm-no")

    def on_mount(self) -> None:
        self.query_one("#type-confirm-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "type-confirm-input":
            matches = event.value.strip() == self._confirm_word
            self.query_one("#type-confirm-yes", Button).disabled = not matches

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "type-confirm-input":
            if event.value.strip() == self._confirm_word:
                self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "type-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
