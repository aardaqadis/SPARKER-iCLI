"""Terminal studio: menus, dialogs, tools, layers, palette and history."""
from __future__ import annotations

from pathlib import Path
import os
import time
import json
from rich.text import Text
from PIL import Image, ImageColor, ImageFilter, ImageOps
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, HorizontalScroll
from textual.widgets import Button, Footer, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option
from textual.theme import Theme

from .canvas import Canvas
from .config import RuntimeConfig
from .dialogs import Confirm, Form, Menu
from .model import BLENDS, Document, Layer, MAX_LAYERS, valid_size
from .storage import export, export_dimensions, import_document, load_project, open_image, project_roots, resolve_export_path, save_project

TOOLS = [("brush", "B  Brush"), ("pencil", "P  Pencil"), ("eraser", "E  Eraser"),
         ("fill", "F  Fill"), ("gradient", "G  Gradient"), ("text", "T  Text"),
         ("line", "L  Line"), ("rectangle", "R  Rectangle"), ("ellipse", "O  Ellipse"),
         ("select_rect", "S  Select"), ("select_ellipse", "   Select oval"),
         ("lasso", "   Lasso"), ("wand", "W  Magic wand"), ("picker", "I  Picker"),
         ("move", "M  Move"), ("hand", "H  Pan")]
TOOLS.append(("library", "   Library tool"))
TOOLS.extend((name, "   " + label) for name, label in (
    ("clone", "Clone"), ("heal", "Heal"), ("perspective-clone", "Perspective clone"),
    ("smudge", "Smudge"), ("blur", "Blur brush"), ("sharpen", "Sharpen brush"),
    ("dodge", "Dodge"), ("burn", "Burn"), ("airbrush", "Airbrush"),
    ("ink", "Calligraphy"), ("natural", "Natural media"),
    ("select_color", "Select by color"), ("scissors", "Scissors"), ("foreground", "Foreground")))
MENUS = {
    "File": [("new", "New canvas                 Ctrl+N"), ("open", "Open project / image       Ctrl+O"),
             ("save", "Save project               Ctrl+S"), ("save_as", "Save project as…"),
             ("import", "Import image as layer…"), ("export", "Export image / text…       Ctrl+E"),
             ("files", "File explorer                 F6"), ("run_script", "Run painting script…"),
             ("export_debug", "Export debug report…"), ("export_folder", "Choose export folder…"),
             ("quit", "Quit                       Ctrl+Q")],
    "Edit": [("undo", "Undo                       Ctrl+Z"), ("redo", "Redo                       Ctrl+Y"),
             ("copy", "Copy active layer selection Ctrl+C"), ("cut", "Cut selection              Ctrl+X"),
             ("paste", "Paste as layer             Ctrl+V"), ("clear", "Clear selection / layer    Delete"),
             ("brush_options", "Tool settings…"), ("colors", "Foreground / background colors…")],
    "Select": [("all", "Select all                 Ctrl+A"), ("none", "Deselect                   Ctrl+D"),
               ("invert_selection", "Invert selection"), ("feather", "Feather selection…"),
               ("selection_mode", "Selection combine mode…"), ("crop", "Crop canvas to selection")],
    "Layers": [("add_layer", "New transparent layer…"), ("duplicate", "Duplicate active layer"),
               ("layer_properties", "Layer properties…"), ("visibility", "Toggle visibility"),
               ("lock", "Toggle pixel lock"), ("raise", "Raise layer"), ("lower", "Lower layer"),
               ("merge", "Merge down"), ("flatten", "Flatten visible layers"),
               ("mask_selection", "Layer mask from selection"), ("mask_invert", "Invert layer mask"),
               ("mask_apply", "Apply layer mask"), ("mask_remove", "Remove layer mask"),
               ("delete_layer", "Delete active layer")],
    "Image": [("resize", "Scale image…"), ("canvas_size", "Canvas size (top-left anchor)…"),
              ("scale_layer", "Scale active layer…"), ("rotate_cw", "Rotate layer 90° clockwise"),
              ("rotate_ccw", "Rotate layer 90° counterclockwise"), ("rotate", "Rotate active layer by angle…"),
              ("flip_h", "Flip layer horizontally"), ("flip_v", "Flip layer vertically")],
    "Tools": [("tool_library", "Browse art tool library         F5"),
              ("library_options", "Library size / angle / density…"),
              ("apply_library", "Apply selected library tool"),
              ("library_categories", "List categories in CLI")],
    "Filters": [(name, label) for name, label in [
        ("brightness", "Brightness…"), ("contrast", "Contrast…"), ("saturation", "Saturation…"),
        ("autocontrast", "Auto contrast"), ("invert", "Invert colors"), ("grayscale", "Grayscale"),
        ("sepia", "Sepia"), ("blur", "Gaussian blur…"), ("sharpen", "Unsharp mask…"),
        ("edges", "Find edges"), ("emboss", "Emboss"), ("posterize", "Posterize…"), ("threshold", "Threshold…")]],
    "View": [("fit", "Fit canvas                 0"), ("actual", "Actual pixels              1"),
             ("zoom_in", "Zoom in                    +"), ("zoom_out", "Zoom out                   -"),
             ("grid", "Toggle grid"), ("guides", "Toggle guides"), ("guide_options", "Grid / guide positions…"),
             ("palette", "Edit palette…"), ("console", "Command console             F3"),
             ("panels", "Toggle tool / layer panels  F4"), ("history", "Toggle history"),
             ("ruler", "Toggle coordinate ruler"), ("palette_view", "Toggle palette")],
    "Help": [("help", "Controls and commands"), ("debug_info", "Runtime debug information"),
             ("runtime_settings", "Runtime settings and variables"), ("about", "About SPARKER iCLI")],
}
MENUS["Tools"].extend([
    ("retouch_options", "Painting / retouch options…"),
    ("geometry_tools", "Advanced transforms…"), ("path_tools", "Editable paths…"),
    ("channel_tools", "Saved channels…"), ("features", "Feature families in CLI"),
    ("ai_settings", "AI / MCP connections…")])
MENUS["Filters"].extend([( "tone_tools", "Tone controls…"),
    ("native_effects", "Native effects…"), ("effect_stack", "Editable effect stack…")])
MENUS["Select"].extend([( "tool_scissors", "Scissors outline tool"),
    ("tool_foreground", "Foreground outline tool"), ("tool_select_color", "Select by color tool")])


class CommandInput(Input):
    """Command recall stays local, without triggering canvas pan shortcuts."""
    BINDINGS = [Binding("up", "previous", "Previous", show=False), Binding("down", "next", "Next", show=False),
                Binding("escape", "close", "Close", show=False), Binding("ctrl+space", "complete", "Complete", show=False)]

    def action_previous(self): self.app.recall_command(-1)
    def action_next(self): self.app.recall_command(1)
    def action_close(self): self.app.action_console()
    def action_complete(self): self.app.complete_command()


class Studio(App):
    TITLE = "SPARKER iCLI"
    CSS = """
    Screen { background: #090909; color: #d7d7d7; }
    Menu, Form, Confirm { background: #090909 40%; }
    Menu > Vertical, Form > Vertical, Confirm > Vertical { background: #161616; border: none; }
    Menu Label, Form #form-title { color: #e79335; }
    Menu OptionList { border: none; padding: 0; background: #161616; }
    Menu OptionList:focus { border: none; }
    Input, Select { border: none; background: #222222; }
    Input:focus { border: none; background: #292929; }
    Form Input, Form Select { height: 1; padding: 0 1; }
    Form Select SelectCurrent { border: none; height: 1; padding: 0; }
    Button { border: none; background: #222222; }
    Button:hover { background: #333333; }
    #menubar { height: 1; background: #121212; }
    #menubar Button { min-width: 7; width: auto; height: 1; padding: 0 1; border: none; background: #121212; }
    #menubar Button:hover, #menubar Button:focus { background: #292929; }
    #brand { width: 1fr; content-align: right middle; padding-right: 1; color: #e79335; text-style: bold; }
    #controls { height: 1; margin-top: 1; margin-bottom: 1; background: #090909; }
    #controls Label { width: auto; padding: 0 1; color: #888888; }
    #controls Input { width: 6; height: 1; padding: 0 1; border: none; }
    #controls #fg { width: 12; }
    #controls Button { min-width: 7; width: auto; height: 1; padding: 0 1; margin-left: 1; }
    #workspace { height: 1fr; }
    #tools { width: 18; padding: 0 1; background: #090909; }
    #tools Label, #sidebar Label { color: #777777; height: 1; margin-bottom: 1; }
    #tool-list { height: 1fr; border: none; padding: 0; background: #090909; }
    #tool-list:focus, #layer-list:focus, #history:focus { border: none; }
    #tool-tip { display: none; }
    #center { width: 1fr; }
    Canvas { background: #090909; }
    #ruler { height: 1; display: none; color: #777777; background: #090909; }
    #sidebar { width: 25; padding: 0 1; background: #090909; }
    #layer-list { height: 1fr; min-height: 4; border: none; padding: 0; background: #090909; }
    #layer-info { height: 2; margin-top: 1; color: #777777; }
    #layer-buttons { height: 1; margin-top: 1; }
    #layer-buttons Button { width: 1fr; height: 1; min-width: 3; padding: 0; }
    #history-label, #history { display: none; }
    #history { height: 1fr; min-height: 3; border: none; padding: 0; background: #090909; }
    #palette { height: 1; margin-top: 1; background: #090909; }
    #palette Button { height: 1; width: 3; min-width: 3; padding: 0; border: none; margin-right: 1; }
    #status { height: 1; margin-top: 1; background: #121212; color: #999999; padding: 0 1; }
    Footer { background: #090909; color: #777777; }
    FooterKey .footer-key--key { color: #aaaaaa; background: #090909; text-style: none; }
    FooterKey .footer-key--description { color: #777777; background: #090909; }
    #console { display: none; height: 7; margin-top: 1; background: #121212; padding: 0 1; }
    #command-output { height: 1fr; background: #121212; padding: 0; }
    #commandbar { height: 1; }
    #command-prompt { width: 2; color: #e79335; }
    #command-line { width: 1fr; height: 1; border: none; padding: 0; background: #121212; }
    #command-line:focus { background: #121212; }
    .compact #sidebar { width: 21; }
    .compact #tools { width: 17; }
    .zen #tools, .zen #sidebar { display: none; }
    .show-history #history-label, .show-history #history { display: block; }
    .show-ruler #ruler { display: block; }
    .hide-palette #palette { display: none; }
    .show-console #console { display: block; }
    """
    BINDINGS = [
        Binding("ctrl+n", "new", "New", show=False), Binding("ctrl+o", "open", "Open", show=False),
        Binding("ctrl+s", "save", "Save"), Binding("ctrl+e", "export", "Export"),
        Binding("ctrl+z", "undo", "Undo"), Binding("ctrl+y", "redo", "Redo"),
        Binding("ctrl+q", "quit", "Quit", priority=True), Binding("f1", "help", "Help"),
        Binding("f2", "menu('File')", "Menu"),
        Binding("f3,colon", "console", "Command"), Binding("f4", "panels", "Panels", show=False),
        Binding("f5", "tool_library", "Tools", show=False),
        Binding("f6", "files", "Files"),
        Binding("ctrl+c", "copy", "Copy", show=False), Binding("ctrl+x", "cut", "Cut", show=False),
        Binding("ctrl+v", "paste", "Paste", show=False), Binding("ctrl+a", "all", "Select all", show=False),
        Binding("ctrl+d", "none", "Deselect", show=False), Binding("delete", "clear", "Clear", show=False),
        *[Binding(key, f"tool('{tool}')", label, show=False) for key, tool, label in [
            ("b", "brush", "Brush"), ("p", "pencil", "Pencil"), ("e", "eraser", "Eraser"),
            ("f", "fill", "Fill"), ("g", "gradient", "Gradient"), ("t", "text", "Text"),
            ("l", "line", "Line"), ("r", "rectangle", "Rect"), ("o", "ellipse", "Ellipse"),
            ("s", "select_rect", "Select"), ("w", "wand", "Wand"), ("i", "picker", "Picker"),
            ("m", "move", "Move"), ("h", "hand", "Pan")]],
        Binding("0", "fit", "Fit", show=False), Binding("1", "actual", "Actual", show=False),
        Binding("plus,equal", "zoom_in", "Zoom+", show=False), Binding("minus", "zoom_out", "Zoom-", show=False),
        Binding("left_square_bracket", "smaller", "Smaller", show=False),
        Binding("right_square_bracket", "larger", "Larger", show=False),
        Binding("x", "swap_colors", "Swap colors", show=False),
        Binding("left", "pan(-8,0)", "Pan", show=False), Binding("right", "pan(8,0)", "Pan", show=False),
        Binding("up", "pan(0,-8)", "Pan", show=False), Binding("down", "pan(0,8)", "Pan", show=False),
    ]

    def __init__(self, doc=None, path=None, config=None):
        super().__init__()
        self.register_theme(Theme(name="sparker", primary="#e79335", secondary="#999999", accent="#e79335",
                                  foreground="#d7d7d7", background="#090909", surface="#121212", panel="#161616",
                                  success="#93a98f", warning="#e79335", error="#ce7972", dark=True))
        self.theme = "sparker"
        self.config = config if config is not None else RuntimeConfig.load()
        self.doc = doc or Document()
        self.project_path = Path(path).resolve() if path else None
        self.tool = "brush"
        self.library_tool = None
        for key in ("size", "angle", "seed", "density", "amount"):
            setattr(self, "library_"+key, self.config.get("library."+key))
        self._cli_screen = None
        self.foreground = "#e79335"
        self.background = "#ffffff"
        self.brush_size = self.config.get("brush.size")
        self.hardness = self.config.get("brush.hardness")
        self.opacity = self.config.get("brush.opacity")
        self.tolerance = self.config.get("fill.tolerance")
        self.filled = False
        self.radial = False
        self.selection_mode = "replace"
        self.syncing = False
        self.command_history = []
        self.command_cursor = 0
        self._debug_last = 0.0
        self._status_text = self._ruler_text = None
        self.apply_runtime_settings()
        self.load_settings()

    def apply_runtime_settings(self):
        self.doc.history_limit = self.config.get("history.max_steps")
        self.doc.history_bytes = self.config.get("history.max_mb") * 1024 * 1024
        self.doc.history_storage = self.config.get("history.storage")
        self.doc.cache_effects = self.config.get("memory.mode") != "low"
        for layer in self.doc.layers:
            layer.cache_effects = self.config.get("memory.mode") != "low"
            layer.invalidate_effects()
        self.doc._trim()

    def publish_debug(self, force=False):
        if self._cli_screen is not None and self._cli_screen.remote_busy: return
        if not os.environ.get("SPARKER_DEBUG_STATE"):
            return
        now = time.monotonic()
        if not force and now - self._debug_last < self.config.get("debug.refresh_ms") / 1000:
            return
        self._debug_last = now
        from .diagnostics import collect_diagnostics
        from .startup import write_debug_snapshot
        data = collect_diagnostics(self.doc, self.config)
        if self.is_mounted:
            canvas = self.screen_stack[0].query_one(Canvas)
            data["terminal"].update(columns=self.size.width, rows=self.size.height)
            data["runtime"] = {"mode": "full-screen CLI" if self._cli_screen is not None else "terminal editor", "tool": self.library_tool or self.tool,
                               "brush_size": self.brush_size, "hardness": self.hardness,
                               "opacity": self.opacity, "fill_tolerance": self.tolerance,
                               "zoom": canvas.zoom, "pan_x": canvas.pan_x, "pan_y": canvas.pan_y,
                               "preview_resampling": self.config.get("view.resampling"),
                               "project_path": str(self.project_path) if self.project_path else "unsaved"}
        write_debug_snapshot({"diagnostics": data, "stage": "ready", "updated_at": time.time()})

    def load_settings(self):
        settings = self.doc.settings
        # Unknown native settings are preserved but never executed.
        for key in ("foreground", "background"):
            value = settings.get(key, getattr(self, key))
            try: ImageColor.getrgb(value)
            except (ValueError, TypeError): continue
            setattr(self, key, value)
        for key, low, high in (("brush_size", 1, 128), ("hardness", 0, 1), ("opacity", 0, 1), ("tolerance", 0, 255)):
            value = settings.get(key, getattr(self, key))
            if type(value) in (int, float) and low <= value <= high:
                setattr(self, key, int(value) if key in ("brush_size", "tolerance") else value)
        for key in ("filled", "radial"):
            if type(settings.get(key)) is bool: setattr(self, key, settings[key])
        if settings.get("tool") in {tool for tool, _ in TOOLS}: self.tool = settings["tool"]
        identifier = settings.get("library_tool")
        if isinstance(identifier, str) and identifier:
            from .tool_library import TOOLS as catalog
            self.library_tool = identifier if identifier in catalog else None
        else: self.library_tool = None
        if self.tool == "library" and not self.library_tool: self.tool = "brush"
        for key, low, high in (("size", 1, 512), ("angle", -360, 360), ("seed", 0, 2147483647), ("density", .1, 4), ("amount", 0, 4)):
            value = settings.get("library_"+key, self.config.get("library."+key))
            if type(value) in (int, float) and low <= value <= high:
                setattr(self, "library_"+key, int(value) if key in ("size", "seed") else value)
        if settings.get("selection_mode") in ("replace", "add", "subtract", "intersect"):
            self.selection_mode = settings["selection_mode"]

    def compose(self) -> ComposeResult:
        with Horizontal(id="menubar"):
            for index, name in enumerate(MENUS):
                yield Button(name, id=f"menu-{index}")
            yield Static("SPARKER iCLI", id="brand")
        with Horizontal(id="controls"):
            yield Label("Color")
            yield Input(self.foreground, id="fg")
            yield Label("Size")
            yield Input(str(self.brush_size), id="size")
            yield Label("Alpha %")
            yield Input(str(round(self.opacity*100)), id="opacity")
            yield Button("Options", id="options")
            yield Button("Fit", id="fit")
        with Horizontal(id="workspace"):
            with Vertical(id="tools"):
                yield Label("Tools")
                yield OptionList(*(Option(label, id=key) for key, label in TOOLS), id="tool-list")
                yield Static("Drag to paint\nWheel: zoom\nRight-drag: pan", id="tool-tip")
            with Vertical(id="center"):
                yield Static("", id="ruler")
                yield Canvas(id="canvas")
            with Vertical(id="sidebar"):
                yield Label("Layers")
                yield OptionList(id="layer-list")
                yield Static("", id="layer-info", markup=False)
                with Horizontal(id="layer-buttons"):
                    yield Button("+", id="layer-add")
                    yield Button("Props", id="layer-props")
                    yield Button("◉", id="layer-visible")
                yield Label("History", id="history-label")
                yield OptionList(id="history")
        with HorizontalScroll(id="palette"):
            for index in range(64):
                yield Button(" ", id=f"swatch-{index}")
        with Vertical(id="console"):
            yield RichLog(id="command-output", markup=False, highlight=False, wrap=True, max_lines=200)
            with Horizontal(id="commandbar"):
                yield Static("›", id="command-prompt")
                yield CommandInput(placeholder="help · command recall ↑↓ · Ctrl+Space complete", id="command-line")
        yield Static("Ready", id="status", markup=False)
        yield Footer()

    @property
    def canvas(self):
        return self.screen_stack[0].query_one(Canvas)

    def on_mount(self):
        self.sync_ui()
        self.set_interval(.1, self.publish_debug)
        self.call_after_refresh(self.action_fit)
        self.call_after_refresh(self.signal_started)
        self.canvas.focus()

    def signal_started(self):
        from .startup import signal_ready
        self.publish_debug(force=True)
        signal_ready()

    def on_resize(self, event):
        self.screen_stack[0].set_class(event.size.width < 100, "compact")

    def update_status(self, point=None):
        if not self.is_mounted: return
        canvas = self.canvas
        canvas.record_export_view()
        point = point or canvas.cursor
        location = f"  x:{point[0]} y:{point[1]}" if point else ""
        name = self.project_path.name if self.project_path else "Untitled.tart"
        status = (
            f"{'● ' if self.doc.dirty else ''}{name}  |  {self.library_tool if self.tool == 'library' else self.tool}  |  {self.doc.width}×{self.doc.height}  |  "
            f"{canvas.zoom*100:.0f}%{location}  |  {self.selection_mode}")
        ruler = (f"  Origin {canvas.pan_x:.0f},{canvas.pan_y:.0f}  ·  "
                 f"1 character = {1/canvas.zoom:.2f} × {2/canvas.zoom:.2f} pixels")
        # Pointer motion should update coordinates without relaying out unchanged
        # status/ruler widgets on every terminal mouse event.
        if status != self._status_text:
            self.screen_stack[0].query_one("#status", Static).update(status)
            self._status_text = status
        if ruler != self._ruler_text:
            self.screen_stack[0].query_one("#ruler", Static).update(ruler)
            self._ruler_text = ruler

    def sync_ui(self, *, artwork=True):
        if not self.is_mounted: return
        self.syncing = True
        self.canvas.invalidate(artwork=artwork)
        self.screen_stack[0].query_one("#fg", Input).value = self.foreground
        self.screen_stack[0].query_one("#size", Input).value = str(self.library_size if self.tool == "library" else self.brush_size)
        self.screen_stack[0].query_one("#opacity", Input).value = str(round(self.opacity*100))
        self.screen_stack[0].query_one("#tool-list", OptionList).highlighted = [x[0] for x in TOOLS].index(self.tool)
        layers = self.screen_stack[0].query_one("#layer-list", OptionList)
        layers.clear_options()
        for index in range(len(self.doc.layers)-1, -1, -1):
            layer = self.doc.layers[index]
            text = f"{'●' if layer.visible else '○'} {'▣' if layer.locked else ' '} {layer.name}"
            if layer.mask is not None: text += " [mask]"
            layers.add_option(Option(Text(text), id=f"layer-{index}"))
        layers.highlighted = len(self.doc.layers)-1-self.doc.active
        layer = self.doc.layer
        self.screen_stack[0].query_one("#layer-info", Static).update(f"{layer.opacity*100:.0f}% · {layer.blend}\n{'Locked' if layer.locked else 'Editable'} · {'Visible' if layer.visible else 'Hidden'}")
        history = self.screen_stack[0].query_one("#history", OptionList)
        history.clear_options()
        history.add_option(Option("Current state", id="current"))
        for count, (label, _) in enumerate(reversed(self.doc.undo_stack), 1):
            history.add_option(Option(f"↶ {label}", id=f"undo-{count}"))
        palette = self.doc.settings["palette"]
        for index in range(64):
            button = self.screen_stack[0].query_one(f"#swatch-{index}", Button)
            button.display = index < len(palette)
            if index < len(palette):
                button.styles.background = palette[index]
                button.tooltip = palette[index]
        self.update_status()
        self.syncing = False
        self.publish_debug(force=True)

    def on_button_pressed(self, event):
        key = event.button.id or ""
        if key.startswith("menu-"):
            self.action_menu(list(MENUS)[int(key.split("-")[1])])
        elif key.startswith("swatch-"):
            self.foreground = self.doc.settings["palette"][int(key.split("-")[1])]
            self.sync_ui()
        else:
            action = {"options": "brush_options", "fit": "fit", "layer-add": "add_layer",
                      "layer-props": "layer_properties", "layer-visible": "visibility"}.get(key)
            if action: self.dispatch(action)

    def on_option_list_option_selected(self, event):
        key = event.option.id
        if event.option_list.id == "tool-list": self.action_tool(key)
        elif event.option_list.id == "layer-list":
            self.doc.active = int(key.split("-")[1])
            self.sync_ui()
            self.canvas.focus()
        elif event.option_list.id == "history" and key.startswith("undo-"):
            for _ in range(int(key.split("-")[1])): self.doc.undo()
            self.sync_ui()

    def on_input_submitted(self, event):
        if event.input.id == "command-line":
            event.stop()
            command = event.value.strip()
            event.input.value = ""
            if command: self.submit_command(command)
            return
        try:
            if event.input.id == "fg":
                color = ImageColor.getrgb(event.value)
                self.foreground = "#%02x%02x%02x" % color[:3]
            elif event.input.id == "size":
                if self.tool == "library": self.library_size = self.number(event.value, 1, 512, int)
                else: self.brush_size = self.number(event.value, 1, 128, int)
            elif event.input.id == "opacity": self.opacity = self.number(event.value, 0, 100)/100
        except ValueError as error: self.notify(str(error), severity="warning")
        self.sync_ui()
        self.canvas.focus()

    def action_console(self):
        from .cli_app import CLIWorkspaceScreen
        from .commands import CommandSession
        if isinstance(self.screen, CLIWorkspaceScreen):
            self.screen.action_return()
            return
        if len(self.screen_stack) > 1: return
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        self.store_tool_settings()
        session = CommandSession(self.doc, self.project_path, config=self.config)
        self._cli_screen = CLIWorkspaceScreen(session, on_document_changed=self.cli_document_changed,
                                               on_exit=self.cli_return, on_view_command=self.view_command)
        self._cli_screen.command_history = self.command_history.copy()
        self._cli_screen.command_cursor = len(self.command_history)
        self.push_screen(self._cli_screen)

    def check_action(self, action, parameters):
        return not (self._cli_screen is not None and self._cli_screen.remote_busy)

    def store_tool_settings(self):
        if self.is_mounted:
            self.canvas.record_export_view()
        self.doc.settings.update({key: getattr(self, key) for key in
                                 ("foreground", "background", "brush_size", "hardness", "opacity", "tolerance",
                                  "filled", "radial", "selection_mode", "tool", "library_tool",
                                  "library_size", "library_angle", "library_seed", "library_density", "library_amount")})

    def cli_document_changed(self, session, result):
        replaced = self.doc is not session.document
        self.doc, self.project_path = session.document, session.project_path
        if result.changed or replaced:
            self.apply_runtime_settings()
            self.load_settings()
            self.sync_ui()
        else:
            self.update_status()
        if replaced: self.action_fit()

    def cli_return(self):
        if self._cli_screen is not None:
            self.command_history = self._cli_screen.command_history.copy()
            self.command_cursor = len(self.command_history)
            self.doc = self._cli_screen.document
            self.project_path = self._cli_screen.session.project_path
        self._cli_screen = None
        self.pop_screen()
        self.load_settings()
        self.sync_ui()
        self.canvas.focus()

    def recall_command(self, direction):
        self.command_cursor = max(0, min(len(self.command_history), self.command_cursor+direction))
        value = self.command_history[self.command_cursor] if self.command_cursor < len(self.command_history) else ""
        field = self.screen_stack[0].query_one("#command-line", CommandInput)
        field.value = value
        field.cursor_position = len(value)

    def complete_command(self):
        from .commands import HELP, syntax_completions
        field = self.screen_stack[0].query_one("#command-line", CommandInput)
        candidates = syntax_completions(field.value)
        if candidates is not None:
            if len(candidates) == 1:
                field.value = candidates[0] + " "
                field.cursor_position = len(field.value)
            elif candidates:
                self.screen_stack[0].query_one("#command-output", RichLog).write("  ".join(candidates))
            return
        words = (*HELP, "view")
        prefix = field.value.strip()
        matches = [word for word in words if word.startswith(prefix)]
        if len(matches) == 1:
            field.value = matches[0]+" "
            field.cursor_position = len(field.value)
        elif matches:
            self.screen_stack[0].query_one("#command-output", RichLog).write("  ".join(matches))

    def submit_command(self, line):
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        self.command_history.append(line)
        self.command_history = self.command_history[-200:]
        self.command_cursor = len(self.command_history)
        log = self.screen_stack[0].query_one("#command-output", RichLog)
        log.write(f"› {line}")
        first = line.split(maxsplit=1)[0].lower()
        if first in ("new", "open", "script") and self.doc.dirty:
            self.guard_document(lambda: self.execute_command(line))
        else: self.execute_command(line)

    def execute_command(self, line):
        if self._cli_screen is not None:
            if not self._cli_screen.is_mounted:
                self.call_after_refresh(lambda: self.execute_command(line))
                return
            self._cli_screen.execute_command(line)
            return
        from .commands import CommandError, CommandSession
        log = self.screen_stack[0].query_one("#command-output", RichLog)
        try:
            if line.strip().lower() == "help view":
                log.write(self.view_command("view"))
                return
            if line.split(maxsplit=1)[0].lower() == "view":
                log.write(self.view_command(line))
                return
            self.store_tool_settings()
            session = CommandSession(self.doc, self.project_path, config=self.config)
            result = session.execute(line)
            for key in ("foreground", "background", "brush_size", "hardness", "opacity", "tolerance", "tool"):
                setattr(self, key, session.setting(key))
            if result.document_replaced:
                self.replace_document(session.document, session.project_path)
            else:
                self.project_path = session.project_path
                self.apply_runtime_settings()
                self.load_settings()
                self.sync_ui()
            if result.text: log.write(result.text)
            if line.strip().lower() == "help":
                log.write("Editor viewport commands: type 'help view'.")
            if result.quit_requested: self.action_quit()
        except (CommandError, ValueError, OSError) as error:
            log.write(Text(f"Error: {error}", style="#ce7972"))
        if len(self.screen_stack) == 1:
            self.query_one("#command-line", CommandInput).focus()

    def view_command(self, line):
        parts = line.split()
        if len(parts) < 2:
            return "view fit | actual | zoom PERCENT | pan X Y | resampling nearest|bilinear|bicubic | grid | guides | panels | history | ruler | palette"
        key = parts[1].lower()
        actions = {"fit": self.action_fit, "actual": self.action_actual, "grid": self.action_grid,
                   "guides": self.action_guides, "panels": self.action_panels, "history": self.action_history,
                   "ruler": self.action_ruler, "palette": self.action_palette_view}
        if key == "zoom" and len(parts) == 3:
            percent = self.number(parts[2], self.config.get("view.zoom_min")*100, self.config.get("view.zoom_max")*100)
            self.canvas.zoom_at(percent/100/self.canvas.zoom)
        elif key == "pan" and len(parts) == 4:
            x, y = self.number(parts[2], -1_000_000, 1_000_000), self.number(parts[3], -1_000_000, 1_000_000)
            self.canvas.pan_x, self.canvas.pan_y = x, y
            self.canvas.invalidate(artwork=False)
            self.update_status()
        elif key == "resampling" and len(parts) == 3:
            self.config.set("view.resampling", parts[2])
            self.canvas.set_resampling(self.config.get("view.resampling"))
        elif key in actions and len(parts) == 2: actions[key]()
        else: raise ValueError("Use 'view' to see available view commands.")
        self.canvas.record_export_view()
        self.publish_debug(force=True)
        return f"View: {key}"

    def action_panels(self):
        self.screen_stack[0].toggle_class("zen")

    def action_history(self):
        self.screen_stack[0].toggle_class("show-history")

    def action_ruler(self):
        self.screen_stack[0].toggle_class("show-ruler")

    def action_palette_view(self):
        self.screen_stack[0].toggle_class("hide-palette")

    @staticmethod
    def number(value, low, high, kind=float):
        try: result = kind(value)
        except (ValueError, TypeError): raise ValueError(f"Enter a number from {low} to {high}.")
        if not low <= result <= high: raise ValueError(f"Enter a number from {low} to {high}.")
        return result

    def form(self, title, fields, callback, validate=None, note="", browse_fields=None):
        self.push_screen(Form(title, fields, validate, note, browse_fields), lambda value: callback(value) if value is not None else None)

    def perform(self, label, operation):
        if self.is_mounted and self.canvas.dragging:
            self.canvas.action_cancel_gesture()
        try:
            with self.doc.edit(label): operation()
        except (ValueError, OSError) as error:
            self.notify(str(error), severity="error")
        self.sync_ui()

    def dispatch(self, key):
        if key:
            if key.startswith("tool_"):
                self.action_tool(key[5:])
                return
            action = getattr(self, f"action_{key}", None)
            if action: action()
            elif key in {x[0] for x in MENUS["Filters"]}: self.filter_dialog(key)

    def action_menu(self, name):
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        self.push_screen(Menu(name, MENUS[name], min(list(MENUS).index(name)*9, max(0, self.size.width-36))), self.dispatch)

    def action_tool(self, tool):
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        if tool == "library":
            if not self.library_tool:
                self.action_tool_library()
                return
        else: self.library_tool = None
        self.tool = tool
        self.store_tool_settings()
        self.sync_ui()
        self.canvas.focus()

    def action_fit(self):
        self.canvas.fit()
        self.update_status()

    def action_actual(self):
        self.canvas.zoom = 1
        self.canvas.pan_x = self.canvas.pan_y = 0
        self.canvas.invalidate(artwork=False)
        self.update_status()

    def action_zoom_in(self): self.canvas.zoom_at(1.25)
    def action_zoom_out(self): self.canvas.zoom_at(.8)
    def action_pan(self, dx, dy):
        self.canvas.pan_x += dx/self.canvas.zoom
        self.canvas.pan_y += dy/self.canvas.zoom
        self.canvas.invalidate(artwork=False)
        self.update_status()
    def action_smaller(self):
        if self.tool == "library": self.library_size = max(1, self.library_size-1)
        else: self.brush_size = max(1, self.brush_size-1)
        self.sync_ui()
    def action_larger(self):
        if self.tool == "library": self.library_size = min(512, self.library_size+1)
        else: self.brush_size = min(128, self.brush_size+1)
        self.sync_ui()
    def action_swap_colors(self):
        self.foreground, self.background = self.background, self.foreground
        self.sync_ui()
    def action_undo(self):
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        else: self.doc.undo()
        self.load_settings()
        self.sync_ui()
    def action_redo(self):
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        self.doc.redo()
        self.load_settings()
        self.sync_ui()

    def guard_document(self, callback):
        if self.doc.dirty:
            self.push_screen(Confirm("This document has unsaved changes. Continue and discard them?\nCancel, then Ctrl+S to save your project."), lambda yes: callback() if yes else None)
        else: callback()

    def action_quit(self):
        if self._cli_screen is not None and self.screen is self._cli_screen:
            self._cli_screen.action_quit()
            return
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        self.guard_document(self.exit)

    def replace_document(self, doc, path=None):
        if doc.clipboard is None:
            doc.clipboard = self.doc.clipboard
        self.doc = doc
        self.project_path = Path(path).resolve() if path else None
        if self._cli_screen is not None:
            self._cli_screen.session.document = self.doc
            self._cli_screen.session.project_path = self.project_path
            if self._cli_screen.is_mounted:
                if self._cli_screen.preview is not None:
                    self._cli_screen.preview.update(self.doc, self._cli_screen.metadata())
                self._cli_screen.update_status()
        self.apply_runtime_settings()
        self.load_settings()
        self.sync_ui()
        self.action_fit()

    def action_new(self):
        def validate(values):
            values["width"] = self.number(values["width"], 1, 4096, int)
            values["height"] = self.number(values["height"], 1, 4096, int)
            valid_size(values["width"], values["height"])
            return values
        def create(values):
            doc = Document(values["width"], values["height"])
            if values["background"] == "transparent": doc.layers.pop(0); doc.active = 0
            doc.metadata["title"] = values["title"]
            self.guard_document(lambda: self.replace_document(doc))
        self.form("New canvas", [("title", "Title", "Untitled", None), ("width", "Width (pixels)", 96, None),
                  ("height", "Height (pixels)", 64, None), ("background", "Background", "white", ["white", "transparent"])], create, validate)

    def path_dialog(self, title, default, callback, extensions=None, must_exist=False, operation=None):
        from .file_explorer import FileExplorer
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        mode = operation or ("open" if must_exist else "save")
        start = default or (self.project_path.parent if self.project_path else Path.cwd())
        self.push_screen(FileExplorer(mode, start, config=self.config, extensions=extensions,
                                      must_exist=must_exist, title=title, project_dir=project_roots()[0]),
                         lambda choice: callback(choice.path) if choice is not None else self.canvas.focus())

    def action_open(self):
        def opened(path):
            try: doc = load_project(path) if path.suffix.lower() == ".tart" else import_document(path)
            except (ValueError, OSError) as error:
                self.notify(str(error), severity="error")
                return
            self.guard_document(lambda: self.replace_document(doc, path if path.suffix.lower() == ".tart" else None))
        self.path_dialog("Open project or image", "", opened, must_exist=True, operation="open")

    def save_to(self, path):
        def write():
            try:
                self.store_tool_settings()
                save_project(self.doc, path)
                self.project_path = Path(path).resolve()
                self.sync_ui()
                self.notify(f"Saved {path}")
            except (ValueError, OSError) as error: self.notify(str(error), severity="error")
        if Path(path).exists() and Path(path).resolve() != self.project_path:
            self.push_screen(Confirm(f"Replace existing file?\n{path}"), lambda yes: write() if yes else None)
        else: write()

    def action_save(self):
        if self.project_path: self.save_to(self.project_path)
        else: self.action_save_as()

    def action_save_as(self):
        self.path_dialog("Save editable project", str(self.project_path or "Untitled.tart"), self.save_to, [".tart"])

    def action_export(self):
        from .file_explorer import FileExplorer
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        try:
            default_path = str(resolve_export_path("painting.png", self.config))
        except (ValueError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        def write(choice):
            if choice is None:
                self.canvas.focus()
                return
            self._export_choice(choice)
        self.push_screen(FileExplorer("export", default_path, config=self.config,
                                      title="Export image / text", project_dir=project_roots()[0],
                                      canvas_size=self.doc.size), write)

    def _export_choice(self, choice):
        path, columns, lossy = choice.path, choice.columns, choice.allow_lossy
        def run():
            try:
                self.canvas.record_export_view()
                destination = export(self.doc, path, columns, allow_lossy=lossy, config=self.config, scale=choice.scale)
                if path.suffix.lower() in (".txt", ".ansi"):
                    self.notify(f"Exported text: {destination}")
                else:
                    width, height = export_dimensions(self.doc.size, choice.scale, extension=path.suffix)
                    self.notify(f"Exported {width}×{height}: {destination}")
            except (ValueError, OSError) as error: self.notify(str(error), severity="error")
        if path.exists(): self.push_screen(Confirm(f"Replace existing export?\n{path}"), lambda yes: run() if yes else None)
        else: run()

    def action_files(self):
        from .file_explorer import FileExplorer
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        start = self.project_path.parent if self.project_path else Path.cwd()
        self.push_screen(FileExplorer("open", start, config=self.config,
                                      title="File explorer", allow_operations=True, project_dir=project_roots()[0],
                                      validator=self._validate_file_choice, canvas_size=self.doc.size),
                         self._file_choice)

    @staticmethod
    def _validate_file_choice(choice):
        if choice.operation == "directory":
            from .file_explorer import FileChoice
            from .storage import validate_export_directory
            return FileChoice(validate_export_directory(choice.path), "directory")
        return choice

    def _file_choice(self, choice):
        if choice is None:
            self.canvas.focus()
            return
        path, mode = choice.path, choice.operation
        if mode == "open":
            try:
                doc = load_project(path) if path.suffix.lower() == ".tart" else import_document(path)
            except (ValueError, OSError) as error:
                self.notify(str(error), severity="error")
                return
            self.guard_document(lambda: self.replace_document(doc, path if path.suffix.lower() == ".tart" else None))
        elif mode == "import":
            self._import_file(path)
        elif mode == "save":
            self.save_to(path)
        elif mode == "export":
            self._export_choice(choice)
        elif mode == "script":
            self.guard_document(lambda: self._run_file_script(path))
        elif mode == "debug":
            self._export_debug_file(path)
        elif mode == "font":
            self.text_dialog((0, 0), font=str(path))
        elif mode == "directory":
            self._set_export_folder(path)

    def _set_export_folder(self, path):
        try:
            self.config.set("export.directory", str(path))
            self.notify(f"Export folder: {path}")
        except (ValueError, OSError) as error:
            self.notify(str(error), severity="error")

    def action_export_folder(self):
        from .file_explorer import FileExplorer, FileChoice
        from .storage import validate_export_directory
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        def validate(choice):
            return FileChoice(validate_export_directory(choice.path), "directory")
        self.push_screen(FileExplorer("directory", self.config.get("export.directory"), config=self.config,
                                      title="Choose export folder", project_dir=project_roots()[0], validator=validate),
                         lambda choice: self._set_export_folder(choice.path) if choice is not None else None)

    def action_run_script(self):
        self.path_dialog("Run painting script", "", lambda path: self.guard_document(lambda: self._run_file_script(path)),
                         must_exist=True, operation="script")

    def _run_file_script(self, path):
        from .commands import CommandError, CommandSession
        self.store_tool_settings()
        session = CommandSession(self.doc, self.project_path, config=self.config)
        try:
            results = session.run_script(path.read_text(encoding="utf-8-sig"), str(path), base_dir=path.parent)
            if session.document is not self.doc:
                self.replace_document(session.document, session.project_path)
            else:
                self.project_path = session.project_path
                self.apply_runtime_settings()
                self.load_settings()
                self.sync_ui()
            self.notify(f"Ran {path.name} · {len(results)} commands")
        except (CommandError, ValueError, OSError) as error:
            self.notify(str(error), severity="error")

    def action_export_debug(self):
        from .file_explorer import FileExplorer
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        try:
            default = resolve_export_path("sparker-debug.json", self.config)
        except (ValueError, OSError) as error:
            self.notify(str(error), severity="error")
            return
        self.push_screen(FileExplorer("debug", default, config=self.config, project_dir=project_roots()[0]),
                         lambda choice: self._export_debug_file(choice.path) if choice is not None else None)

    def _export_debug_file(self, path):
        def run():
            import json
            from .diagnostics import collect_diagnostics
            try:
                destination = resolve_export_path(path, self.config)
                if destination.suffix.lower() != ".json":
                    raise ValueError("Debug export requires a .json filename.")
                data = collect_diagnostics(self.doc, self.config)
                destination.parent.mkdir(parents=True, exist_ok=True)
                from .storage import atomic_write
                atomic_write(destination, lambda temporary: Path(temporary).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8"))
                self.notify(f"Exported diagnostics: {destination}")
            except (ValueError, OSError) as error: self.notify(str(error), severity="error")
        if path.exists(): self.push_screen(Confirm(f"Replace existing debug report?\n{path}"), lambda yes: run() if yes else None)
        else: run()

    def action_import(self):
        self.path_dialog("Import image as layer · top-left, clipped to canvas", "", self._import_file,
                         must_exist=True, operation="import")

    def _import_file(self, path):
        try:
            image = open_image(path)
            full = Image.new("RGBA", self.doc.size)
            full.paste(image, (0, 0))
            self.perform("Import layer", lambda: self.doc.add_layer(path.stem, full))
        except (ValueError, OSError) as error: self.notify(str(error), severity="error")

    def action_brush_options(self):
        if self.tool == "library":
            self.action_library_options()
            return
        def validate(v):
            v["size"] = self.number(v["size"], 1, 128, int)
            v["hardness"] = self.number(v["hardness"], 0, 100)/100
            v["opacity"] = self.number(v["opacity"], 0, 100)/100
            v["tolerance"] = self.number(v["tolerance"], 0, 255, int)
            return v
        def apply(v):
            self.brush_size, self.hardness, self.opacity, self.tolerance = v["size"], v["hardness"], v["opacity"], v["tolerance"]
            self.filled, self.radial = v["shape"] == "filled", v["gradient"] == "radial"
            self.sync_ui()
        self.form("Tool settings", [("size", "Brush / shape width", self.brush_size, None),
                  ("hardness", "Brush hardness %", round(self.hardness*100), None),
                  ("opacity", "Tool opacity %", round(self.opacity*100), None),
                  ("tolerance", "Fill / wand RGBA tolerance", self.tolerance, None),
                  ("shape", "Shape mode", "filled" if self.filled else "outline", ["outline", "filled"]),
                  ("gradient", "Gradient type", "radial" if self.radial else "linear", ["linear", "radial"])], apply, validate)

    def action_colors(self):
        def validate(v):
            for key in v:
                rgb = ImageColor.getrgb(v[key])
                v[key] = "#%02x%02x%02x" % rgb[:3]
            return v
        def apply(v):
            self.foreground, self.background = v["fg"], v["bg"]
            self.sync_ui()
        self.form("Colors", [("fg", "Foreground", self.foreground, None), ("bg", "Background / gradient endpoint", self.background, None)], apply, validate)

    def text_dialog(self, point, font=""):
        def validate(v):
            v["size"] = self.number(v["size"], 1, 512, int)
            if v["font"]:
                from PIL import ImageFont
                ImageFont.truetype(v["font"], v["size"])
            return v
        self.form("Place raster text", [("text", "Text (use \\n for newlines)", "Hello", None),
                  ("size", "Font size (pixels)", 12, None), ("font", "Optional TTF / OTF path", font, None)],
                  lambda v: self.perform("Text", lambda: self.doc.text(point, v["text"].replace("\\n", "\n"), self.foreground, v["size"], v["font"], self.opacity)), validate,
                  "Text is painted onto the active layer. Add a layer first to keep it separate.",
                  browse_fields={"font": {"operation": "font", "config": self.config,
                                          "project_dir": project_roots()[0], "title": "Choose a font"}})

    def action_all(self): self.perform("Select all", lambda: setattr(self.doc, "selection", Image.new("L", self.doc.size, 255)))
    def action_none(self): self.perform("Deselect", lambda: setattr(self.doc, "selection", None))
    def action_invert_selection(self):
        self.perform("Invert selection", lambda: setattr(self.doc, "selection", ImageOps.invert(self.doc.selection) if self.doc.selection is not None else Image.new("L", self.doc.size, 255)))
    def action_selection_mode(self):
        self.form("Selection mode", [("mode", "Combine next selection", self.selection_mode, ["replace", "add", "subtract", "intersect"])],
                  lambda v: (setattr(self, "selection_mode", v["mode"]), self.sync_ui()))
    def action_feather(self):
        if self.doc.selection is None: self.notify("Select an area first.", severity="warning"); return
        self.form("Feather selection", [("radius", "Radius (pixels)", 2, None)],
                  lambda v: self.perform("Feather selection", lambda: setattr(self.doc, "selection", self.doc.selection.filter(ImageFilter.GaussianBlur(v)))),
                  lambda v: self.number(v["radius"], 0, 100))
    def action_crop(self): self.perform("Crop canvas", self.doc.crop_selection); self.action_fit()
    def action_copy(self):
        try: self.doc.copy_selection(); self.notify("Copied active layer selection")
        except ValueError as error: self.notify(str(error), severity="warning")
    def action_cut(self): self.perform("Cut", lambda: self.doc.copy_selection(True))
    def action_paste(self): self.perform("Paste", self.doc.paste)
    def action_clear(self): self.perform("Clear", lambda: self.doc.paint_mask(Image.new("L", self.doc.size, 255), "black", erase=True))

    def action_add_layer(self):
        self.form("New layer", [("name", "Layer name", f"Layer {len(self.doc.layers)+1}", None)],
                  lambda v: self.perform("New layer", lambda: self.doc.add_layer(v["name"][:256] or "Layer")))
    def action_duplicate(self): self.perform("Duplicate layer", self.doc.duplicate)
    def action_delete_layer(self): self.perform("Delete layer", self.doc.delete_layer)
    def action_visibility(self): self.perform("Layer visibility", lambda: setattr(self.doc.layer, "visible", not self.doc.layer.visible))
    def action_lock(self): self.perform("Layer lock", lambda: setattr(self.doc.layer, "locked", not self.doc.layer.locked))
    def reorder(self, direction):
        def move():
            old = self.doc.active
            new = max(0, min(len(self.doc.layers)-1, old+direction))
            self.doc.layers[old], self.doc.layers[new] = self.doc.layers[new], self.doc.layers[old]
            self.doc.active = new
        self.perform("Reorder layer", move)
    def action_raise(self): self.reorder(1)
    def action_lower(self): self.reorder(-1)
    def action_merge(self): self.perform("Merge layers", self.doc.merge_down)
    def action_flatten(self):
        def flatten():
            image = self.doc.composite()
            self.doc.layers = [Layer("Flattened", image)]
            self.doc.active = 0
        self.perform("Flatten visible layers", flatten)
    def action_layer_properties(self):
        layer = self.doc.layer
        def validate(v):
            v["opacity"] = self.number(v["opacity"], 0, 100)/100
            if not v["name"].strip() or len(v["name"]) > 256: raise ValueError("Name must be 1–256 characters.")
            return v
        def apply(v):
            def change():
                layer.name, layer.opacity, layer.blend = v["name"], v["opacity"], v["blend"]
            self.perform("Layer properties", change)
        self.form("Layer properties", [("name", "Name", layer.name, None), ("opacity", "Layer opacity %", layer.opacity*100, None),
                  ("blend", "Blend mode", layer.blend, BLENDS)], apply, validate)
    def action_mask_selection(self):
        def apply():
            self.doc.ensure_editable()
            if self.doc.selection is None: raise ValueError("Select an area first.")
            self.doc.layer.mask = self.doc.selection.copy()
        self.perform("Layer mask from selection", apply)
    def action_mask_invert(self):
        def apply():
            self.doc.ensure_editable()
            if self.doc.layer.mask is None: raise ValueError("This layer has no mask.")
            self.doc.layer.mask = ImageOps.invert(self.doc.layer.mask)
        self.perform("Invert layer mask", apply)
    def action_mask_remove(self):
        def apply(): self.doc.ensure_editable(); self.doc.layer.mask = None
        self.perform("Remove layer mask", apply)
    def action_mask_apply(self):
        def apply():
            self.doc.ensure_editable()
            from PIL import ImageChops
            if self.doc.layer.mask is not None:
                self.doc.layer.image.putalpha(ImageChops.multiply(self.doc.layer.image.getchannel("A"), self.doc.layer.mask))
                self.doc.layer.mask = None
        self.perform("Apply layer mask", apply)

    def size_dialog(self, resample):
        def validate(v):
            w, h = self.number(v["width"], 1, 4096, int), self.number(v["height"], 1, 4096, int)
            valid_size(w, h)
            return w, h
        def apply(v):
            self.perform("Scale image" if resample else "Canvas size", lambda: self.doc.resize(*v, resample=resample))
            self.action_fit()
        self.form("Scale image" if resample else "Canvas size · top-left anchor", [("width", "Width", self.doc.width, None),
                  ("height", "Height", self.doc.height, None)], apply, validate)
    def action_resize(self): self.size_dialog(True)
    def action_canvas_size(self): self.size_dialog(False)
    def transform(self, kind): self.perform("Transform layer", lambda: self.doc.transform_layer(kind))
    def action_flip_h(self): self.transform("flip_h")
    def action_flip_v(self): self.transform("flip_v")
    def action_rotate_cw(self): self.transform("rotate_cw")
    def action_rotate_ccw(self): self.transform("rotate_ccw")
    def action_rotate(self):
        def apply(angle):
            def rotate():
                self.doc.ensure_editable()
                self.doc.layer.image = self.doc.layer.image.rotate(-angle, Image.Resampling.BICUBIC)
                if self.doc.layer.mask is not None: self.doc.layer.mask = self.doc.layer.mask.rotate(-angle, Image.Resampling.BICUBIC)
                self.doc.layer.text_recipe = None
            self.perform("Rotate layer", rotate)
        self.form("Rotate layer · clockwise, clipped to canvas", [("angle", "Degrees", 15, None)], apply,
                  lambda v: self.number(v["angle"], -360, 360))
    def action_scale_layer(self):
        def apply(percent):
            def scale():
                self.doc.ensure_editable()
                size = max(1, round(self.doc.width*percent/100)), max(1, round(self.doc.height*percent/100))
                valid_size(*size)
                layer = self.doc.layer
                for attr in ("image", "mask"):
                    image = getattr(layer, attr)
                    if image is None: continue
                    resized = image.resize(size, Image.Resampling.LANCZOS)
                    full = Image.new(image.mode, self.doc.size)
                    full.paste(resized, ((self.doc.width-size[0])//2, (self.doc.height-size[1])//2))
                    setattr(layer, attr, full)
                layer.text_recipe = None
            self.perform("Scale layer", scale)
        self.form("Scale active layer · centered, clipped to canvas", [("percent", "Scale %", 75, None)], apply,
                  lambda v: self.number(v["percent"], 1, 400))

    def filter_dialog(self, name):
        adjustable = {"brightness": (1.2, 0, 10), "contrast": (1.2, 0, 10), "saturation": (1.2, 0, 10),
                      "blur": (2, 0, 100), "sharpen": (1, 0, 10), "posterize": (4, 1, 8), "threshold": (128, 0, 255)}
        if name not in adjustable:
            self.perform(name.title(), lambda: self.doc.apply_filter(name))
        else:
            default, low, high = adjustable[name]
            self.form(name.title(), [("amount", f"Amount ({low}–{high})", default, None)],
                      lambda amount: self.perform(name.title(), lambda: self.doc.apply_filter(name, amount)),
                      lambda v: self.number(v["amount"], low, high),
                      "Applies to active layer pixels inside the selection, preserving alpha. Undo to revert.")

    def action_grid(self): self.canvas.grid = not self.canvas.grid; self.canvas.invalidate(artwork=False)
    def action_guides(self): self.canvas.guides = not self.canvas.guides; self.canvas.invalidate(artwork=False)
    def action_guide_options(self):
        def validate(v):
            spacing = self.number(v["spacing"], 1, 4096, int)
            def parse(value, maximum):
                result = [int(x.strip()) for x in value.split(",") if x.strip()]
                if len(result) > 100 or any(not 0 <= x < maximum for x in result): raise ValueError("Guides must be within the canvas (maximum 100 per axis).")
                return result
            return spacing, parse(v["gx"], self.doc.width), parse(v["gy"], self.doc.height)
        def apply(v):
            self.perform("Grid and guides", lambda: self.doc.metadata.update(grid_spacing=v[0], guides_x=v[1], guides_y=v[2]))
        self.form("Grid and guides", [("spacing", "Grid spacing in pixels", self.doc.metadata["grid_spacing"], None),
                  ("gx", "Vertical guide X positions, comma-separated", ",".join(map(str, self.doc.metadata["guides_x"])), None),
                  ("gy", "Horizontal guide Y positions, comma-separated", ",".join(map(str, self.doc.metadata["guides_y"])), None)], apply, validate)
    def action_palette(self):
        def validate(v):
            colors = [x.strip() for x in v["colors"].split(",") if x.strip()]
            if not 1 <= len(colors) <= 64: raise ValueError("Enter 1–64 comma-separated colors.")
            for color in colors: ImageColor.getrgb(color)
            return ["#%02x%02x%02x" % ImageColor.getrgb(color)[:3] for color in colors]
        self.form("Edit palette", [("colors", "Comma-separated hex colors or color names", ", ".join(self.doc.settings["palette"]), None)],
                  lambda colors: self.perform("Palette", lambda: self.doc.settings.update(palette=colors)), validate)
    def action_help(self):
        self.form("SPARKER iCLI controls", [], lambda _: None, note=(
            "Left-drag: paint, shapes, selections, gradient or move layer.\n"
            "Right/middle-drag or Hand: pan. Wheel: zoom around pointer.\n"
            "Tool keys: B brush · P pencil · E eraser · F fill · G gradient\n"
            "T text · L line · R rect · O ellipse · S select · W wand\n"
            "I picker · M move layer · H hand · X swap colors · [ ] size\n"
            "0 fit · 1 actual size · + / - zoom · arrows pan\n"
            "Ctrl+S save · Ctrl+E export · Ctrl+Z undo · Ctrl+Y redo\n"
            "Ctrl+A all · Ctrl+D deselect · Ctrl+C/X/V copy/cut/paste\n"
            "F2 File dropdown. Tab and arrows navigate menus and fields.\n"
            "Press Enter after editing the top color/size/opacity fields.\n"
            "Escape cancels a live gesture or dialog. Ctrl+Q quits.\n\n"
            "F3 or : opens the full-screen CLI and live painting preview.\n"
            "F3 / Escape returns to painting and keeps your edits.\n"
            "↑ / ↓ recall commands. Ctrl+Space completes a command name.\n"
            "F4 toggles side panels. F5 opens the art tool library.\n"
            "F6 opens the file explorer for projects, imports, exports, scripts and fonts.\n"
            "Tools menu offers search, previews, options and immediate apply.\n"
            "In CLI: tools categories · tools search QUERY · apply TOOL_ID\n\n"
            "Layer transforms affect the full layer and clip to the canvas.\n"
            "Shapes and text are raster pixels. Masks come from selections.\n"
            "At low zoom, one terminal cell covers multiple image pixels.\n"
            "Export is flattened; use .tart to preserve editable state."))
    def action_about(self):
        from . import __version__
        self.form(f"SPARKER iCLI {__version__}", [], lambda _: None, note="A minimal terminal image editor and command studio.\nYour mouse tools and commands edit the same document.\nNative .tart projects remain compatible with earlier files.")

    def action_debug_info(self):
        self.show_console_command("debug info")

    def action_runtime_settings(self):
        self.show_console_command("config list")

    def action_ai_settings(self):
        from .mcp_settings import MCPSettingsScreen
        self.push_screen(MCPSettingsScreen(self.config))

    def action_retouch_options(self):
        from .native_options import validate_retouch_options
        def validate(values):
            return validate_retouch_options(json.loads(values["options"]))
        self.form("Painting and retouch options", [
            ("options", "Options as JSON", json.dumps(self.doc.settings.get("retouch_options", {})), None)],
            lambda values: self.doc.settings.update(retouch_options=values), validate,
            "strength, radius, tonal_range, exposure, rate, angle, aspect, pressure, preset, seed, density, merged.\n"
            "Clone/heal: Ctrl-click sets the source. Perspective clone needs source_quad and dest_quad (four X,Y pairs each).\n"
            "Foreground: Ctrl-click marks the subject, then drag its outline.\n"
            'Example: {"strength": 0.5, "radius": 3, "merged": true}')

    def command_form(self, title, default):
        from .commands import CommandSession, tokenize
        def validate(values):
            line = values["command"].strip()
            tokens = tokenize(line)
            if not tokens or tokens[0] != tokenize(default)[0]:
                raise ValueError("Keep the tool family shown here; use F3 for other commands.")
            return line
        def apply(line):
            self.store_tool_settings()
            session = CommandSession(self.doc, self.project_path, config=self.config)
            try:
                result = session.execute(line)
                self.cli_document_changed(session, result)
                if result.text: self.notify(result.text[:500])
            except (ValueError, OSError) as error:
                self.notify(str(error), severity="error")
        self.form(title, [("command", "Command", default, None)], apply, validate,
                  "Use F3 and help COMMAND to see every option. Edits share the painter's undo history.")

    def action_geometry_tools(self): self.command_form("Advanced transforms", "geometry shear 0.2 --resample bilinear")
    def action_path_tools(self): self.show_console_command("help path")
    def action_channel_tools(self): self.show_console_command("help channel")
    def action_features(self): self.show_console_command("features")
    def action_tone_tools(self): self.command_form("Tone controls", "tone exposure --stops 1")
    def action_native_effects(self): self.command_form("Native effects", "effect-filter gaussian-blur --radius 2")
    def action_effect_stack(self): self.show_console_command("help fx")

    def show_console_command(self, command):
        if self._cli_screen is None:
            self.action_console()
        self.call_after_refresh(lambda: self.execute_command(command))

    def action_tool_library(self):
        from .tool_browser import ToolLibraryBrowser
        if self.canvas.dragging: self.canvas.action_cancel_gesture()
        def choose(result):
            if result is None: return
            action, identifier = result
            self.library_tool, self.tool = identifier, "library"
            self.store_tool_settings()
            if action == "apply": self.action_apply_library()
            else: self.sync_ui()
            self.canvas.focus()
        self.push_screen(ToolLibraryBrowser(), choose)

    def library_parameters(self):
        return {"color": self.foreground, "background": self.background, "opacity": self.opacity,
                "size": self.library_size, "angle": self.library_angle, "seed": self.library_seed,
                "density": self.library_density, "amount": self.library_amount}

    def action_apply_library(self):
        if not self.library_tool:
            self.action_tool_library()
            return
        from .tool_library import apply_tool
        try:
            apply_tool(self.doc, self.library_tool, **self.library_parameters())
        except (ValueError, OSError) as error: self.notify(str(error), severity="error")
        self.sync_ui()

    def action_library_options(self):
        def validate(v):
            return {"size": self.number(v["size"], 1, 512, int), "angle": self.number(v["angle"], -360, 360),
                    "seed": self.number(v["seed"], 0, 2147483647, int), "density": self.number(v["density"], .1, 4),
                    "amount": self.number(v["amount"], 0, 4)}
        def apply(v):
            for key, value in v.items(): setattr(self, "library_"+key, value)
            self.store_tool_settings()
            self.sync_ui()
        self.form("Art library tool options", [("size", "Tip / pattern tile size (pixels)", self.library_size, None),
                  ("angle", "Rotation (degrees)", self.library_angle, None), ("seed", "Texture seed", self.library_seed, None),
                  ("density", "Mark density (0.1–4)", self.library_density, None),
                  ("amount", "Effect strength (0–4)", self.library_amount, None)], apply, validate,
                  "Current tool: " + (self.library_tool or "choose one with F5") + "\nDrawing uses the active layer, selection and opacity.")

    def action_library_categories(self):
        self.show_console_command("tools categories")
