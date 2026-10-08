"""A shared, read-first file browser for editor and command workspace actions.

Browsing and previews never create or modify files. The only filesystem write
performed by this screen is the user's explicit New folder action; callers own
the final operation, overwrite confirmation, and document replacement guards.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from PIL import Image, ImageOps
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from .config import RuntimeConfig, pictures_directory
from .dialogs import Form
from .storage import default_export_directory, load_project, project_roots, validate_export_directory


@dataclass(frozen=True)
class FileChoice:
    path: Path
    operation: str
    columns: int = 100
    allow_lossy: bool = False


OPERATIONS = {
    "open": "Open image / project", "import": "Import image as layer",
    "save": "Save native project", "export": "Export painting",
    "script": "Run command script", "debug": "Save debug report", "font": "Choose font",
    "directory": "Export folder",
}
IMAGE_EXTENSIONS = (".png", ".tif", ".tiff", ".webp", ".jpg", ".jpeg", ".bmp", ".gif")
READ_OPERATIONS = {"open", "import", "script", "font"}
EXTENSIONS = {
    "open": (".tart", *IMAGE_EXTENSIONS), "import": IMAGE_EXTENSIONS,
    "save": (".tart",), "export": (".png", ".tiff", ".webp", ".txt", ".ansi", ".jpg", ".gif", ".bmp"),
    "script": (".sparker", ".txt"), "debug": (".json",), "font": (".ttf", ".otf", ".ttc"), "directory": (),
}
DEFAULT_SUFFIX = {"save": ".tart", "export": ".png", "debug": ".json"}
FORMAT_ALIASES = {".tif": ".tiff", ".jpeg": ".jpg"}
MAX_ENTRIES = 5000
MAX_PREVIEW_PIXELS = 4_194_304
MAX_PROJECT_PREVIEW_BYTES = 16 * 1024 * 1024
TEXT_PREVIEW_BYTES = 4096


def literal(value):
    """Keep filenames and source text literal, including control sequences."""
    return "".join(character if character.isprintable() or character in "\n\t" else f"\\x{ord(character):02x}"
                   for character in str(value))


def readable_size(size):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def nearest_directory(path):
    """Find an existing ancestor without creating the intended destination."""
    candidate = Path(path).expanduser().resolve()
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else Path.cwd()


def folder_name(value):
    name = str(value).strip()
    if not name or name in (".", "..") or "/" in name or any(ord(character) < 32 for character in name):
        raise ValueError("Enter one folder name, without a path or control characters.")
    if os.name == "nt":
        if any(character in name for character in '\\<>:"|?*') or name.endswith((".", " ")):
            raise ValueError("The folder name contains characters reserved by Windows.")
        if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
            raise ValueError("That folder name is reserved by Windows.")
    if len(name) > 128:
        raise ValueError("Use a folder name of at most 128 characters.")
    return name


def _image_text(image, max_width=22):
    """Small terminal image preview; original canvas and file remain untouched."""
    canvas = image.convert("RGBA")
    canvas.thumbnail((max(1, min(44, int(max_width))), 24), Image.Resampling.BILINEAR)
    background = Image.new("RGBA", canvas.size, "#202020")
    for y in range(canvas.height):
        for x in range(canvas.width):
            if (x // 4 + y // 4) % 2:
                background.putpixel((x, y), (44, 44, 44, 255))
    display = Image.alpha_composite(background, canvas).convert("RGB")
    text = Text()
    for y in range(0, display.height, 2):
        for x in range(display.width):
            upper = display.getpixel((x, y))
            lower = display.getpixel((x, min(y + 1, display.height - 1)))
            foreground = "#%02x%02x%02x" % upper
            background_color = "#%02x%02x%02x" % lower
            text.append("▀", style=f"{foreground} on {background_color}")
        text.append("\n")
    return text


def file_preview(path, *, max_width=22):
    """Return safe literal information and a bounded preview; never execute files."""
    path = Path(path)
    information = Text(literal(path.name), style="bold")
    try:
        details = path.stat()
        information.append(f"\n{readable_size(details.st_size)} · {datetime.fromtimestamp(details.st_mtime):%Y-%m-%d %H:%M}\n")
        if path.is_dir():
            information.append("Folder\nEnter or double-click to browse.")
            return information, Text()
        if not stat.S_ISREG(details.st_mode):
            information.append("Preview unavailable for this file type.")
            return information, Text()
        if path.suffix.lower() == ".tart":
            with zipfile.ZipFile(path) as archive:
                header = archive.getinfo("manifest.json")
                if header.file_size > 1024 * 1024:
                    raise ValueError("Project manifest exceeds the preview limit.")
                data = json.loads(archive.read(header))
                width, height = data["width"], data["height"]
                if type(width) is not int or type(height) is not int:
                    raise ValueError("Invalid project dimensions.")
                layers = data.get("layers", [])
                information.append(f"Native project · {width} × {height} pixels\n{len(layers)} layers\n")
                if (width * height > MAX_PREVIEW_PIXELS or width * height * (5 * len(layers) + 1) > MAX_PROJECT_PREVIEW_BYTES
                        or sum(item.file_size for item in archive.infolist()) > MAX_PROJECT_PREVIEW_BYTES):
                    information.append("Project is too large for the browser preview; opening still uses the normal project loader.")
                    return information, Text()
            document = load_project(path)
            return information, _image_text(document.composite(), max_width)
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            with Image.open(path) as image:
                information.append(f"{image.format} · {image.width} × {image.height} pixels · {image.mode}\n")
                if image.width * image.height > MAX_PREVIEW_PIXELS:
                    information.append("Image is too large for the browser preview; its dimensions remain unchanged.")
                    return information, Text()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((128, 128), Image.Resampling.BILINEAR)
                return information, _image_text(image, max_width)
        if path.suffix.lower() in (".ttf", ".otf", ".ttc"):
            information.append("Font file · loaded only when selected.")
            return information, Text()
        with path.open("rb") as stream:
            raw = stream.read(TEXT_PREVIEW_BYTES + 1)
        if b"\0" in raw:
            information.append("Binary file · no text preview.")
            return information, Text()
        source = literal(raw[:TEXT_PREVIEW_BYTES].decode("utf-8", errors="replace"))
        if len(raw) > TEXT_PREVIEW_BYTES:
            source += "\n… Preview limited to 4 KiB."
        return information, Text(source)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, zipfile.BadZipFile,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        information.append(f"\nPreview unavailable: {literal(error)}")
        return information, Text()


class _FolderForm(Form):
    """The folder path in filesystem errors is literal even with Rich markup."""
    def submit(self):
        values = {key: self.query_one(f"#{key}").value for key, *_ in self.fields}
        try:
            result = self.validator(values)
        except (ValueError, OSError) as error:
            self.query_one("#form-error", Static).update(Text(literal(error)))
            return
        self.dismiss(result)


class FileExplorer(ModalScreen):
    DEFAULT_CSS = """
    FileExplorer { align: center middle; background: $background 70%; }
    FileExplorer #file-explorer { width: 96%; height: 94%; border: none;
        background: $panel; padding: 1 2; }
    FileExplorer #file-title { color: $accent; text-style: bold; height: 1; }
    FileExplorer .file-row { height: 3; }
    FileExplorer Button { min-width: 8; margin-right: 1; }
    FileExplorer #file-address { width: 1fr; }
    FileExplorer #file-search { width: 1fr; }
    FileExplorer #file-type { width: 23; }
    FileExplorer #file-sort { width: 19; }
    FileExplorer #file-hidden { width: 13; border: none; }
    FileExplorer #file-operation { width: 30; }
    FileExplorer #file-main { height: 1fr; }
    FileExplorer #file-places { width: 17; margin-right: 1; }
    FileExplorer #file-places Button { width: 100%; height: 3; }
    FileExplorer #file-table { width: 1fr; height: 1fr; border: none; }
    FileExplorer #file-details { width: 31%; min-width: 24; padding-left: 2; }
    FileExplorer #file-info, FileExplorer #file-preview { height: auto; }
    FileExplorer #file-error { color: $error; height: auto; max-height: 3; }
    FileExplorer #file-notice { color: $text-muted; height: auto; max-height: 2; }
    FileExplorer #path { width: 1fr; }
    FileExplorer #file-format { width: 16; }
    FileExplorer #columns { width: 12; }
    FileExplorer #allow-lossy { width: 12; }
    FileExplorer #file-export-options Label { width: auto; margin: 1 1 0 1; }
    FileExplorer #file-actions { align-horizontal: right; }
    FileExplorer .file-hide { display: none; }
    FileExplorer.file-compact #file-details { display: none; }
    FileExplorer.file-compact #file-places { width: 14; }
    FileExplorer.file-compact #file-operation { width: 23; }
    FileExplorer.file-compact #file-type { width: 16; }
    FileExplorer.file-compact #file-sort { width: 15; }
    FileExplorer.file-compact #file-hidden { width: 11; }
    FileExplorer.file-compact #file-export-options Label:last-child { display: none; }
    """
    BINDINGS = [
        ("escape", "cancel", "Cancel"), ("alt+up", "up", "Up folder"),
        ("alt+left", "back", "Back"), ("alt+right", "forward", "Forward"),
        ("ctrl+l", "address", "Folder address"), ("ctrl+f", "search", "Search"),
        ("ctrl+shift+n", "new_folder", "New folder"), ("f5", "refresh", "Refresh"),
    ]

    def __init__(self, operation="open", start_path=None, default_name="", *, config=None,
                 extensions=None, must_exist=None, title=None, allow_operations=False,
                 validator=None, project_dir=None):
        super().__init__()
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown file operation: {operation}.")
        self.operation = operation
        self.config, self.validator = config or RuntimeConfig.load(), validator
        self.custom_extensions = tuple(str(item).lower() for item in extensions) if extensions else None
        self.must_exist = must_exist
        self.title_text = title
        self.allow_operations = allow_operations
        self.project_dir = Path(project_dir or project_roots()[0]).expanduser().resolve()
        self.selected_path = None
        self.entries = []
        self._row_paths = {}
        self._refreshing = False
        self._mounted_ready = False
        self.notice = ""
        base = Path(start_path).expanduser() if start_path is not None else (
            default_export_directory(self.config) if operation in ("export", "debug", "directory") else Path.home())
        base = base.resolve()
        filename = str(default_name)
        if base.is_file() or (not base.is_dir() and base.suffix and not default_name and operation != "directory"):
            filename = filename or base.name
            intended_directory = base.parent
        else:
            intended_directory = base
        self.current_directory = nearest_directory(intended_directory)
        self._selection_directory = intended_directory
        if self.current_directory != intended_directory:
            self.notice = f"Destination folder does not exist: {intended_directory}. Browse an existing folder or create folders explicitly."
            if filename:
                filename = str(intended_directory / filename)
        self.initial_filename = filename
        self.history = [self.current_directory]
        self.history_index = 0
        # The OS default is independent of a user-selected export folder.
        pictures = pictures_directory()
        self.places = {"Home": Path.home(), "Pictures": pictures, "Project": self.project_dir}
        try:
            self.places["Exports"] = default_export_directory(config)
        except ValueError:
            pass
        if os.name == "nt":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = Path(f"{letter}:\\")
                if drive.exists():
                    self.places[f"{letter}: drive"] = drive
        else:
            self.places["Filesystem"] = Path("/")
            # These are ordinary browsable roots; mounted media can be found
            # here without querying mount tools or requiring platform packages.
            for label, directory in (("Volumes", "/Volumes"), ("Media", "/media"),
                                     ("User media", f"/run/media/{Path.home().name}"), ("Mounts", "/mnt")):
                location = Path(directory)
                if location.is_dir():
                    self.places[label] = location

    def _extensions(self):
        return self.custom_extensions or EXTENSIONS[self.operation]

    def _type_options(self):
        if self.operation == "directory":
            return [("Folders", "supported")]
        options = [("Supported files", "supported")]
        options.extend((f"{item[1:].upper()} files", item) for item in self._extensions())
        if self.operation in READ_OPERATIONS:
            options.append(("All files", "all"))
        return options

    def compose(self) -> ComposeResult:
        with Vertical(id="file-explorer"):
            yield Label(self.title_text or OPERATIONS[self.operation], id="file-title")
            with Horizontal(classes="file-row"):
                yield Button("Back", id="file-back")
                yield Button("Forward", id="file-forward")
                yield Button("Up", id="file-up")
                yield Input(str(self.current_directory), placeholder="Folder address", id="file-address")
                yield Button("Go", id="file-go")
                yield Button("Refresh", id="file-refresh")
            with Horizontal(classes="file-row"):
                if self.allow_operations:
                    yield Select([(label, key) for key, label in OPERATIONS.items()],
                                 value=self.operation, allow_blank=False, id="file-operation")
                yield Input(placeholder="Filter files by name", id="file-search")
                yield Select(self._type_options(), value="supported", allow_blank=False, id="file-type")
                yield Select([("Name A–Z", "name"), ("Newest first", "modified"),
                              ("Largest first", "size"), ("File type", "type")],
                             value="name", allow_blank=False, id="file-sort")
                yield Checkbox("Hidden", id="file-hidden")
            with Horizontal(id="file-main"):
                with VerticalScroll(id="file-places"):
                    for index, label in enumerate(self.places):
                        yield Button(label, id=f"file-place-{index}")
                yield DataTable(id="file-table", cursor_type="row", zebra_stripes=False)
                with VerticalScroll(id="file-details"):
                    yield Static(Text("Select a file for its details and preview."), id="file-info")
                    yield Static(Text(), id="file-preview")
            yield Static(Text(self.notice), id="file-notice")
            yield Static(Text(), id="file-error")
            with Horizontal(classes="file-row"):
                yield Input(self.initial_filename, placeholder="Filename or full path", id="path")
                yield Select([(suffix[1:].upper(), suffix) for suffix in EXTENSIONS["export"]],
                             value=".png", allow_blank=False, id="file-format")
            with Horizontal(id="file-export-options", classes="file-row"):
                yield Label("Text columns")
                yield Input("100", id="columns")
                yield Label("Allow lossy")
                yield Select([("No", "no"), ("Yes", "yes")], value="no", allow_blank=False, id="allow-lossy")
                yield Label("Images retain their original pixel dimensions.")
            with Horizontal(id="file-actions", classes="file-row"):
                yield Button("New folder", id="file-new-folder")
                yield Button("Select", variant="primary", id="file-submit")
                yield Button("Cancel", id="file-cancel")

    def on_mount(self):
        table = self.query_one("#file-table", DataTable)
        table.add_columns("Name", "Type", "Size", "Modified")
        self._mounted_ready = True
        self.set_class(self.app.size.width < 110, "file-compact")
        self._update_operation()
        self._sync_format()
        self.refresh_files()
        self.query_one("#path", Input).focus()

    def on_resize(self, event):
        self.set_class(event.size.width < 110, "file-compact")
        if self._mounted_ready and self.selected_path is not None:
            self.call_after_refresh(self._resize_preview)

    def _resize_preview(self):
        if self.is_mounted and self.selected_path is not None:
            self.select_file(self.selected_path, set_filename=False)

    def error(self, message=""):
        self.query_one("#file-error", Static).update(Text(literal(message)))

    def _update_operation(self):
        self.query_one("#file-title", Label).update(Text(self.title_text or OPERATIONS[self.operation]))
        self.query_one("#file-submit", Button).label = "Select folder" if self.operation == "directory" else self.operation.capitalize()
        self.query_one("#path", Input).placeholder = "Folder path, or leave blank to select the current folder" if self.operation == "directory" else "Filename or full path"
        for selector in ("#file-format", "#file-export-options"):
            self.query_one(selector).set_class(self.operation != "export", "file-hide")
        types = self.query_one("#file-type", Select)
        types.set_options(self._type_options())
        types.value = "supported"

    def navigate(self, path, *, record=True):
        try:
            candidate = Path(os.path.expandvars(str(path))).expanduser()
            if not candidate.is_absolute():
                candidate = self.current_directory / candidate
            candidate = candidate.resolve()
            if not candidate.is_dir():
                raise ValueError("That folder does not exist. Use New folder to create a folder explicitly.")
            # Detect denied access before changing history or the active folder.
            with os.scandir(candidate) as iterator:
                next(iterator, None)
            if record and candidate != self.current_directory:
                self.history = self.history[:self.history_index + 1]
                self.history.append(candidate)
                self.history_index += 1
            self.current_directory = candidate
            self._selection_directory = candidate
            self.selected_path = None
            self.query_one("#file-address", Input).value = str(candidate)
            field = self.query_one("#path", Input)
            field.value = Path(field.value).name if self.operation not in READ_OPERATIONS and field.value else ""
            self.error()
            self.refresh_files()
            return True
        except (OSError, ValueError) as error:
            self.error(error)
            return False

    def refresh_files(self):
        if not self._mounted_ready:
            return
        table = self.query_one("#file-table", DataTable)
        self._refreshing = True
        table.clear()
        self._row_paths = {}
        self.entries = []
        query = self.query_one("#file-search", Input).value.casefold()
        file_type = self.query_one("#file-type", Select).value
        sort_by = self.query_one("#file-sort", Select).value
        hidden = self.query_one("#file-hidden", Checkbox).value
        rows = []
        denied = 0
        truncated = False
        try:
            with os.scandir(self.current_directory) as iterator:
                for index, item in enumerate(iterator):
                    if index >= MAX_ENTRIES:
                        truncated = True
                        break
                    try:
                        details = item.stat()
                        is_directory = item.is_dir()
                        is_hidden = item.name.startswith(".") or bool(getattr(details, "st_file_attributes", 0) & 2)
                        if (is_hidden and not hidden) or query not in item.name.casefold():
                            continue
                        suffix = Path(item.name).suffix.lower()
                        if not is_directory and file_type != "all":
                            accepted = self._extensions() if file_type == "supported" else (str(file_type),)
                            if FORMAT_ALIASES.get(suffix, suffix) not in tuple(FORMAT_ALIASES.get(item, item) for item in accepted):
                                continue
                        rows.append((Path(item.path), is_directory, details, suffix))
                    except OSError:
                        denied += 1
            def order(row):
                path, is_directory, details, suffix = row
                primary = {"name": path.name.casefold(), "type": suffix,
                           "modified": -details.st_mtime, "size": -details.st_size}.get(sort_by, path.name.casefold())
                return not is_directory, primary, path.name.casefold()
            rows.sort(key=order)
            for index, (path, is_directory, details, suffix) in enumerate(rows):
                key = str(index)
                table.add_row(Text(literal(path.name)), "Folder" if is_directory else suffix[1:].upper() or "File",
                              "—" if is_directory else readable_size(details.st_size),
                              f"{datetime.fromtimestamp(details.st_mtime):%Y-%m-%d %H:%M}", key=key)
                self._row_paths[key] = path
                self.entries.append(path)
            notice = f"{len(rows)} items · Enter opens folders; choose files with the filename field or Select."
            if truncated:
                notice += f" Listing limited to {MAX_ENTRIES} entries."
            if denied:
                notice += f" {denied} inaccessible items omitted."
            if self.notice:
                notice = self.notice
                self.notice = ""
            self.query_one("#file-notice", Static).update(Text(literal(notice)))
        except (OSError, ValueError, OverflowError) as error:
            self.error(error)
        finally:
            self._refreshing = False
        self.query_one("#file-back", Button).disabled = self.history_index == 0
        self.query_one("#file-forward", Button).disabled = self.history_index == len(self.history) - 1
        self.query_one("#file-up", Button).disabled = self.current_directory == self.current_directory.parent
        if not rows:
            self.query_one("#file-info", Static).update(Text("No matching files."))
            self.query_one("#file-preview", Static).update(Text())

    def select_file(self, path, *, set_filename=True):
        self.selected_path = Path(path)
        if set_filename and not self.selected_path.is_dir():
            self._selection_directory = self.current_directory
            self.query_one("#path", Input).value = self.selected_path.name
            self._sync_format()
        preview_widget = self.query_one("#file-preview", Static)
        width = preview_widget.content_size.width or 22
        information, preview = file_preview(self.selected_path, max_width=width)
        self.query_one("#file-info", Static).update(information)
        preview_widget.update(preview)

    def on_data_table_row_highlighted(self, event):
        if event.data_table.id == "file-table" and not self._refreshing:
            path = self._row_paths.get(event.row_key.value)
            if path is not None:
                self.select_file(path, set_filename=False)

    def on_data_table_row_selected(self, event):
        if event.data_table.id != "file-table":
            return
        event.stop()
        path = self._row_paths.get(event.row_key.value)
        if path is not None:
            self.select_file(path)
            if path.is_dir():
                self.navigate(path)
            else:
                self.query_one("#path", Input).focus()

    def on_input_changed(self, event):
        if self._mounted_ready and event.input.id == "file-search":
            self.refresh_files()
        elif self._mounted_ready and event.input.id == "path":
            self._sync_format()

    def _sync_format(self):
        """Reflect known suffixes without canonicalizing the entered filename."""
        if self.operation != "export":
            return
        suffix = Path(self.query_one("#path", Input).value).suffix.lower()
        canonical = FORMAT_ALIASES.get(suffix, suffix)
        selector = self.query_one("#file-format", Select)
        if canonical in EXTENSIONS["export"] and selector.value != canonical:
            selector.value = canonical

    def on_select_changed(self, event):
        if not self._mounted_ready or event.value is Select.BLANK or event.value != event.select.value:
            return
        if event.select.id == "file-operation":
            if event.value == self.operation:
                return
            previous = self.operation
            self.operation = str(event.value)
            self._update_operation()
            field = self.query_one("#path", Input)
            if self.operation in DEFAULT_SUFFIX:
                filename = Path(field.value).name if field.value else ("sparker-debug.json" if self.operation == "debug" else "Untitled" + DEFAULT_SUFFIX[self.operation])
                filename = str(Path(filename).with_suffix(DEFAULT_SUFFIX[self.operation]))
                if self.operation in ("export", "debug") and previous not in ("export", "debug"):
                    destination = default_export_directory(self.config)
                    ancestor = nearest_directory(destination)
                    if ancestor != self.current_directory:
                        self.history = self.history[:self.history_index + 1]
                        self.history.append(ancestor)
                        self.history_index += 1
                    self.current_directory = ancestor
                    self._selection_directory = destination
                    self.query_one("#file-address", Input).value = str(self.current_directory)
                    field.value = str(destination / filename) if destination != self.current_directory else filename
                else:
                    field.value = filename
            elif self.operation == "directory":
                field.value = ""
            self.error()
            self.refresh_files()
        elif event.select.id in ("file-sort", "file-type"):
            self.refresh_files()
        elif event.select.id == "file-format":
            if self.operation != "export":
                return
            field = self.query_one("#path", Input)
            suffix = Path(field.value).suffix.lower()
            if field.value and FORMAT_ALIASES.get(suffix, suffix) != event.value:
                field.value = str(Path(field.value).with_suffix(str(event.value)))

    def on_checkbox_changed(self, event):
        if self._mounted_ready and event.checkbox.id == "file-hidden":
            self.refresh_files()

    def on_input_submitted(self, event):
        event.stop()
        if event.input.id == "file-address":
            self.navigate(event.value)
        elif event.input.id == "file-search":
            self.query_one("#file-table", DataTable).focus()
        else:
            self.submit()

    def on_button_pressed(self, event):
        key = event.button.id
        if key == "file-submit": self.submit()
        elif key == "file-cancel": self.dismiss(None)
        elif key == "file-up": self.action_up()
        elif key == "file-back": self.action_back()
        elif key == "file-forward": self.action_forward()
        elif key == "file-refresh": self.action_refresh()
        elif key == "file-go": self.navigate(self.query_one("#file-address", Input).value)
        elif key == "file-new-folder": self.action_new_folder()
        elif key and key.startswith("file-place-"):
            destination = list(self.places.values())[int(key.removeprefix("file-place-"))]
            self.navigate(destination)

    def validate_choice(self, choice):
        path = choice.path
        if choice.operation == "directory":
            if not path.is_dir():
                raise ValueError("Choose an existing folder, or create a folder explicitly with New folder.")
            validate_export_directory(path)
            if path.is_relative_to(self.project_dir):
                raise ValueError("Exports cannot be placed in the program/project folder.")
            return choice
        if path.is_dir():
            raise ValueError("Choose a file, or open this folder with the folder address.")
        must_exist = self.must_exist if self.must_exist is not None else choice.operation in READ_OPERATIONS
        if must_exist and not path.is_file():
            raise ValueError("The selected file does not exist.")
        if not must_exist and not path.parent.is_dir() and choice.operation not in ("export", "debug"):
            raise ValueError("The destination folder does not exist. Create it explicitly with New folder.")
        writable_parent = path.parent if path.parent.is_dir() else nearest_directory(path.parent)
        if not must_exist and not os.access(writable_parent, os.W_OK):
            raise ValueError("The destination folder is not writable.")
        suffix = path.suffix.lower()
        if choice.operation == "save" and suffix != ".tart":
            raise ValueError("Native project files use the .tart extension.")
        if choice.operation == "debug" and suffix != ".json":
            raise ValueError("Debug reports use the .json extension.")
        if choice.operation == "export":
            if suffix not in (*EXTENSIONS["export"], ".tif", ".jpeg"):
                raise ValueError("Choose PNG, TIFF, WebP, TXT, ANSI, or explicitly allowed JPEG, GIF, BMP.")
            strict = self.config.get("export.lossless", True)
            if suffix in (".jpg", ".jpeg", ".gif", ".bmp") and strict and not choice.allow_lossy:
                raise ValueError("This format cannot preserve every RGBA pixel. Choose PNG, TIFF or WebP, or explicitly allow lossy export.")
        if choice.operation in ("export", "debug"):
            validate_export_directory(path)
            if path.is_relative_to(self.project_dir):
                raise ValueError("Exports cannot be placed in the program/project folder.")
        if must_exist:
            with path.open("rb"):
                pass
        return choice

    def submit(self):
        try:
            raw = self.query_one("#path", Input).value.strip()
            if self.operation == "directory":
                directory = Path(os.path.expandvars(raw)).expanduser() if raw else self.current_directory
                if not directory.is_absolute():
                    directory = self.current_directory / directory
                choice = self.validate_choice(FileChoice(directory.resolve(), "directory"))
                if self.validator:
                    choice = self.validator(choice) or choice
                if not isinstance(choice, FileChoice):
                    raise ValueError("The file selection validator returned an invalid selection.")
                self.dismiss(choice)
                return
            if not raw:
                if self.selected_path is not None and self.selected_path.is_dir():
                    self.navigate(self.selected_path)
                    return
                if self.selected_path is not None and self.selected_path.is_file():
                    raw = str(self.selected_path)
                else:
                    raise ValueError("Enter or select a filename.")
            path = Path(os.path.expandvars(raw)).expanduser()
            if not path.is_absolute():
                path = self._selection_directory / path
            path = path.resolve()
            if path.is_dir():
                self.navigate(path)
                return
            if not path.suffix and self.operation in DEFAULT_SUFFIX:
                suffix = str(self.query_one("#file-format", Select).value) if self.operation == "export" else DEFAULT_SUFFIX[self.operation]
                path = path.with_suffix(suffix)
            columns, allow_lossy = 100, False
            if self.operation == "export":
                try:
                    columns = int(self.query_one("#columns", Input).value)
                except ValueError as error:
                    raise ValueError("Text columns must be an integer in 1..500.") from error
                if not 1 <= columns <= 500:
                    raise ValueError("Text columns must be in 1..500.")
                allow_lossy = self.query_one("#allow-lossy", Select).value == "yes"
            choice = FileChoice(path, self.operation, columns, allow_lossy)
            choice = self.validate_choice(choice)
            if self.validator:
                choice = self.validator(choice) or choice
            if not isinstance(choice, FileChoice):
                raise ValueError("The file selection validator returned an invalid selection.")
            self.dismiss(choice)
        except (ValueError, OSError, OverflowError) as error:
            self.error(error)

    def create_folder(self, name):
        name = folder_name(name)
        destination = self.current_directory / name
        destination.mkdir(exist_ok=False)
        return destination

    def action_new_folder(self):
        def validate(values):
            return self.create_folder(values["folder-name"])
        def finished(destination):
            if destination is not None:
                self.navigate(destination)
        self.app.push_screen(_FolderForm("New folder", [("folder-name", "Folder name", "", None)], validate,
                                  note=Text(f"Create one folder in {literal(self.current_directory)}")), finished)

    def action_up(self): self.navigate(self.current_directory.parent)
    def action_back(self):
        if self.history_index > 0 and self.navigate(self.history[self.history_index - 1], record=False):
            self.history_index -= 1
            self.refresh_files()
    def action_forward(self):
        if self.history_index + 1 < len(self.history) and self.navigate(self.history[self.history_index + 1], record=False):
            self.history_index += 1
            self.refresh_files()
    def action_address(self): self.query_one("#file-address", Input).focus()
    def action_search(self): self.query_one("#file-search", Input).focus()
    def action_refresh(self):
        self.error()
        self.refresh_files()
    def action_cancel(self): self.dismiss(None)
