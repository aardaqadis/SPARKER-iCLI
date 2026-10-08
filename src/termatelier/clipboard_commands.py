"""Pixel clipboard commands shared by the terminal, REPL and batch editor.

The clipboard belongs to the editor session, not the operating system. File
roundtrips preserve its exact RGBA pixels independently of display sampling.
"""
from __future__ import annotations

import json

from PIL import Image

from .model import valid_size
from .storage import atomic_write, open_image, resolve_export_path


HELP = {
    "copy": "copy [--merged] [--box X0,Y0,X1,Y1] — copy rendered active-layer or all visible pixels; box bounds are half-open and intersect selection",
    "cut": "cut [--box X0,Y0,X1,Y1] — copy and erase selected active-layer pixels; one undo step",
    "paste": "paste [X Y | --x X --y Y | --center | --in-place] [--into | --name NAME] [--opacity 0..1] [--scale WIDTHxHEIGHT] [--rotate DEGREES] [--flip-h] [--flip-v] [--grid COLSxROWS] [--gap X,Y] — transformed clipboard copy; nearest pixel sampling",
    "clipboard": "clipboard [info [--json] | clear | load IMAGE | save [PATH.png|.webp|.tiff]] — inspect, import or save the session's exact RGBA clipboard; exports stay outside the project",
}


def execute_clipboard(session, command, tokens):
    """Return CommandResult for clipboard commands, or None for another family."""
    if command not in HELP:
        return None
    # Deferred import keeps HELP available to central commands without a cycle.
    from .commands import CommandError, CommandResult, _args, _count, _fraction, _integer, _number, _size

    doc = session.document
    if command == "clipboard":
        explicit_action = bool(tokens) and not tokens[0].startswith("--")
        action = tokens[0].lower() if explicit_action else "info"
        args, opts = _args(tokens[1:] if explicit_action else tokens, flags=("json",))
        if action == "info":
            _count(args, 0)
            if doc.clipboard is None:
                data = {"empty": True}
                return CommandResult(json.dumps(data) if opts.get("json") else "Clipboard is empty.")
            image, origin = doc.clipboard
            data = {"empty": False, "width": image.width, "height": image.height,
                    "mode": image.mode, "origin": list(origin),
                    "pixel_bytes": image.width * image.height * len(image.getbands()),
                    "alpha_range": list(image.getchannel("A").getextrema())}
            return CommandResult(json.dumps(data, indent=2) if opts.get("json") else
                                 f"Clipboard: {image.width}×{image.height} {image.mode}, origin {origin[0]},{origin[1]}, "
                                 f"{data['pixel_bytes']:,} pixel bytes, alpha {data['alpha_range'][0]}..{data['alpha_range'][1]}.")
        if opts:
            raise CommandError("--json applies to clipboard info.")
        if action == "clear":
            _count(args, 0)
            doc.clipboard = None
            return CommandResult("Cleared the session clipboard.")
        if action == "load":
            _count(args, 1)
            image = open_image(session._path(args[0]))
            doc.clipboard = (image, (0, 0))
            return CommandResult(f"Loaded {image.width}×{image.height} RGBA clipboard pixels.")
        if action == "save":
            _count(args, 0, 1)
            if doc.clipboard is None:
                raise CommandError("Copy a selection or load a clipboard image first.")
            path = resolve_export_path(args[0] if args else "clipboard.png", config=session.config)
            formats = {".png": "PNG", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF"}
            extension = path.suffix.lower()
            if extension not in formats:
                raise CommandError("Clipboard save preserves RGBA pixels; use PNG, lossless WebP or TIFF.")
            image = doc.clipboard[0]
            options = ({"lossless": True, "exact": True, "method": 6} if extension == ".webp" else
                       {"compression": "tiff_deflate"} if extension in (".tif", ".tiff") else {})
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, lambda temporary: image.save(temporary, formats[extension], **options))
            return CommandResult(f"Saved exact {image.width}×{image.height} clipboard pixels to {path}.")
        raise CommandError(HELP["clipboard"])

    if command in ("copy", "cut"):
        args, opts = _args(tokens, ("box",), flags=("merged",))
        _count(args, 0)
        if command == "cut" and opts.get("merged"):
            raise CommandError("Cut edits the active layer; use copy --merged for all visible pixels.")
        box = None
        if "box" in opts:
            parts = opts["box"].split(",")
            _count(parts, 4)
            box = tuple(_integer(value) for value in parts)
        if command == "copy":
            doc.copy_selection(merged=bool(opts.get("merged")), box=box)
            return CommandResult(f"Copied {doc.clipboard[0].width}×{doc.clipboard[0].height} pixels.")
        previous = doc.clipboard
        try:
            return session._edit("Cut", lambda: doc.copy_selection(True, box=box))
        except Exception:
            # Document snapshots intentionally omit clipboard session state.
            doc.clipboard = previous
            raise

    args, opts = _args(tokens, ("x", "y", "name", "opacity", "scale", "rotate", "grid", "gap"),
                       flags=("center", "in-place", "into", "flip-h", "flip-v"))
    if len(args) not in (0, 2):
        raise CommandError("Paste coordinates require both X and Y.")
    if doc.clipboard is None:
        raise CommandError("Copy a selection or load a clipboard image first.")
    explicit = bool(args) or "x" in opts or "y" in opts
    if sum((explicit, bool(opts.get("center")), bool(opts.get("in-place")))) > 1:
        raise CommandError("Choose paste coordinates, --center or --in-place.")
    if args and ("x" in opts or "y" in opts):
        raise CommandError("Choose X Y or --x/--y coordinates.")
    if opts.get("into") and "name" in opts:
        raise CommandError("--name creates a new layer; omit it when using --into.")
    name = opts.get("name", "Pasted")
    if len(name) > 256:
        raise CommandError("Layer names must contain at most 256 characters.")
    opacity = _fraction(opts.get("opacity", 1))
    image, origin = doc.clipboard
    # Prepare and validate every transform before opening the edit transaction.
    if "scale" in opts:
        image = image.resize(_size(opts["scale"]), Image.Resampling.NEAREST)
    if opts.get("flip-h"):
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if opts.get("flip-v"):
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if "rotate" in opts:
        angle = _number(opts["rotate"], -360, 360)
        image = image.rotate(angle, resample=Image.Resampling.NEAREST, expand=True)
        valid_size(*image.size)
    if opts.get("center"):
        position = ((doc.width - image.width) // 2, (doc.height - image.height) // 2)
    elif args:
        position = tuple(_integer(value, -16384, 16384) for value in args)
    else:
        position = (_integer(opts.get("x", origin[0]), -16384, 16384),
                    _integer(opts.get("y", origin[1]), -16384, 16384))
    cols, rows = (1, 1)
    if "grid" in opts:
        parts = opts["grid"].lower().split("x")
        _count(parts, 2)
        cols, rows = (_integer(value, 1, 256) for value in parts)
        if cols * rows > 256:
            raise CommandError("Paste grids contain at most 256 copies.")
    gap = (0, 0)
    if "gap" in opts:
        if "grid" not in opts:
            raise CommandError("--gap requires --grid COLSxROWS.")
        parts = opts["gap"].split(",")
        _count(parts, 2)
        gap = tuple(_integer(value, -4096, 4096) for value in parts)
    positions = [(position[0] + col * (image.width + gap[0]),
                  position[1] + row * (image.height + gap[1]))
                 for row in range(rows) for col in range(cols)]
    return session._edit("Paste", lambda: doc.paste(image=image, positions=positions,
                                                    into=bool(opts.get("into")), name=name, opacity=opacity))
