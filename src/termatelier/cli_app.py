"""A full terminal command workspace backed by the editor's shared document.

The separate painting preview is a viewer: rendering it never resizes editable
pixels. The same screen can be pushed over Studio or run in its own CLIApp.
"""
from __future__ import annotations

import os
import time
from typing import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Footer, Input, RichLog, Static

from .commands import CommandError, CommandResult, CommandSession, HELP, TOOLS as CLASSIC_TOOLS, tokenize
from .dialogs import Confirm


FILE_OPERATIONS = ("open", "import", "save", "export", "script", "debug", "font", "directory")
FILES_HELP = (
    "files [open|import|save|export|script|debug|font|directory] [START_PATH] — open the file explorer.\n"
    "F6 opens its operation chooser; Ctrl+O opens, Ctrl+E exports, and the first Ctrl+S saves.\n"
    "Browse folders with the mouse or arrow keys and Enter; use the address field for a path.\n"
    "Choose a file, filter the list, create a folder, and use the filename field for new files.\n"
    "Save/export destinations are checked before writing; existing destinations ask before replacement.\n"
    "Exports and debug JSON default to the external export folder. PNG, WebP and TIFF preserve RGBA pixels.\n"
    "Font selection prepares a text command for you to edit. Esc or Cancel closes without changes.\n"
    "files directory chooses the persistent external export folder.\n"
    "The explorer is available in the full-screen CLI. Piped CLI, scripts and --repl use path commands."
)


def quote_command_argument(value):
    """Quote one argument for our non-escaping tokenizer, including apostrophes."""
    return '"' + str(value).replace('"', '"\'"\'"') + '"'


class CLIInput(Input):
    """Keep command history keys local to the workspace's input field."""

    BINDINGS = [
        Binding("up", "recall_previous", show=False, priority=True),
        Binding("down", "recall_next", show=False, priority=True),
        Binding("ctrl+space", "complete", show=False, priority=True),
        Binding("tab", "complete", show=False, priority=True),
    ]

    def action_recall_previous(self):
        self.screen.recall_command(-1)

    def action_recall_next(self):
        self.screen.recall_command(1)

    def action_complete(self):
        self.screen.complete_command()


class CLIWorkspaceScreen(Screen):
    """Full-screen output and prompt, with a live pop-out painting viewer.

    ``on_document_changed(session, result)`` synchronizes a surrounding Studio.
    ``on_exit()`` returns there without discarding edits. With no exit callback,
    this is a standalone workspace and leaving it uses the unsaved-work guard.
    """

    DEFAULT_CSS = """
    CLIWorkspaceScreen { background: #090909; color: #d7d7d7; layout: vertical; }
    CLIWorkspaceScreen #cli-header { height: 2; padding: 0 1; background: #121212; }
    CLIWorkspaceScreen #cli-title { width: 1fr; color: #e79335; text-style: bold; }
    CLIWorkspaceScreen #cli-preview-status { width: auto; color: #999999; }
    CLIWorkspaceScreen #cli-command-output { height: 1fr; width: 100%; padding: 0 1;
        border: none; background: #090909; scrollbar-color: #444444; }
    CLIWorkspaceScreen #cli-document-status { height: 1; padding: 0 1; color: #999999;
        background: #121212; }
    CLIWorkspaceScreen #cli-prompt-row { height: 3; padding: 0 1; background: #121212; }
    CLIWorkspaceScreen #cli-prompt { width: 10; height: 3; content-align: left middle;
        color: #e79335; text-style: bold; }
    CLIWorkspaceScreen #cli-command-line { width: 1fr; border: none; background: #121212; }
    CLIWorkspaceScreen #cli-command-line:focus { border: none; }
    CLIWorkspaceScreen Footer { background: #121212; }
    """

    BINDINGS = [
        Binding("f1", "help", "Help"),
        Binding("f2", "tools", "Tools"),
        Binding("f3", "return", "Painter / exit"),
        Binding("escape", "return", show=False),
        Binding("f5", "preview", "Preview"),
        Binding("f6", "files", "Files"),
        Binding("f4", "history", "History"),
        Binding("ctrl+n", "new_prompt", "New", show=False, priority=True),
        Binding("ctrl+o", "open_prompt", "Open", show=False, priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+e", "export_prompt", "Export", priority=True),
        Binding("ctrl+z", "undo", "Undo", priority=True),
        Binding("ctrl+y", "redo", "Redo", priority=True),
        Binding("ctrl+l", "clear_log", "Clear output"),
        Binding("pageup", "output_up", show=False),
        Binding("pagedown", "output_down", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, session: CommandSession,
                 on_document_changed: Callable | None = None,
                 on_exit: Callable | None = None, preview=None,
                 on_view_command: Callable | None = None):
        super().__init__()
        self.session = session
        self.on_document_changed = on_document_changed
        self.on_exit = on_exit
        self.on_view_command = on_view_command
        self.preview = preview
        self.command_history: list[str] = []
        self.command_cursor = 0
        self._draft = ""
        self._preview_closed = False
        self._preview_label = None
        self._diagnostics_last = 0.0

    def check_action(self, action, parameters):
        # Priority shortcuts must never edit the hidden workspace while a file
        # browser or confirmation owns input.
        return self.app.screen is self

    @property
    def document(self):
        return self.session.document

    def compose(self) -> ComposeResult:
        with Horizontal(id="cli-header"):
            yield Static("SPARKER iCLI  /  COMMAND WORKSPACE", id="cli-title")
            yield Static("Preview starting…", id="cli-preview-status")
        yield RichLog(id="cli-command-output", wrap=True, markup=False,
                      highlight=False, max_lines=10000)
        yield Static("", id="cli-document-status")
        with Horizontal(id="cli-prompt-row"):
            yield Static("sparker ›", id="cli-prompt")
            yield CLIInput(placeholder="Type a command · help · tools search · ↑ history · Ctrl+Space complete",
                           id="cli-command-line")
        yield Footer()

    def on_mount(self):
        self.write("Commands edit the same painting and share its undo history.")
        self.write("Type help for syntax or tools search QUERY to explore the tool library.")
        self.write("F5 opens the painting preview again if you close its window.")
        self.write("F6 opens the file explorer for projects, imports, exports, scripts and fonts.")
        self._ensure_preview()
        self._start_preview()
        self.update_status()
        self.set_interval(.25, self._poll_runtime)
        self.query_one("#cli-command-line", CLIInput).focus()
        self.call_after_refresh(self._signal_ready)

    def _ensure_preview(self):
        if self.preview is None and not self.app.is_headless:
            try:
                from .preview import PreviewController
                self.preview = PreviewController(config=self.session.config)
            except (ImportError, OSError, RuntimeError) as error:
                self.write_error(f"Preview unavailable: {error}")

    def _signal_ready(self):
        from .startup import signal_ready
        signal_ready()
        self.publish_debug(force=True)

    def _poll_runtime(self):
        self.update_preview_status()
        self.publish_debug()

    def publish_debug(self, force=False):
        # Studio owns its own telemetry publisher while the screen is embedded.
        if not isinstance(self.app, CLIApp) or not os.environ.get("SPARKER_DEBUG_STATE"):
            return
        now = time.monotonic()
        if not force and now - self._diagnostics_last < self.session.config.get("debug.refresh_ms") / 1000:
            return
        self._diagnostics_last = now
        from .diagnostics import collect_diagnostics
        from .startup import write_debug_snapshot
        data = collect_diagnostics(self.document, self.session.config)
        data["terminal"].update(columns=self.app.size.width, rows=self.app.size.height)
        data["runtime"] = {"mode": "command workspace", "tool": self.session.setting("tool"),
                           "project_path": str(self.session.project_path or "unsaved"),
                           "preview_running": self.preview is not None and self.preview.running}
        write_debug_snapshot({"diagnostics": data, "stage": "ready", "updated_at": time.time()})

    def metadata(self):
        return {"source": "cli", "path": str(self.session.project_path or ""),
                "dirty": self.document.dirty}

    def _start_preview(self):
        if self.preview is not None:
            try:
                started = self.preview.start(self.document, self.metadata())
                if not started and self.preview.error:
                    self.write_error(f"Preview unavailable: {self.preview.error}")
            except (OSError, ValueError, RuntimeError) as error:
                self.write_error(f"Preview unavailable: {error}")
        self.update_preview_status()

    def update_preview_status(self):
        if not self.is_mounted:
            return
        running = self.preview is not None and self.preview.running
        text = "Live painting preview" if running else "Preview closed · F5 to open"
        if self.preview is not None and not running and self.preview.error:
            text = "Preview unavailable · F5 to retry"
        if self.preview is None and self.app.is_headless:
            text = "Preview omitted in headless mode"
        if not self.session.config.get("preview.enabled"):
            text = ("Preview disabled in low-memory mode" if self.session.config.get("memory.mode") == "low"
                    else "Preview disabled in settings")
        if text != self._preview_label:
            self.query_one("#cli-preview-status", Static).update(Text(text))
            self._preview_label = text

    def update_status(self):
        if not self.is_mounted:
            return
        doc = self.document
        name = self.session.project_path.name if self.session.project_path else "Untitled.tart"
        text = (f"{name}{' *' if doc.dirty else ''}  ·  {doc.width}×{doc.height} pixels  ·  "
                f"{len(doc.layers)} layers  ·  active: {doc.layer.name}  ·  "
                f"{len(doc.undo_stack)} undo / {len(doc.redo_stack)} redo")
        self.query_one("#cli-document-status", Static).update(Text(text))
        self.update_preview_status()

    def write(self, value):
        self.query_one("#cli-command-output", RichLog).write(
            value if isinstance(value, Text) else Text(str(value)))

    def write_error(self, message):
        self.write(Text(f"Error: {message}", style="#ce7972"))

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "cli-command-line":
            return
        event.stop()
        line = event.value.strip()
        event.input.value = ""
        if line:
            self.submit_command(line)

    def submit_command(self, line):
        line = line.strip()
        if not line:
            return
        if not self.command_history or self.command_history[-1] != line:
            self.command_history.append(line)
            self.command_history = self.command_history[-500:]
        self.command_cursor = len(self.command_history)
        self._draft = ""
        self.write(Text(f"› {line}", style="#e79335"))
        try:
            tokens = tokenize(line)
        except CommandError as error:
            self.write_error(error)
            return
        if tokens and tokens[0].lower() in ("new", "open", "script") and self.document.dirty:
            self.app.push_screen(
                Confirm("This painting has unsaved changes. Continue with this command?\n"
                        "Cancel, then use save to preserve the editable project."),
                lambda yes: self.execute_command(line) if yes else self._refocus())
        else:
            self.execute_command(line)

    def execute_command(self, line):
        """Refresh shared state after success or edits retained by a failed script."""
        previous_document = self.document
        previous_state = self._state_signature()
        command_completed = False
        try:
            tokens = tokenize(line)
            if tokens and tokens[0].lower() == "view" and self.on_view_command:
                self.write(self.on_view_command(line))
                result = CommandResult()
            elif line.strip().lower() == "help view" and self.on_view_command:
                self.write(self.on_view_command("view"))
                result = CommandResult()
            elif tokens and tokens[0].lower() == "preview":
                if len(tokens) != 1:
                    raise CommandError("preview — open the live painting viewer")
                self.action_preview()
                result = CommandResult()
            elif line.strip().lower() == "help preview":
                self.write("preview — open the live painting viewer.\n"
                           "In the preview: F fits the painting, 1 shows actual pixels, "
                           "the mouse wheel zooms, and left-drag pans.\n"
                           "In the CLI: F5 reopens the viewer after closing it.")
                result = CommandResult()
            elif tokens and tokens[0].lower() == "files":
                self.open_file_explorer(tokens[1:])
                return
            elif line.strip().lower() == "help files":
                self.write(FILES_HELP)
                result = CommandResult()
            else:
                result = self.session.execute(line)
                if result.text:
                    self.write(result.text)
                if line.strip().lower() == "help":
                    self.write("Workspace: files / F6 opens the file explorer; help files explains it. "
                               "preview / F5 opens the painting viewer; F3 / Esc returns or exits.")
            command_completed = True
            self._refresh_document(result)
            if result.quit_requested:
                self.action_quit()
        except (CommandError, ValueError, OSError) as error:
            self.write_error(error)
            # A nonatomic script deliberately keeps completed edits. Its failed
            # final line must not leave the viewer or surrounding editor stale.
            if not command_completed and self._state_signature() != previous_state:
                self._refresh_document(CommandResult(changed=True,
                    document_replaced=self.document is not previous_document))
        self._refocus()

    def _state_signature(self):
        doc = self.document
        return (id(doc), doc.revision, doc.saved_revision, self.session.project_path,
                self.session.config.as_dict())

    def _refresh_document(self, result):
        if self.on_document_changed:
            self.on_document_changed(self.session, result)
        if self.preview is not None:
            from .preview import PreviewController
            if isinstance(self.preview, PreviewController):
                self.preview.update(self.document, self.metadata(),
                                    artwork_changed=bool(result.changed or result.document_replaced))
            else:
                # Embedded preview adapters retain the original two-argument API.
                self.preview.update(self.document, self.metadata())
        self.update_status()
        self.publish_debug(force=True)

    def _refocus(self):
        if self.is_mounted and self.app.screen is self:
            self.query_one("#cli-command-line", CLIInput).focus()

    def recall_command(self, direction):
        field = self.query_one("#cli-command-line", CLIInput)
        if self.command_cursor == len(self.command_history):
            self._draft = field.value
        self.command_cursor = max(0, min(len(self.command_history), self.command_cursor + direction))
        field.value = (self.command_history[self.command_cursor]
                       if self.command_cursor < len(self.command_history) else self._draft)
        field.cursor_position = len(field.value)

    def complete_command(self):
        field = self.query_one("#cli-command-line", CLIInput)
        value = field.value.lstrip()
        if " " not in value:
            choices = sorted({*HELP, "preview", "files", *(('view',) if self.on_view_command else ())})
            prefix = value
            stem = ""
        else:
            words = value.split()
            if words[0] == "files" and len(words) <= 2:
                stem = "files "
                choices = list(FILE_OPERATIONS)
                prefix = value[len(stem):]
                matches = [choice for choice in choices if choice.startswith(prefix)]
                if len(matches) == 1:
                    field.value = stem + matches[0] + " "
                    field.cursor_position = len(field.value)
                elif matches:
                    self.write("  ".join(matches))
                return
            if words[0] in ("apply", "tool") and len(words) <= 2:
                stem = f"{words[0]} "
            elif words[0] == "tools" and (len(words) == 2 and words[1] in ("use", "apply", "info")
                                           or len(words) == 3 and words[1] in ("use", "apply", "info")):
                stem = f"tools {words[1]} "
            else:
                return
            try:
                from .tool_library import TOOLS
                choices = sorted(set(TOOLS) | (CLASSIC_TOOLS if words[0] == "tool" else set()))
            except ImportError:
                choices = []
            prefix = value[len(stem):]
        matches = [choice for choice in choices if choice.startswith(prefix)]
        if len(matches) == 1:
            field.value = stem + matches[0] + " "
            field.cursor_position = len(field.value)
        elif matches:
            self.write("  ".join(matches[:80]))
            if len(matches) > 80:
                self.write(f"{len(matches)} matches. Type more of the tool name to narrow completion.")

    def action_help(self):
        self.submit_command("help")

    def action_tools(self):
        self.submit_command("tools categories")

    def action_history(self):
        self.submit_command("history")

    def prefill_command(self, line):
        field = self.query_one("#cli-command-line", CLIInput)
        field.value = line
        field.cursor_position = len(line)
        self.command_cursor = len(self.command_history)
        field.focus()

    def action_new_prompt(self):
        self.prefill_command("new ")

    def action_open_prompt(self):
        self.submit_command("files open")

    def action_export_prompt(self):
        self.submit_command("files export")

    def action_save(self):
        if self.session.project_path:
            self.submit_command("save")
        else:
            self.submit_command("files save")

    def action_files(self):
        self.submit_command("files")

    def open_file_explorer(self, arguments=()):
        from .file_explorer import FileExplorer
        if len(arguments) > 2 or arguments and arguments[0].lower() not in FILE_OPERATIONS:
            raise CommandError("files [open|import|save|export|script|debug|font|directory] [START_PATH]")
        operation = arguments[0].lower() if arguments else "open"
        start = self.session._path(arguments[1]) if len(arguments) == 2 else None
        if start is None:
            if operation in ("export", "debug", "directory"):
                from .storage import default_export_directory
                start = default_export_directory(self.session.config)
            else:
                start = (self.session.project_path.parent if self.session.project_path
                         else self.session.base_dir)
        project_name = self.session.project_path.stem if self.session.project_path else "Untitled"
        default_name = {"save": project_name + ".tart", "export": project_name + ".png",
                        "debug": "sparker-debug.json"}.get(operation, "")
        if operation == "save" and self.session.project_path:
            default_name = self.session.project_path.name
        if len(arguments) == 2 and (start.is_file() or start.suffix and not start.is_dir()):
            default_name = start.name
        from .storage import project_roots
        explorer = FileExplorer(operation, start, default_name, config=self.session.config,
                                allow_operations=not arguments, validator=self._validate_file_choice,
                                project_dir=project_roots()[0])
        self.app.push_screen(explorer, self._file_chosen)

    def _validate_file_choice(self, choice):
        from .file_explorer import FileChoice
        from .storage import resolve_export_path, validate_export_directory
        if choice.operation not in FILE_OPERATIONS:
            raise ValueError("Choose a supported file operation.")
        path = self.session._path(choice.path)
        if choice.operation == "directory":
            path = validate_export_directory(path)
            if not path.is_dir():
                raise ValueError("Choose an existing export folder, or create one with New folder.")
        if choice.operation in ("export", "debug"):
            path = resolve_export_path(path, config=self.session.config)
        if choice.operation == "save" and path.suffix.lower() != ".tart":
            raise ValueError("Native project files use the .tart extension.")
        if choice.operation == "debug" and path.suffix.lower() != ".json":
            raise ValueError("Debug export requires a .json filename.")
        if choice.operation == "export":
            suffix = path.suffix.lower()
            if suffix not in (".png", ".webp", ".tif", ".tiff", ".jpg", ".jpeg",
                              ".gif", ".bmp", ".txt", ".ansi"):
                raise ValueError("Choose PNG, WebP, TIFF, JPEG, GIF, BMP, TXT or ANSI.")
            if (suffix in (".jpg", ".jpeg", ".gif", ".bmp") and
                    self.session.config.get("export.lossless") and not choice.allow_lossy):
                raise ValueError("This format cannot preserve every RGBA pixel. Use PNG, TIFF "
                                 "or lossless WebP, or explicitly allow lossy export.")
            if type(choice.columns) is not int or not 1 <= choice.columns <= 500:
                raise ValueError("Text export columns must be between 1 and 500.")
        return FileChoice(path, choice.operation, choice.columns, choice.allow_lossy)

    def _file_chosen(self, choice):
        if choice is None:
            self._refocus()
            return
        # Recheck when the picker closes: the destination or preferences may
        # have changed while browsing.
        try:
            choice = self._validate_file_choice(choice)
        except (ValueError, OSError) as error:
            self.write_error(error)
            self._refocus()
            return
        if choice.operation == "font":
            self.prefill_command(f'text 0 0 "Your text" --font {quote_command_argument(choice.path)}')
            return
        destination = choice.path
        current_save = (choice.operation == "save" and destination == self.session.project_path)
        if (choice.operation in ("save", "export", "debug") and destination.exists()
                and not current_save):
            self.app.push_screen(Confirm(f"Replace the existing file?\n{destination}"),
                                 lambda yes: self._submit_file_choice(choice) if yes else self._refocus())
        else:
            self._submit_file_choice(choice)

    def _submit_file_choice(self, choice):
        if choice.operation == "directory":
            self.submit_command(f"config set export.directory {quote_command_argument(choice.path)}")
            return
        operation = "debug export" if choice.operation == "debug" else choice.operation
        line = f"{operation} {quote_command_argument(choice.path)}"
        if choice.operation == "export":
            line += f" --columns {choice.columns}"
            if choice.allow_lossy:
                line += " --allow-lossy"
        self.submit_command(line)

    def action_undo(self):
        self.submit_command("undo")

    def action_redo(self):
        self.submit_command("redo")

    def action_clear_log(self):
        self.query_one("#cli-command-output", RichLog).clear()

    def action_output_up(self):
        self.query_one("#cli-command-output", RichLog).scroll_page_up()

    def action_output_down(self):
        self.query_one("#cli-command-output", RichLog).scroll_page_down()

    def action_preview(self):
        if self.preview is None:
            if self.app.is_headless:
                self.write("Preview is omitted in headless mode.")
                return
            self._ensure_preview()
            self._start_preview()
        else:
            try:
                self.preview.reopen(self.document, self.metadata())
            except (OSError, ValueError, RuntimeError) as error:
                self.write_error(f"Preview unavailable: {error}")
        self.update_preview_status()

    def action_return(self):
        self.action_quit()

    def action_quit(self):
        if self.on_exit is not None:
            self._close_preview()
            self.on_exit()
        elif self.document.dirty:
            self.app.push_screen(
                Confirm("This painting has unsaved changes. Exit and discard them?\n"
                        "Cancel, then use save to preserve the editable project."),
                lambda yes: self._exit_app() if yes else self._refocus())
        else:
            self._exit_app()

    def _exit_app(self):
        self._close_preview()
        self.app.exit()

    def _close_preview(self):
        if not self._preview_closed and self.preview is not None:
            self.preview.close()
        self._preview_closed = True

    def on_unmount(self):
        self._close_preview()


class CLIApp(App):
    """Standalone full terminal command mode used by interactive ``--cli``."""

    TITLE = "SPARKER iCLI · Command workspace"
    BINDINGS = [Binding("ctrl+q", "guarded_quit", "Quit", priority=True)]

    def __init__(self, session: CommandSession, preview=None):
        super().__init__()
        self.session = session
        self.workspace = CLIWorkspaceScreen(session, preview=preview)
        self.register_theme(Theme(name="sparker-cli", primary="#e79335", secondary="#999999",
                                  accent="#e79335", foreground="#d7d7d7", background="#090909",
                                  surface="#121212", panel="#161616", success="#93a98f",
                                  warning="#e79335", error="#ce7972", dark=True))
        self.theme = "sparker-cli"

    def on_mount(self):
        self.push_screen(self.workspace)

    def on_unmount(self):
        self.workspace._close_preview()

    def action_guarded_quit(self):
        if self.screen is self.workspace:
            self.workspace.action_quit()
