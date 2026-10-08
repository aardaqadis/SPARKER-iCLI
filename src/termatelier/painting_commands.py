"""Command adapters for native painting and image-assisted selections."""
from __future__ import annotations

from .painting_tools import (NATURAL_PRESETS, apply_stroke, color_selection,
                             foreground_selection, perspective_coefficients,
                             scissors_selection)

HELP = {
    "clone": "clone X,Y [X,Y ...] --source X,Y [--size 1..512] [--hardness 0..1] [--opacity N] [--merged] — copy aligned pixels from a frozen source",
    "heal": "heal X,Y [X,Y ...] --source X,Y [--size 1..512] [--radius .1..64] [--hardness N] [--opacity N] [--merged] — source texture with destination local color and alpha",
    "perspective-clone": 'perspective-clone X,Y [X,Y ...] --source-quad "X,Y;X,Y;X,Y;X,Y" --target-quad "X,Y;X,Y;X,Y;X,Y" [--size N] [--hardness N] [--opacity N] [--merged] — project source into a convex quadrilateral',
    "smudge": "smudge X,Y X,Y [X,Y ...] [--size 1..512] [--strength N] [--hardness N] [--opacity N] — carry sampled pigment continuously along a stroke",
    "retouch": "retouch blur|sharpen|dodge|burn X,Y [X,Y ...] [--size N] [--strength N] [--radius .1..64] [--range shadows|midtones|highlights|all] [--exposure 0..4] [--hardness N] [--opacity N] — local pixel retouching, preserving alpha",
    "airbrush": "airbrush X,Y [X,Y ...] [--duration .01..60] [--rate .1..120] [--size N] [--color COLOR] [--hardness N] [--opacity N] — timed accumulating spray flow",
    "ink": "ink X,Y [X,Y ...] [--angle -360..360] [--aspect .05..1] [--pressure .05..1] [--size N] [--color COLOR] [--hardness N] [--opacity N] — oriented elliptical calligraphy nib",
    "natural": "natural list | natural PRESET_OR_NATURAL_MEDIA_ID X,Y [X,Y ...] [--size N] [--seed N] [--density .1..4] [--angle N] [--color COLOR] [--opacity N] — native textured tips: chalk, charcoal, dry, bristle, sponge, fan, rake, splatter",
    "select-color": "select-color X Y | --color COLOR [--tolerance 0..255] [--mode replace|add|subtract|intersect] [--feather 0..64] [--merged] — all matching color islands, not only connected pixels",
    "scissors": "scissors X,Y X,Y X,Y [X,Y ...] [--resolution 32..512] [--feather N] [--mode MODE] [--merged] — edge-following closed selection with bounded working resolution",
    "foreground": 'foreground X,Y X,Y X,Y [X,Y ...] --marks "X,Y;X,Y" [--tolerance 0..255] [--smooth 0..8] [--mode MODE] [--merged] — seeded color segmentation inside an approximate polygon',
}


def execute_painting(session, command, tokens):
    """Return a transactional command result, or None for another adapter."""
    if command not in HELP:
        return None
    from .commands import CommandError, CommandResult, _args, _color, _count, _fraction, _integer, _number
    from .painting_tools import checked_points

    def points(values, minimum=1, maximum=2048):
        result = []
        _count(values, minimum, maximum)
        for value in values:
            pair = value.split(",")
            _count(pair, 2)
            result.append(tuple(_integer(v, -32768, 32768) for v in pair))
        return checked_points(result, minimum, maximum)

    def quad(value):
        return points(value.split(";"), 4, 4)

    doc = session.document
    if command in ("select-color", "scissors", "foreground"):
        options = ("mode", "tolerance", "feather", "color") if command == "select-color" else (
            ("mode", "resolution", "feather") if command == "scissors" else ("mode", "marks", "tolerance", "smooth"))
        args, opts = _args(tokens, options, ("merged",))
        mode = opts.get("mode", "replace").lower()
        if mode not in ("replace", "add", "subtract", "intersect"):
            raise CommandError("Selection mode must be replace, add, subtract or intersect.")
        if command == "select-color":
            if "color" in opts:
                _count(args, 0)
                point, color = None, _color(opts["color"])
            else:
                if len(args) == 1:
                    point = points(args)[0]
                else:
                    _count(args, 2)
                    point = tuple(_integer(v, 0, limit - 1) for v, limit in zip(args, doc.size))
                color = None
            mask = color_selection(doc, point, color=color,
                                   tolerance=_number(opts.get("tolerance", session.setting("tolerance")), 0, 255),
                                   merged=bool(opts.get("merged")), feather=_number(opts.get("feather", 0), 0, 64))
        elif command == "scissors":
            mask = scissors_selection(doc, points(args, 3, 32), merged=bool(opts.get("merged")),
                                      resolution=_integer(opts.get("resolution", 256), 32, 512),
                                      feather=_number(opts.get("feather", 0), 0, 64))
        else:
            if "marks" not in opts:
                raise CommandError('Mark the foreground with --marks "X,Y;X,Y".')
            mask = foreground_selection(doc, points(args, 3, 64), points(opts["marks"].split(";"), 1, 32),
                                        merged=bool(opts.get("merged")),
                                        tolerance=_number(opts.get("tolerance", 80), 0, 255),
                                        smooth=_number(opts.get("smooth", 0), 0, 8))
        return session._edit(command.capitalize() + " selection", lambda: doc.set_selection(mask, mode))

    common = ("size", "hardness", "opacity")
    extra = {
        "clone": ("source",), "heal": ("source", "radius"),
        "perspective-clone": ("source-quad", "target-quad"), "smudge": ("strength",),
        "retouch": ("strength", "radius", "range", "exposure"),
        "airbrush": ("duration", "rate", "color"),
        "ink": ("angle", "aspect", "pressure", "color"),
        "natural": ("seed", "density", "angle", "color"),
    }
    args, opts = _args(tokens, common + extra[command], ("merged",) if command in ("clone", "heal", "perspective-clone") else ())
    tool = command
    if command == "retouch":
        if not args or args[0].lower() not in ("blur", "sharpen", "dodge", "burn"):
            raise CommandError(HELP["retouch"])
        tool, args = args[0].lower(), args[1:]
    if command == "natural":
        if args == ["list"] and not opts:
            return CommandResult("\n".join(f"{name}: {identifier}" for name, identifier in NATURAL_PRESETS.items()))
        if not args:
            raise CommandError(HELP["natural"])
        preset, args = args[0], args[1:]
    path = points(args, 2 if command == "smudge" else 1)
    kwargs = {
        "size": _integer(opts.get("size", session.setting("brush_size")), 1, 512),
        "hardness": _fraction(opts.get("hardness", session.setting("hardness"))),
        "opacity": _fraction(opts.get("opacity", session.setting("opacity"))),
    }
    if "color" in opts or command in ("airbrush", "ink", "natural"):
        kwargs["color"] = _color(opts.get("color", session.setting("foreground")))
    if command in ("clone", "heal"):
        if "source" not in opts:
            raise CommandError("Set a source point with --source X,Y.")
        kwargs["source"] = points([opts["source"]])[0]
        kwargs["merged"] = bool(opts.get("merged"))
    if command == "perspective-clone":
        if "source-quad" not in opts or "target-quad" not in opts:
            raise CommandError("Perspective cloning requires --source-quad and --target-quad.")
        kwargs["source_quad"], kwargs["dest_quad"] = quad(opts["source-quad"]), quad(opts["target-quad"])
        perspective_coefficients(kwargs["source_quad"], kwargs["dest_quad"])
        kwargs["merged"] = bool(opts.get("merged"))
    if command == "retouch":
        kwargs["tonal_range"] = opts.get("range", "midtones").lower()
    if command == "natural":
        kwargs["preset"] = preset
    fractions = {"strength": (0, 1), "radius": (.1, 64), "exposure": (0, 4),
                 "duration": (.01, 60), "rate": (.1, 120), "angle": (-360, 360),
                 "aspect": (.05, 1), "pressure": (.05, 1), "density": (.1, 4)}
    for name, bounds in fractions.items():
        if name in opts:
            kwargs[name] = _fraction(opts[name]) if name == "strength" else _number(opts[name], *bounds)
    if "seed" in opts:
        kwargs["seed"] = _integer(opts["seed"], -2**31, 2**31 - 1)
    doc.ensure_editable()
    return session._edit(tool.replace("-", " ").title(), lambda: apply_stroke(doc, tool, path, **kwargs))
