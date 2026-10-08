"""Validated mouse preferences and a truthful overview of tool families."""
from __future__ import annotations

import json
import math

from .painting_tools import MOUSE_TOOLS, NATURAL_PRESETS, perspective_coefficients
from .geometry_tools import OPERATIONS
from .adjustment_tools import ADJUSTMENTS, EFFECTS

HELP = {
    "features": "features [QUERY] [--json] — named tool families, available commands and implementation limits",
    "retouch-options": "retouch-options [JSON_OBJECT|reset] — mouse brush strength, radius, tonal_range, exposure, rate, angle, aspect, pressure, preset, seed, density, merged, source_quad, dest_quad",
}


def validate_retouch_options(value):
    if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False)) > 8192:
        raise ValueError("Retouch options must be a JSON object of at most 8 KiB.")
    ranges = {"strength": (0, 1), "radius": (.1, 64), "exposure": (0, 4),
              "rate": (.1, 120), "angle": (-360, 360), "aspect": (.05, 1),
              "pressure": (.05, 1), "density": (.1, 4)}
    allowed = {*ranges, "tonal_range", "preset", "seed", "merged", "source_quad", "dest_quad"}
    if set(value) - allowed:
        raise ValueError("Unknown retouch option: " + ", ".join(sorted(set(value) - allowed)))
    for name, (low, high) in ranges.items():
        if name in value and (type(value[name]) not in (int, float) or
                not math.isfinite(value[name]) or not low <= value[name] <= high):
            raise ValueError(f"{name} must be a finite number from {low} to {high}.")
    if "merged" in value and type(value["merged"]) is not bool:
        raise ValueError("merged must be true or false.")
    if "seed" in value and (type(value["seed"]) is not int or not 0 <= value["seed"] <= 2**31 - 1):
        raise ValueError("seed must be an integer from 0 to 2147483647.")
    if "tonal_range" in value and value["tonal_range"] not in ("all", "shadows", "midtones", "highlights"):
        raise ValueError("tonal_range must be all, shadows, midtones or highlights.")
    if "preset" in value:
        from .tool_library import get_tool
        if not isinstance(value["preset"], str): raise ValueError("preset must be a brush name.")
        spec = get_tool(NATURAL_PRESETS.get(value["preset"], value["preset"]))
        if spec.category != "natural-media" or spec.kind != "brush":
            raise ValueError("Use a natural-media brush preset.")
    if "source_quad" in value or "dest_quad" in value:
        perspective_coefficients(value.get("source_quad", ()), value.get("dest_quad", ()))
    return value


def feature_families():
    return {
        "painting": {"tools": sorted(MOUSE_TOOLS | {"brush", "pencil", "eraser", "fill", "gradient"}),
                     "commands": ["stroke", "clone", "heal", "perspective-clone", "smudge", "retouch", "airbrush", "ink", "natural", "retouch-options"],
                     "notes": "Native raster algorithms and natural-media tips; no libMyPaint pressure/tilt engine. Ctrl-click sets clone/heal source; airbrush flows while held."},
        "selection": {"tools": ["rectangle", "ellipse", "lasso", "wand", "by-color", "scissors", "foreground"],
                      "commands": ["select", "select-color", "scissors", "foreground", "selection", "channel"],
                      "notes": "Scissors follows edges at a bounded working resolution; foreground uses marked color samples. Channels preserve grayscale selections."},
        "geometry": {"tools": sorted(OPERATIONS), "commands": ["geometry", "transform", "align", "distribute", "measure", "crop", "resize", "canvas"],
                     "notes": "Raster transforms clip to the canvas; 3D is a planar perspective projection, cage is weighted point deformation. Transformed text can be baked; undo restores editing."},
        "tone": {"tools": sorted(ADJUSTMENTS), "commands": ["tone", "adjust", "filter"],
                 "notes": "Curves, levels and color adjustments preserve alpha unless alpha is explicitly adjusted."},
        "effects": {"tools": sorted(EFFECTS), "commands": ["effect-filter", "fx", "tools"],
                    "notes": "Editable stacks preserve source pixels in .tart. Native filters and procedural art tools; no GEGL plugin engine."},
        "document": {"tools": ["layers", "masks", "blend-modes", "paths", "channels", "editable-text", "clipboard", "palettes", "grid", "guides", "undo", "history"],
                     "commands": ["layer", "path", "channel", "text", "text-edit", "fonts", "copy", "cut", "paste", "palette", "grid", "guides", "undo", "redo"],
                     "notes": "Advanced text layout with editable source; cubic paths remain editable. Independent raster layers up to 64; nested layer groups are not implemented."},
        "files-and-ai": {"tools": ["native-tart", "png", "webp", "tiff", "openraster", "ascii", "ansi", "mcp-http", "mcp-stdio"],
                         "commands": ["files", "open", "import", "save", "export", "mcp", "ai"],
                         "notes": "Exact canvas exports, optional crisp integer enlargement. ORA preserves raster layers with supported blends. AI requires your configured external MCP service; named tools are discovered and explicitly mapped."},
    }


def execute_native(session, command, tokens):
    if command not in HELP: return None
    from .commands import CommandResult, _args, _count
    if command == "retouch-options":
        _count(tokens, 0, 1)
        if tokens:
            value = {} if tokens[0] == "reset" else json.loads(tokens[0])
            session.document.settings["retouch_options"] = validate_retouch_options(value)
        return CommandResult(json.dumps(session.document.settings.get("retouch_options", {}), indent=2))
    args, options = _args(tokens, flags=("json",))
    _count(args, 0, 1)
    query = args[0].lower() if args else ""
    result = {name: value for name, value in feature_families().items()
              if query in name or query in json.dumps(value).lower()}
    if options.get("json"): return CommandResult(json.dumps(result, indent=2))
    return CommandResult("\n\n".join(f"{name}: {', '.join(value['tools'])}\nCommands: {', '.join(value['commands'])}\n{value['notes']}"
                                    for name, value in result.items()) or "No matching feature family.")
