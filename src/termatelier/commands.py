"""Shared, deliberately non-shell editing commands for SPARKER iCLI.

CommandSession.execute raises CommandError, or returns CommandResult. The same
session powers batch jobs, the REPL and the terminal editor's command bar. All
pixel edits use Document.edit and therefore share the editor's undo history.
"""
from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shlex
import time

from PIL import Image, ImageChops, ImageColor, ImageFilter, ImageOps

from .model import BLENDS, Document, Layer, valid_size
from .storage import export, import_document, load_project, open_image, save_project, resolve_export_path
from .config import RuntimeConfig, SPECS
from .diagnostics import publish_diagnostics, format_diagnostics


class CommandError(ValueError):
    """A command is malformed or cannot be applied to the current document."""


class ScriptError(CommandError):
    """A script failed; its source and line number are included in the message."""


@dataclass(frozen=True)
class CommandResult:
    text: str = ""
    changed: bool = False
    document_replaced: bool = False
    quit_requested: bool = False


HELP = {
    "help": "help [COMMAND] — list commands or show command syntax",
    "new": "new WIDTHxHEIGHT [--transparent | --color COLOR]",
    "open": "open PATH — replace the document with a .tart project or image",
    "save": "save [PATH.tart] — preserve layers, selection, palette and metadata",
    "export": "export [PATH] [--columns N] [--allow-lossy] — external export folder; PNG/WebP/TIFF preserve RGBA pixels",
    "files": "files [open|import|save|export|script|debug|font|directory] [START_PATH] — F6 file explorer in the painter/full-screen CLI; use explicit paths in batch or --repl",
    "config": "config list [PREFIX] [--json] | get NAME | set NAME VALUE | reset [NAME|all] | path — validated, persistent preferences",
    "debug": "debug [info] [--json] | on | off | toggle | settings [--json] | set NAME VALUE | export [PATH.json] — transparent overlay and diagnostics",
    "import": "import PATH [--name NAME] [--x N] [--y N] — import as a layer",
    "info": "info [--json] — canvas, layers and current command settings",
    "tool": "tool NAME_OR_LIBRARY_ID — select a classic drawing tool or any catalog tool from tools list",
    "tools": "tools [list|search QUERY|categories|count|info ID|use ID|apply ID] [--category NAME] [--kind NAME] [--page N] [--limit N] [--json] — searchable art tool library",
    "apply": "apply TOOL_ID [X,Y ...] [--box X0,Y0,X1,Y1] [--size N] [--color COLOR] [--background COLOR] [--opacity N] [--seed N] [--angle N] [--density N] [--amount N] — apply a library tool",
    "tool-options": "tool-options [--size 1..512] [--angle -360..360] [--seed N] [--density 0.1..4] [--amount 0..4] — configure the selected library tool",
    "color": "color COLOR [BACKGROUND] | color foreground|background COLOR | color swap",
    "brush": "brush [--size 1..128] [--hardness 0..1] [--opacity 0..1] — configure tools",
    "stroke": "stroke X,Y [X,Y ...] [--color COLOR] [--size N] [--hardness N] [--opacity N]",
    "pencil": "pencil X,Y [X,Y ...] [--color COLOR] [--size N] [--opacity N]",
    "erase": "erase X,Y [X,Y ...] [--size N] [--hardness N] [--opacity N]",
    "fill": "fill X Y [--color COLOR] [--tolerance 0..255] [--opacity N] [--merged]",
    "gradient": "gradient X0 Y0 X1 Y1 [--from COLOR] [--to COLOR] [--opacity N] [--radial]",
    "line": "line X0 Y0 X1 Y1 [--color COLOR] [--width N] [--opacity N]",
    "rectangle": "rectangle X0 Y0 X1 Y1 [--filled] [--color COLOR] [--width N] [--opacity N]",
    "ellipse": "ellipse X0 Y0 X1 Y1 [--filled] [--color COLOR] [--width N] [--opacity N]",
    "text": "text X Y \"WORDS\" [--color COLOR] [--size N] [--font PATH] [--opacity N]",
    "pick": "pick X Y [--active] — sample visible pixels into foreground color",
    "clear": "clear — erase active-layer pixels inside the selection, or the whole layer",
    "layer": "layer list|add [NAME]|select INDEX_OR_NAME|rename NAME|duplicate|delete|raise|lower|reorder INDEX|merge|flatten|opacity N|blend MODE|show|hide|lock|unlock|mask selection|white|black|invert|apply|remove (indices start at 1, bottom first)",
    "select": "select all|none|invert|rectangle X0 Y0 X1 Y1|ellipse X0 Y0 X1 Y1|lasso X,Y X,Y X,Y ...|wand X Y|feather N [--mode replace|add|subtract|intersect] [--tolerance N] [--merged]",
    "move": "move DX DY — shift the active layer and its mask",
    "resize": "resize WIDTHxHEIGHT — resample the complete image",
    "canvas": "canvas WIDTHxHEIGHT — resize canvas with a top-left anchor",
    "transform": "transform flip-h|flip-v|rotate-cw|rotate-ccw|rotate DEGREES|scale WIDTHxHEIGHT — active layer and mask, centered within canvas",
    "crop": "crop — crop the complete image to selection bounds",
    "filter": "filter invert|grayscale|sepia|blur|sharpen|edges|emboss|posterize|threshold|brightness|contrast|saturation|autocontrast [AMOUNT] — active layer, constrained by selection",
    "copy": "copy — copy active-layer pixels within the selection",
    "cut": "cut — copy then erase selected active-layer pixels",
    "paste": "paste — add clipboard pixels as a new layer at their original location",
    "palette": "palette list|add COLOR|remove INDEX|set COLOR [COLOR ...] — 1..64 colors, indices start at 1",
    "guides": "guides [x|y POSITION ...] | guides clear — list or replace guides on one axis",
    "grid": "grid [SPACING] — inspect or set grid spacing in pixels",
    "meta": "meta [KEY VALUE] — inspect metadata or set a text value (title, author, etc.)",
    "undo": "undo [COUNT] — undo edits",
    "redo": "redo [COUNT] — redo edits",
    "history": "history — list undo and redo labels",
    "script": "script PATH [--nonatomic] — run commands relative to the script's folder; stop and report the first failing line",
    "quit": "quit — leave the REPL or request exit from the editor",
}

TOOLS = {"brush", "pencil", "eraser", "fill", "gradient", "text", "line", "rectangle", "ellipse",
         "select_rect", "select_ellipse", "lasso", "wand", "picker", "move", "hand"}
FILTERS = {"invert", "grayscale", "sepia", "blur", "sharpen", "edges", "emboss", "posterize",
           "threshold", "brightness", "contrast", "saturation", "autocontrast"}
DEFAULTS = {"foreground": "#e79335", "background": "#ffffff", "brush_size": 3,
            "hardness": .8, "opacity": 1.0, "tolerance": 20, "tool": "brush"}


def tokenize(line):
    """Use shell-like quotes without interpreting Windows backslashes or code."""
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    try:
        return list(lexer)
    except ValueError as error:
        raise CommandError(str(error)) from error


def _args(tokens, options=(), flags=()):
    positional, values = [], {}
    index = 0
    literal = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--" and not literal:
            literal = True
        elif token.startswith("--") and not literal:
            key, separator, value = token[2:].partition("=")
            if key in values:
                raise CommandError(f"Duplicate option --{key}.")
            if key in flags and not separator:
                values[key] = True
            elif key in options:
                if not separator:
                    index += 1
                    if index == len(tokens) or tokens[index].startswith("--"):
                        raise CommandError(f"--{key} requires a value.")
                    value = tokens[index]
                values[key] = value
            else:
                raise CommandError(f"Unknown option --{key}.")
        else:
            positional.append(token)
        index += 1
    return positional, values


def _count(args, low, high=None):
    high = low if high is None else high
    if not low <= len(args) <= high:
        raise CommandError(f"Expected {low}" + (f"–{high}" if high != low else "") + " argument(s); use help COMMAND for syntax.")


def _integer(value, low=None, high=None):
    try:
        number = int(value)
    except (ValueError, TypeError) as error:
        raise CommandError(f"Expected an integer, got {value!r}.") from error
    if (low is not None and number < low) or (high is not None and number > high):
        raise CommandError(f"Integer {number} is outside {low}..{high}.")
    return number


def _number(value, low=None, high=None):
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise CommandError(f"Expected a number, got {value!r}.") from error
    if not math.isfinite(number) or (low is not None and number < low) or (high is not None and number > high):
        raise CommandError(f"Number {value!r} is outside {low}..{high}.")
    return number


def _fraction(value):
    if isinstance(value, str) and value.endswith("%"):
        return _number(value[:-1], 0, 100) / 100
    return _number(value, 0, 1)


def _size(value):
    parts = value.lower().split("x")
    _count(parts, 2)
    width, height = map(_integer, parts)
    valid_size(width, height)
    return width, height


def _color(value):
    try:
        r, g, b, alpha = ImageColor.getcolor(value, "RGBA")
    except (ValueError, TypeError) as error:
        raise CommandError(f"Invalid color {value!r}. Use a color name or #RRGGBB.") from error
    return f"#{r:02x}{g:02x}{b:02x}" + (f"{alpha:02x}" if alpha != 255 else "")


def _points(args, minimum=1):
    if len(args) < minimum:
        raise CommandError(f"Expected at least {minimum} X,Y point(s).")
    result = []
    for pair in args:
        xy = pair.split(",")
        _count(xy, 2)
        result.append(tuple(map(_integer, xy)))
    return result


class CommandSession:
    def __init__(self, document=None, project_path=None, base_dir=None, config=None):
        self.document = document if document is not None else Document()
        self.config = config if config is not None else RuntimeConfig.load()
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.project_path = self._path(project_path) if project_path else None
        self.script_depth = 0
        self._apply_document_config()

    def _path(self, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.base_dir / path).resolve()

    def setting(self, name):
        preference = {"brush_size": "brush.size", "hardness": "brush.hardness",
                      "opacity": "brush.opacity", "tolerance": "fill.tolerance",
                      "library_size": "library.size", "library_angle": "library.angle",
                      "library_seed": "library.seed", "library_density": "library.density",
                      "library_amount": "library.amount"}.get(name)
        fallback = self.config.get(preference) if preference else DEFAULTS.get(name)
        return self.document.settings.get(name, fallback)

    def _apply_document_config(self, changed_name=None):
        doc = self.document
        doc.history_limit = self.config.get("history.max_steps")
        doc.history_bytes = self.config.get("history.max_mb") * 1024 * 1024
        doc._trim()
        mappings = {"brush.size": "brush_size", "brush.hardness": "hardness",
                    "brush.opacity": "opacity", "fill.tolerance": "tolerance",
                    "library.size": "library_size", "library.angle": "library_angle",
                    "library.seed": "library_seed", "library.density": "library_density",
                    "library.amount": "library_amount"}
        if changed_name in mappings:
            doc.settings[mappings[changed_name]] = self.config.get(changed_name)

    def _paint_options(self, opts):
        return (_color(opts.get("color", self.setting("foreground"))),
                _fraction(opts.get("opacity", self.setting("opacity"))))

    def _edit(self, label, operation):
        with self.document.edit(label):
            operation()
        return CommandResult(label, changed=True)

    def execute(self, line):
        """Apply one command. Errors leave a failed edit rolled back, never evaluated."""
        if not line.strip() or line.lstrip().startswith(("#", "//")):
            return CommandResult()
        tokens = tokenize(line)
        if not tokens:
            return CommandResult()
        command = tokens.pop(0).lower()
        command = {"rect": "rectangle", "eraser": "erase", "exit": "quit", "ls": "info"}.get(command, command)
        try:
            result = self._execute(command, tokens)
            if result.document_replaced:
                self._apply_document_config()
            publish_diagnostics(self.document, self.config, stage="ready")
            return result
        except CommandError:
            raise
        except (ValueError, OSError, KeyError, IndexError, TypeError, RuntimeError) as error:
            raise CommandError(str(error)) from error

    def _config(self, tokens):
        action = tokens[0].lower() if tokens else "list"
        args, opts = _args(tokens[1:] if tokens else [], flags=("json",))
        if action == "list":
            _count(args, 0, 1)
            prefix = args[0] if args else ""
            values = {name: value for name, value in self.config.as_dict().items() if name.startswith(prefix)}
            if not values:
                raise CommandError(f"No settings match {prefix!r}.")
            if opts.get("json"):
                return CommandResult(json.dumps(values, indent=2))
            rows = [f"{name} = {json.dumps(value)} — {SPECS[name][2]}" for name, value in values.items()]
            return CommandResult("\n".join(rows) + f"\nSettings: {self.config.path}")
        if opts:
            raise CommandError("--json applies to config list.")
        if action == "path":
            _count(args, 0)
            return CommandResult(str(self.config.path))
        if action == "get":
            _count(args, 1)
            if args[0] not in SPECS:
                raise CommandError(f"Unknown setting {args[0]!r}.")
            return CommandResult(f"{args[0]} = {json.dumps(self.config.get(args[0]))}")
        if action == "set":
            _count(args, 2)
            value = self.config.set(args[0], args[1])
            self._apply_document_config(args[0])
            return CommandResult(f"{args[0]} = {json.dumps(value)}\nSaved preferences: {self.config.path}", changed=True)
        if action == "reset":
            _count(args, 0, 1)
            name = args[0] if args and args[0].lower() != "all" else None
            self.config.reset(name)
            self._apply_document_config(name)
            if name is None:
                for item in ("brush.size", "brush.hardness", "brush.opacity", "fill.tolerance",
                             "library.size", "library.angle", "library.seed", "library.density", "library.amount"):
                    self._apply_document_config(item)
            return CommandResult(f"Reset {name or 'all preferences'} to defaults.\nSettings: {self.config.path}", changed=True)
        raise CommandError(HELP["config"])

    def _debug(self, tokens):
        action = tokens[0].lower() if tokens else "info"
        remaining = tokens[1:] if tokens else []
        if action == "settings":
            return self._config(["list", "debug.", *remaining])
        if action == "set":
            _count(remaining, 2)
            name = remaining[0] if remaining[0].startswith("debug.") else "debug." + remaining[0]
            if not name.startswith("debug."):
                raise CommandError("Use config set for settings outside debug.")
            return self._config(["set", name, remaining[1]])
        if action in ("on", "off", "toggle"):
            _count(remaining, 0)
            enabled = not self.config.get("debug.enabled") if action == "toggle" else action == "on"
            self.config.set("debug.enabled", enabled)
            return CommandResult(f"Debug overlay {'enabled' if enabled else 'hidden'}.", changed=True)
        if action == "info":
            args, opts = _args(remaining, flags=("json",))
            _count(args, 0)
            data = publish_diagnostics(self.document, self.config, stage="ready")
            return CommandResult(json.dumps(data, indent=2) if opts.get("json") else format_diagnostics(data))
        if action == "export":
            args, _ = _args(remaining)
            _count(args, 0, 1)
            requested = args[0] if args else time.strftime("sparker-debug-%Y%m%d-%H%M%S.json")
            path = resolve_export_path(requested, config=self.config)
            if path.suffix.lower() != ".json":
                raise CommandError("Debug export requires a .json filename.")
            data = publish_diagnostics(self.document, self.config, stage="ready")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return CommandResult(f"Exported diagnostics to {path}.")
        raise CommandError(HELP["debug"])

    def _library_use(self, identifier):
        from .tool_library import get_tool
        spec = get_tool(identifier)
        def select():
            self.document.settings.update(tool="library", library_tool=spec.id)
        return self._edit(f"Use {spec.name}", select)

    def _library_apply(self, tokens):
        from .tool_library import apply_tool
        args, opts = _args(tokens, ("box", "size", "color", "background", "opacity", "seed", "angle", "density", "amount"))
        _count(args, 1, 2049)
        points = _points(args[1:], minimum=1) if len(args) > 1 else None
        box = None
        if "box" in opts:
            coordinates = opts["box"].split(",")
            _count(coordinates, 4)
            box = tuple(map(_integer, coordinates))
        if box is not None and points is not None:
            raise CommandError("Choose points or --box for one operation.")
        values = {"color": _color(opts.get("color", self.setting("foreground"))),
                  "background": _color(opts.get("background", self.setting("background"))),
                  "opacity": _fraction(opts.get("opacity", self.setting("opacity"))),
                  "size": _integer(opts.get("size", self.setting("library_size")), 1, 512),
                  "seed": _integer(opts.get("seed", self.setting("library_seed")), 0, 2147483647),
                  "angle": _number(opts.get("angle", self.setting("library_angle")), -360, 360),
                  "density": _number(opts.get("density", self.setting("library_density")), .1, 4),
                  "amount": _number(opts.get("amount", self.setting("library_amount")), 0, 4)}
        message = apply_tool(self.document, args[0], points=points, box=box, **values)
        return CommandResult(message, changed=True)

    def _library_options(self, tokens):
        args, opts = _args(tokens, ("size", "angle", "seed", "density", "amount"))
        _count(args, 0)
        if not opts:
            return CommandResult(" · ".join(f"{key}={self.setting('library_'+key)}" for key in ("size", "angle", "seed", "density", "amount")))
        values = {}
        for key, value in opts.items():
            if key == "size": normalized = _integer(value, 1, 512)
            elif key == "seed": normalized = _integer(value, 0, 2147483647)
            elif key == "angle": normalized = _number(value, -360, 360)
            elif key == "density": normalized = _number(value, .1, 4)
            else: normalized = _number(value, 0, 4)
            values["library_"+key] = normalized
        return self._edit("Library tool options", lambda: self.document.settings.update(values))

    def _library(self, tokens):
        from dataclasses import asdict
        from .tool_library import TOOLS as catalog, get_tool, list_tools
        action = tokens[0].lower() if tokens else "list"
        remaining = tokens[1:] if tokens else []
        if action in ("use", "apply"):
            if action == "use":
                _count(remaining, 1)
                return self._library_use(remaining[0])
            return self._library_apply(remaining)
        args, opts = _args(remaining, ("category", "kind", "page", "limit"), ("json",))
        if action == "info":
            _count(args, 1)
            if set(opts) - {"json"}: raise CommandError("tools info supports --json only.")
            spec = get_tool(args[0])
            if opts.get("json"): return CommandResult(json.dumps(asdict(spec), indent=2))
            return CommandResult(f"{spec.id} — {spec.name}\n{spec.category} · {spec.kind}\n{spec.description}\nUse: tool {spec.id}\nApply: apply {spec.id} [X,Y ...] [--box X0,Y0,X1,Y1]")
        if action in ("count", "categories"):
            _count(args, 0)
            if set(opts) - {"json"}: raise CommandError("Use --json only for tools count/categories.")
            categories = dict(sorted(Counter(spec.category for spec in catalog.values()).items()))
            kinds = dict(sorted(Counter(spec.kind for spec in catalog.values()).items()))
            data = {"total": len(catalog), "categories": categories, "kinds": kinds}
            if opts.get("json"): return CommandResult(json.dumps(data, indent=2))
            rows = [f"{len(catalog)} distinct art tools", " · ".join(f"{name}: {count}" for name, count in kinds.items())]
            rows.extend(f"{category}: {count}" for category, count in categories.items())
            return CommandResult("\n".join(rows))
        if action not in ("list", "search"):
            raise CommandError(HELP["tools"])
        _count(args, 0 if action == "list" else 1, 1)
        category, kind = opts.get("category"), opts.get("kind")
        if category and category not in {spec.category for spec in catalog.values()}:
            raise CommandError("Unknown category. Use tools categories.")
        if kind and kind not in {spec.kind for spec in catalog.values()}:
            raise CommandError("Unknown kind. Use tools count.")
        found = list_tools(query=args[0] if args else "", category=category, kind=kind)
        limit = _integer(opts.get("limit", 30), 1, 200)
        page = _integer(opts.get("page", 1), 1)
        subset = found[(page-1)*limit:page*limit]
        if opts.get("json"):
            return CommandResult(json.dumps({"total": len(found), "page": page, "limit": limit,
                                             "tools": [asdict(spec) for spec in subset]}, indent=2))
        lines = [f"{spec.id} — {spec.name} ({spec.category}, {spec.kind})" for spec in subset]
        lines.append(f"{len(found)} matches · page {page}/{max(1, math.ceil(len(found)/limit))} · tools info ID for details")
        return CommandResult("\n".join(lines))

    def _execute(self, command, tokens):
        doc = self.document
        if command == "help":
            _count(tokens, 0, 1)
            if tokens:
                if tokens[0] not in HELP:
                    raise CommandError(f"Unknown command {tokens[0]!r}.")
                return CommandResult(HELP[tokens[0]])
            return CommandResult("SPARKER iCLI commands\n" + "\n".join(HELP.values()))
        if command == "quit":
            _count(tokens, 0)
            return CommandResult("Session ended.", quit_requested=True)
        if command == "config":
            return self._config(tokens)
        if command == "debug":
            return self._debug(tokens)
        if command == "tools":
            return self._library(tokens)
        if command == "apply":
            return self._library_apply(tokens)
        if command == "tool-options":
            return self._library_options(tokens)
        if command == "files":
            raise CommandError("The file explorer requires the painter or full-screen --cli workspace. "
                               "Use explicit open, save, import, export, script or debug export paths in batch/--repl.")
        if command == "new":
            args, opts = _args(tokens, ("color",), ("transparent",))
            _count(args, 1)
            if "transparent" in opts and "color" in opts:
                raise CommandError("Choose --transparent or --color.")
            replacement = Document(*_size(args[0]))
            if opts.get("transparent"):
                replacement.layers = [Layer("Paint", Image.new("RGBA", replacement.size))]
                replacement.active = 0
            elif "color" in opts:
                replacement.layers[0].image = Image.new("RGBA", replacement.size, _color(opts["color"]))
            self.document, self.project_path = replacement, None
            return CommandResult(f"New {replacement.width}×{replacement.height} canvas.", True, True)
        if command == "open":
            args, _ = _args(tokens)
            _count(args, 1)
            path = self._path(args[0])
            native = path.suffix.lower() == ".tart"
            replacement = load_project(path) if native else import_document(path)
            self.document, self.project_path = replacement, path if native else None
            return CommandResult(f"Opened {path}.", True, True)
        if command == "save":
            args, _ = _args(tokens)
            _count(args, 0, 1)
            path = self._path(args[0]) if args else self.project_path
            if path is None:
                raise CommandError("Use save PATH.tart for the first save.")
            save_project(doc, path)
            self.project_path = path
            return CommandResult(f"Saved {path}.", changed=True)
        if command == "export":
            args, opts = _args(tokens, ("columns",), ("allow-lossy",))
            _count(args, 0, 1)
            columns = _integer(opts.get("columns", 100), 1, 500)
            requested = args[0] if args else None
            if requested and (requested.startswith("./") or requested.startswith(".\\") or requested.startswith("../") or requested.startswith("..\\")):
                requested = self._path(requested)
            path = export(doc, requested, columns, allow_lossy=bool(opts.get("allow-lossy")), config=self.config)
            suffix = Path(path).suffix.lower()
            note = " (lossy format; dimensions preserved)" if suffix in (".jpg", ".jpeg", ".gif", ".bmp") else ""
            return CommandResult(f"Exported {path}{note}.")
        if command == "import":
            args, opts = _args(tokens, ("name", "x", "y"))
            _count(args, 1)
            path = self._path(args[0])
            image = open_image(path)
            full = Image.new("RGBA", doc.size)
            full.paste(image, (_integer(opts.get("x", 0)), _integer(opts.get("y", 0))))
            name = opts.get("name", path.stem)
            self._name(name)
            return self._edit(f"Import {name}", lambda: doc.add_layer(name, full))
        if command == "info":
            args, opts = _args(tokens, flags=("json",))
            _count(args, 0)
            data = self.describe()
            if opts.get("json"):
                return CommandResult(json.dumps(data, ensure_ascii=False, indent=2))
            return CommandResult(f"SPARKER iCLI · {doc.width}×{doc.height} · {len(doc.layers)} layers · "
                                 f"active {doc.active+1}: {doc.layer.name} · {'unsaved' if doc.dirty else 'saved'}\n"
                                 f"Foreground {self.setting('foreground')} · brush {self.setting('brush_size')} · opacity {self.setting('opacity')}\n"
                                 f"Project: {self.project_path or '(none)'}")
        if command == "tool":
            _count(tokens, 1)
            name = tokens[0].lower()
            if name not in TOOLS:
                return self._library_use(name)
            return self._edit(f"Tool {name}", lambda: doc.settings.update(tool=name, library_tool=None))
        if command == "color":
            if tokens == ["swap"]:
                fg, bg = self.setting("foreground"), self.setting("background")
                return self._edit("Swap colors", lambda: doc.settings.update(foreground=bg, background=fg))
            _count(tokens, 1, 2)
            if tokens[0] in ("foreground", "background"):
                _count(tokens, 2)
                values = {tokens[0]: _color(tokens[1])}
            else:
                values = {"foreground": _color(tokens[0])}
                if len(tokens) == 2:
                    values["background"] = _color(tokens[1])
            return self._edit("Set colors", lambda: doc.settings.update(values))
        if command == "brush":
            args, opts = _args(tokens, ("size", "hardness", "opacity"))
            _count(args, 0)
            if not opts:
                return CommandResult(f"size={self.setting('brush_size')} hardness={self.setting('hardness')} opacity={self.setting('opacity')}")
            values = {}
            if "size" in opts: values["brush_size"] = _integer(opts["size"], 1, 128)
            for name in ("hardness", "opacity"):
                if name in opts: values[name] = _fraction(opts[name])
            return self._edit("Configure brush", lambda: doc.settings.update(values))
        if command in ("stroke", "pencil", "erase"):
            args, opts = _args(tokens, ("color", "size", "hardness", "opacity"))
            points = _points(args)
            color, opacity = self._paint_options(opts)
            size = _integer(opts.get("size", 1 if command == "pencil" else self.setting("brush_size")), 1, 128)
            hardness = _fraction(opts.get("hardness", 1 if command == "pencil" else self.setting("hardness")))
            mask = doc.stroke_mask(points, size, hardness)
            return self._edit(command.capitalize(), lambda: doc.paint_mask(mask, color, opacity, command == "erase"))
        if command == "fill":
            args, opts = _args(tokens, ("color", "opacity", "tolerance"), ("merged",))
            _count(args, 2)
            point = tuple(map(_integer, args))
            self._inside(point)
            color, opacity = self._paint_options(opts)
            tolerance = _integer(opts.get("tolerance", self.setting("tolerance")), 0, 255)
            mask = doc.region_mask(point, tolerance, bool(opts.get("merged")))
            return self._edit("Fill", lambda: doc.paint_mask(mask, color, opacity))
        if command == "gradient":
            args, opts = _args(tokens, ("from", "to", "opacity"), ("radial",))
            _count(args, 4)
            points = list(map(_integer, args))
            fg, bg = _color(opts.get("from", self.setting("foreground"))), _color(opts.get("to", self.setting("background")))
            opacity = _fraction(opts.get("opacity", self.setting("opacity")))
            return self._edit("Gradient", lambda: doc.gradient(tuple(points[:2]), tuple(points[2:]), fg, bg, opacity, bool(opts.get("radial"))))
        if command in ("line", "rectangle", "ellipse"):
            args, opts = _args(tokens, ("color", "width", "opacity"), ("filled",))
            _count(args, 4)
            points = list(map(_integer, args))
            color, opacity = self._paint_options(opts)
            width = _integer(opts.get("width", self.setting("brush_size")), 1, 128)
            mask = doc.shape_mask(command, tuple(points[:2]), tuple(points[2:]), width, bool(opts.get("filled")))
            return self._edit(command.capitalize(), lambda: doc.paint_mask(mask, color, opacity))
        if command == "text":
            args, opts = _args(tokens, ("color", "size", "font", "opacity"))
            _count(args, 3)
            point = tuple(map(_integer, args[:2]))
            color, opacity = self._paint_options(opts)
            size = _integer(opts.get("size", 12), 1, 512)
            font = str(self._path(opts["font"])) if opts.get("font") else ""
            return self._edit("Text", lambda: doc.text(point, args[2], color, size, font, opacity))
        if command == "pick":
            args, opts = _args(tokens, flags=("active",))
            _count(args, 2)
            point = tuple(map(_integer, args))
            self._inside(point)
            pixel = (doc.layer.image if opts.get("active") else doc.composite()).getpixel(point)
            color = "#{:02x}{:02x}{:02x}".format(*pixel[:3])
            result = self._edit("Pick color", lambda: doc.settings.update(foreground=color))
            return CommandResult(f"Picked {color} (alpha {pixel[3]}).", result.changed)
        if command == "clear":
            _count(tokens, 0)
            return self._edit("Clear", lambda: doc.paint_mask(Image.new("L", doc.size, 255), "black", erase=True))
        if command == "layer":
            return self._layer(tokens)
        if command == "select":
            return self._select(tokens)
        if command in ("resize", "canvas"):
            _count(tokens, 1)
            size = _size(tokens[0])
            return self._edit(command.capitalize(), lambda: doc.resize(*size, resample=command == "resize"))
        if command == "move":
            _count(tokens, 2)
            dx, dy = map(_integer, tokens)
            return self._edit("Move layer", lambda: doc.move(dx, dy))
        if command == "transform":
            return self._transform(tokens)
        if command == "crop":
            _count(tokens, 0)
            return self._edit("Crop", doc.crop_selection)
        if command == "filter":
            _count(tokens, 1, 2)
            name = tokens[0].lower()
            if name not in FILTERS:
                raise CommandError("Unknown filter. " + HELP["filter"])
            amount = _number(tokens[1]) if len(tokens) == 2 else {"posterize": 4, "threshold": 128, "blur": 2}.get(name, 1)
            ranges = {"blur": (0, 100), "sharpen": (0, 10), "posterize": (1, 8), "threshold": (0, 255),
                      "brightness": (0, 10), "contrast": (0, 10), "saturation": (0, 10)}
            if name in ranges: amount = _number(amount, *ranges[name])
            elif len(tokens) == 2: raise CommandError(f"{name} does not take an amount.")
            return self._edit(f"Filter {name}", lambda: doc.apply_filter(name, amount))
        if command in ("copy", "cut", "paste"):
            _count(tokens, 0)
            if command == "copy":
                doc.copy_selection()
                return CommandResult("Copied selection.")
            return self._edit(command.capitalize(), doc.paste if command == "paste" else lambda: doc.copy_selection(True))
        if command == "palette":
            return self._palette(tokens)
        if command == "guides":
            if not tokens:
                return CommandResult(f"x={doc.metadata['guides_x']} y={doc.metadata['guides_y']}")
            if tokens == ["clear"]:
                return self._edit("Clear guides", lambda: doc.metadata.update(guides_x=[], guides_y=[]))
            if tokens[0] not in ("x", "y"):
                raise CommandError(HELP["guides"])
            if len(tokens) > 101:
                raise CommandError("At most 100 guide positions per axis.")
            limit = doc.width if tokens[0] == "x" else doc.height
            values = [_integer(x, 0, limit-1) for x in tokens[1:]]
            return self._edit("Set guides", lambda: doc.metadata.update({f"guides_{tokens[0]}": values}))
        if command == "grid":
            _count(tokens, 0, 1)
            if not tokens: return CommandResult(f"Grid spacing {doc.metadata['grid_spacing']} pixels.")
            spacing = _integer(tokens[0], 1, 4096)
            return self._edit("Set grid", lambda: doc.metadata.update(grid_spacing=spacing))
        if command == "meta":
            _count(tokens, 0, 2)
            if not tokens: return CommandResult(json.dumps(doc.metadata, ensure_ascii=False, indent=2))
            _count(tokens, 2)
            if tokens[0] in ("guides_x", "guides_y", "grid_spacing"):
                raise CommandError("Use guides or grid to change this metadata.")
            return self._edit("Set metadata", lambda: doc.metadata.update({tokens[0]: tokens[1]}))
        if command in ("undo", "redo"):
            _count(tokens, 0, 1)
            count = _integer(tokens[0], 1, 1000) if tokens else 1
            available = len(doc.undo_stack if command == "undo" else doc.redo_stack)
            if count > available:
                raise CommandError(f"Only {available} {command} step(s) available; nothing changed.")
            for _ in range(count): getattr(doc, command)()
            return CommandResult(f"{command.capitalize()} {count} step(s).", changed=True)
        if command == "history":
            _count(tokens, 0)
            return CommandResult("Undo (oldest first): " + ", ".join(x[0] for x in doc.undo_stack) +
                                 "\nRedo (next first): " + ", ".join(x[0] for x in reversed(doc.redo_stack)))
        if command == "script":
            args, opts = _args(tokens, flags=("nonatomic",))
            _count(args, 1)
            path = self._path(args[0])
            results = self.run_script(path.read_text(encoding="utf-8-sig"), str(path), atomic=not opts.get("nonatomic"), base_dir=path.parent)
            return CommandResult(f"Ran {len(results)} command(s) from {path}.\n" + "\n".join(r.text for r in results if r.text),
                                 any(r.changed for r in results), any(r.document_replaced for r in results))
        raise CommandError(f"Unknown command {command!r}. Type help to list commands.")

    def describe(self):
        doc = self.document
        return {"application": "SPARKER iCLI", "size": doc.size, "active": doc.active+1,
                "dirty": doc.dirty, "project": str(self.project_path) if self.project_path else None,
                "selection_bounds": doc.selection.getbbox() if doc.selection is not None else None,
                "metadata": copy.deepcopy(doc.metadata), "settings": copy.deepcopy(doc.settings),
                "layers": [{"index": n+1, "name": layer.name, "visible": layer.visible, "locked": layer.locked,
                            "opacity": layer.opacity, "blend": layer.blend, "masked": layer.mask is not None}
                           for n, layer in enumerate(doc.layers)]}

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not 1 <= len(name) <= 256:
            raise CommandError("Layer names must contain 1..256 characters.")

    def _inside(self, point):
        if not (0 <= point[0] < self.document.width and 0 <= point[1] < self.document.height):
            raise CommandError("Point is outside the canvas.")

    def _layer(self, tokens):
        doc = self.document
        tokens, _ = _args(tokens)
        _count(tokens, 1, 100)
        action, args = tokens[0].lower(), tokens[1:]
        if action == "list":
            _count(args, 0)
            return CommandResult("\n".join(f"{'*' if n == doc.active else ' '} {n+1}: {layer.name} · "
                f"{layer.opacity:.0%} {layer.blend} · {'visible' if layer.visible else 'hidden'}"
                f"{' locked' if layer.locked else ''}{' masked' if layer.mask is not None else ''}"
                for n, layer in enumerate(doc.layers)))
        if action == "add":
            _count(args, 0, 1)
            name = args[0] if args else "Layer"
            self._name(name)
            return self._edit(f"Add layer {name}", lambda: doc.add_layer(name))
        if action == "select":
            _count(args, 1)
            matches = [n for n, layer in enumerate(doc.layers) if layer.name == args[0]]
            if args[0].isdigit(): index = _integer(args[0], 1, len(doc.layers))-1
            elif len(matches) == 1: index = matches[0]
            else: raise CommandError("Layer name was not found or is ambiguous; use its 1-based index.")
            doc.active = index
            return CommandResult(f"Active layer {index+1}: {doc.layer.name}.", changed=True)
        if action in ("rename", "opacity", "blend"):
            _count(args, 1)
            name = {"rename": "name", "opacity": "opacity", "blend": "blend"}[action]
            value = _fraction(args[0]) if action == "opacity" else args[0]
            if action == "rename": self._name(value)
            if action == "blend" and value not in BLENDS: raise CommandError("Blend modes: " + ", ".join(BLENDS))
            return self._edit(f"Layer {action}", lambda: setattr(doc.layer, name, value))
        if action in ("show", "hide", "lock", "unlock"):
            _count(args, 0)
            name = "visible" if action in ("show", "hide") else "locked"
            return self._edit(f"Layer {action}", lambda: setattr(doc.layer, name, action in ("show", "lock")))
        if action in ("raise", "lower", "reorder"):
            _count(args, 1 if action == "reorder" else 0)
            index = _integer(args[0], 1, len(doc.layers))-1 if action == "reorder" else doc.active + (1 if action == "raise" else -1)
            if not 0 <= index < len(doc.layers): raise CommandError("Layer is already at the edge of the stack.")
            def reorder():
                layer = doc.layers.pop(doc.active)
                doc.layers.insert(index, layer)
                doc.active = index
            return self._edit("Reorder layer", reorder)
        if action == "mask":
            _count(args, 1)
            mode = args[0]
            if mode not in ("selection", "white", "black", "invert", "apply", "remove"):
                raise CommandError(HELP["layer"])
            doc.ensure_editable()
            if mode == "selection" and doc.selection is None:
                raise CommandError("Select an area before creating a selection mask.")
            if mode in ("invert", "apply", "remove") and doc.layer.mask is None:
                raise CommandError("The active layer has no mask.")
            def mask():
                if mode == "selection": doc.layer.mask = doc.selection.copy()
                elif mode in ("white", "black"): doc.layer.mask = Image.new("L", doc.size, 255 if mode == "white" else 0)
                elif mode == "invert": doc.layer.mask = ImageOps.invert(doc.layer.mask)
                elif mode == "apply":
                    doc.layer.image.putalpha(ImageChops.multiply(doc.layer.image.getchannel("A"), doc.layer.mask))
                    doc.layer.mask = None
                else: doc.layer.mask = None
            return self._edit(f"Layer mask {mode}", mask)
        _count(args, 0)
        operations = {"duplicate": doc.duplicate, "delete": doc.delete_layer, "merge": doc.merge_down}
        if action == "flatten":
            if any(layer.locked for layer in doc.layers): raise CommandError("Unlock all layers before flattening.")
            def flatten():
                doc.layers = [Layer("Flattened", doc.composite())]
                doc.active = 0
            return self._edit("Flatten visible layers", flatten)
        if action in operations: return self._edit(f"Layer {action}", operations[action])
        raise CommandError("Unknown layer action. " + HELP["layer"])

    def _select(self, tokens):
        doc = self.document
        args, opts = _args(tokens, ("mode", "tolerance"), ("merged",))
        if not args: raise CommandError(HELP["select"])
        kind, rest = args[0].lower(), args[1:]
        kind = {"rect": "rectangle"}.get(kind, kind)
        mode = opts.get("mode", "replace")
        if mode not in ("replace", "add", "subtract", "intersect"):
            raise CommandError("Selection mode must be replace, add, subtract or intersect.")
        if kind in ("all", "none", "invert"):
            _count(rest, 0)
            if opts: raise CommandError(f"select {kind} does not take options.")
            def operation():
                if kind == "all": doc.selection = Image.new("L", doc.size, 255)
                elif kind == "none": doc.selection = None
                else: doc.selection = ImageOps.invert(doc.selection if doc.selection is not None else Image.new("L", doc.size))
        elif kind == "feather":
            _count(rest, 1)
            if opts: raise CommandError("select feather does not take options.")
            if doc.selection is None: raise CommandError("Select an area before feathering.")
            radius = _number(rest[0], 0, 100)
            operation = lambda: setattr(doc, "selection", doc.selection.filter(ImageFilter.GaussianBlur(radius)))
        elif kind in ("rectangle", "ellipse"):
            _count(rest, 4)
            if set(opts)-{"mode"}: raise CommandError("Shape selections only accept --mode.")
            points = list(map(_integer, rest))
            operation = lambda: doc.select(kind, tuple(points[:2]), tuple(points[2:]), mode)
        elif kind == "lasso":
            if set(opts)-{"mode"}: raise CommandError("Lasso selections only accept --mode.")
            points = _points(rest, 3)
            operation = lambda: doc.select("lasso", points[0], points[-1], mode, points)
        elif kind == "wand":
            _count(rest, 2)
            point = tuple(map(_integer, rest))
            self._inside(point)
            tolerance = _integer(opts.get("tolerance", self.setting("tolerance")), 0, 255)
            mask = doc.region_mask(point, tolerance, bool(opts.get("merged")))
            operation = lambda: doc.set_selection(mask, mode)
        else:
            raise CommandError("Unknown selection type. " + HELP["select"])
        return self._edit(f"Select {kind}", operation)

    def _transform(self, tokens):
        doc = self.document
        _count(tokens, 1, 2)
        kind = tokens[0].replace("-", "_")
        if kind in ("flip_h", "flip_v", "rotate_cw", "rotate_ccw"):
            _count(tokens, 1)
            return self._edit(f"Transform {kind}", lambda: doc.transform_layer(kind))
        _count(tokens, 2)
        if kind == "rotate":
            angle = _number(tokens[1], -3600, 3600)
            function = lambda image: image.rotate(angle, Image.Resampling.BICUBIC, expand=True)
        elif kind == "scale":
            size = _size(tokens[1])
            function = lambda image: image.resize(size, Image.Resampling.LANCZOS)
        else: raise CommandError(HELP["transform"])
        def operation():
            doc.ensure_editable()
            def centered(image):
                transformed = function(image)
                result = Image.new(image.mode, doc.size)
                result.paste(transformed, ((doc.width-transformed.width)//2, (doc.height-transformed.height)//2))
                return result
            doc.layer.image = centered(doc.layer.image)
            if doc.layer.mask is not None: doc.layer.mask = centered(doc.layer.mask)
        return self._edit(f"Transform {kind}", operation)

    def _palette(self, tokens):
        tokens, _ = _args(tokens)
        _count(tokens, 1, 65)
        doc = self.document
        kind, args = tokens[0], tokens[1:]
        colors = doc.settings["palette"].copy()
        if kind == "list":
            _count(args, 0)
            return CommandResult(" ".join(f"{n+1}:{value}" for n, value in enumerate(colors)))
        if kind == "set":
            _count(args, 1, 64)
            colors = [_color(value) for value in args]
        elif kind == "add":
            _count(args, 1)
            if len(colors) == 64: raise CommandError("At most 64 palette colors.")
            colors.append(_color(args[0]))
        elif kind == "remove":
            _count(args, 1)
            if len(colors) == 1: raise CommandError("Keep at least one palette color.")
            colors.pop(_integer(args[0], 1, len(colors))-1)
        else: raise CommandError(HELP["palette"])
        return self._edit("Edit palette", lambda: doc.settings.update(palette=colors))

    def run_script(self, text, source="<script>", atomic=True, base_dir=None):
        """Fail fast with line numbers; atomic rollback includes all in-memory state.

        Files already saved/exported by preceding lines stay on disk. Nonatomic
        mode keeps successful earlier commands and their undo history. Neither
        mode silently ignores an error. Paths are relative to base_dir when set.
        """
        if self.script_depth >= 8:
            raise ScriptError("Scripts may nest at most 8 levels.")
        original_doc, original_path, original_base = self.document, self.project_path, self.base_dir
        backup = copy.deepcopy(original_doc.__dict__) if atomic else None
        self.script_depth += 1
        if base_dir is not None: self.base_dir = Path(base_dir).resolve()
        results = []
        try:
            for line_number, line in enumerate(text.splitlines(), 1):
                try:
                    result = self.execute(line)
                    if result.quit_requested:
                        raise CommandError("quit is only valid in an interactive session.")
                    if result.text: results.append(result)
                except CommandError as error:
                    suffix = " In-memory changes rolled back; files already written remain. Saved preferences also remain." if atomic else " Earlier commands remain applied."
                    raise ScriptError(f"{source}:{line_number}: {error}{suffix}") from error
            return results
        except Exception:
            if atomic:
                original_doc.__dict__.clear()
                original_doc.__dict__.update(backup)
                self.document, self.project_path = original_doc, original_path
                self._apply_document_config()
            raise
        finally:
            self.script_depth -= 1
            self.base_dir = original_base
            publish_diagnostics(self.document, self.config, stage="ready")
