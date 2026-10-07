"""Keyboard-accessible dropdowns and validated editor dialogs."""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option


class Menu(ModalScreen):
    DEFAULT_CSS = """
    Menu { align: left top; background: $background 30%; }
    Menu > Vertical { width: 40; height: auto; max-height: 90%; margin-top: 1;
        border: none; background: $panel; padding: 1 2; }
    Menu OptionList { height: auto; max-height: 28; border: none; }
    Menu Label { color: $accent; text-style: bold; }
    """
    BINDINGS = [("escape", "cancel", "Close")]

    def __init__(self, title, entries, offset=0):
        super().__init__()
        self.title_text, self.entries, self.menu_offset = title, entries, offset

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text)
            yield OptionList(*(Option(label, id=key) for key, label in self.entries), id="menu-options")

    def on_mount(self):
        self.query_one(Vertical).styles.margin = (1, 0, 0, self.menu_offset)
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event):
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self):
        self.dismiss(None)


class Form(ModalScreen):
    DEFAULT_CSS = """
    Form { align: center middle; background: $background 65%; }
    Form > Vertical { width: 66; height: auto; max-height: 95%; border: none;
        background: $panel; padding: 1 2; overflow-y: auto; }
    Form Label { margin-top: 1; }
    Form #form-title { text-style: bold; color: $accent; margin-top: 0; }
    Form Input, Form Select { width: 100%; }
    Form Horizontal { height: 3; margin-top: 1; }
    Form Button { margin-right: 1; }
    Form .form-path-row { height: 3; margin-top: 0; }
    Form .form-path-row Input { width: 1fr; }
    Form .form-browse { width: 12; min-width: 10; margin-right: 0; }
    Form #form-error { height: auto; color: $error; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title, fields, validate=None, note="", browse_fields=None):
        super().__init__()
        self.title_text, self.fields, self.validator, self.note = title, fields, validate, note
        self.browse_fields = browse_fields or {}

    def compose(self):
        with Vertical():
            yield Label(self.title_text, id="form-title")
            if self.note: yield Static(self.note)
            for key, label, value, options in self.fields:
                yield Label(label)
                if options:
                    yield Select([(str(x), str(x)) for x in options], value=str(value), allow_blank=False, id=key)
                elif key in self.browse_fields:
                    with Horizontal(classes="form-path-row"):
                        yield Input(value=str(value), id=key)
                        yield Button("Browse…", id=f"browse-{key}", classes="form-browse")
                else:
                    yield Input(value=str(value), id=key)
            yield Static("", id="form-error")
            with Horizontal():
                yield Button("Apply", variant="primary", id="form-apply")
                yield Button("Cancel", id="form-cancel")

    def on_mount(self):
        widgets = list(self.query("Input, Select"))
        if widgets: widgets[0].focus()

    def submit(self):
        values = {}
        for key, *_ in self.fields:
            values[key] = self.query_one(f"#{key}").value
        try:
            result = self.validator(values) if self.validator else values
        except (ValueError, OSError) as error:
            self.query_one("#form-error", Static).update(str(error))
            return
        self.dismiss(result)

    def on_button_pressed(self, event):
        key = event.button.id or ""
        if key.startswith("browse-") and key[7:] in self.browse_fields:
            event.stop()
            self.browse_path(key[7:])
        elif key == "form-apply":
            event.stop()
            self.submit()
        elif key == "form-cancel":
            event.stop()
            self.dismiss(None)

    def browse_path(self, key):
        from .file_explorer import FileExplorer
        options = dict(self.browse_fields[key])
        field = self.query_one(f"#{key}", Input)
        start = field.value or options.pop("start_path", None)
        options.pop("start_path", None)

        def chosen(choice):
            if choice is not None:
                field.value = str(choice.path)
            field.focus()

        self.app.push_screen(FileExplorer(start_path=start, **options), chosen)

    def on_input_submitted(self, event):
        event.stop()
        self.submit()

    def action_cancel(self):
        self.dismiss(None)


class Confirm(ModalScreen):
    DEFAULT_CSS = """
    Confirm { align: center middle; background: $background 65%; }
    Confirm Vertical { width: 62; height: auto; border: none; background: $panel; padding: 1 2; }
    Confirm Horizontal { height: 3; margin-top: 1; }
    Confirm Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        with Vertical():
            yield Static(self.message)
            with Horizontal():
                yield Button("Continue", variant="warning", id="confirm-yes")
                yield Button("Cancel", id="confirm-no")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm-yes")

    def action_cancel(self):
        self.dismiss(False)
