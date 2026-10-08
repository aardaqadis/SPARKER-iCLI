"""Editable paths, saved channels, text sources and layer effect stacks."""
from __future__ import annotations

import base64
import copy
import io
import json
import math

from PIL import Image, ImageDraw

MAX_CHANNEL_BYTES = 8 * 1024 * 1024
MAX_EFFECTS = 16

HELP = {
    "path": "path list [--json] | new NAME X,Y ... [--closed] | curve NAME START CONTROL CONTROL END ... [--closed] | point NAME INDEX X,Y | close NAME | delete NAME | stroke NAME [--width N] [--color COLOR] [--opacity N] | fill NAME [--color COLOR] | select NAME [--mode replace|add|subtract|intersect] | move NAME DX DY — editable polygon/Bézier paths saved in .tart",
    "channel": "channel list [--json] | save NAME [--from selection|red|green|blue|alpha] | load NAME [--mode replace|add|subtract|intersect] | mask NAME | delete NAME — saved grayscale selection/color channels, up to 8",
    "fx": "fx list [--json] | add tone|filter NAME [--args JSON] [--opacity N] | edit INDEX [--args JSON] [--opacity N] | show INDEX | hide INDEX | remove INDEX | reorder INDEX POSITION | bake — editable layer effects; original pixels retained until bake, indices start at 1",
    "text-edit": 'text-edit "WORDS" [--size N] [--color COLOR] [--font PATH] [--wrap N] [--align left|center|right] — regenerate the active editable text layer from its saved source',
    "tool-source": "tool-source X Y — set the clone/heal source; Ctrl+click also sets it in the painter",
}


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError("Use a name containing 1..128 printable characters.")
    return value


def encode_channel(image):
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def decode_channel(value, size):
    if not isinstance(value, str) or len(value) > MAX_CHANNEL_BYTES:
        raise ValueError("Saved channel data exceeds the channel budget.")
    try:
        raw = base64.b64decode(value, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.mode != "L" or image.size != tuple(size):
                raise ValueError("Saved channels must be canvas-sized grayscale PNGs.")
            return image.copy()
    except (ValueError, OSError) as error:
        raise ValueError(f"Invalid saved channel: {error}") from error


def validate_metadata(metadata, size):
    paths, channels = metadata.get("paths", {}), metadata.get("channels", {})
    if not isinstance(paths, dict) or len(paths) > 64:
        raise ValueError("A project supports at most 64 editable paths.")
    for name, path in paths.items():
        _name(name)
        if not isinstance(path, dict) or set(path) - {"kind", "points", "closed"}:
            raise ValueError("Invalid editable path structure.")
        points = path.get("points")
        if (path.get("kind") not in ("polyline", "bezier") or type(path.get("closed")) is not bool or
                not isinstance(points, list) or not 2 <= len(points) <= 4096):
            raise ValueError("Invalid editable path geometry.")
        if path["kind"] == "bezier" and (len(points) < 4 or (len(points) - 1) % 3):
            raise ValueError("Cubic paths require a start and groups of three control/end points.")
        for point in points:
            if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                    any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 32768 for v in point)):
                raise ValueError("Path coordinates must be finite and within ±32,768.")
    if (not isinstance(channels, dict) or len(channels) > 8 or
            sum(len(v) if isinstance(v, str) else MAX_CHANNEL_BYTES + 1 for v in channels.values()) > MAX_CHANNEL_BYTES):
        raise ValueError("Saved channels exceed 8 channels / 8 MiB encoded data.")
    for name, data in channels.items():
        _name(name)
        decode_channel(data, size)


def validate_effects(effects):
    if not isinstance(effects, list) or len(effects) > MAX_EFFECTS:
        raise ValueError("A layer supports at most 16 editable effects.")
    from .adjustment_tools import validate_adjustment, validate_effect
    for item in effects:
        if (not isinstance(item, dict) or set(item) != {"kind", "name", "options", "enabled", "opacity"} or
                item["kind"] not in ("tone", "filter") or type(item["enabled"]) is not bool or
                type(item["opacity"]) not in (int, float) or not math.isfinite(item["opacity"]) or
                not 0 <= item["opacity"] <= 1 or not isinstance(item["options"], dict)):
            raise ValueError("Invalid layer effect entry.")
        (validate_adjustment if item["kind"] == "tone" else validate_effect)(item["name"], item["options"])
    return effects


def render_effects(image, effects):
    from .adjustment_tools import apply_adjustment, apply_effect
    result = image
    for item in effects:
        if item["enabled"] and item["opacity"]:
            processed = (apply_adjustment if item["kind"] == "tone" else apply_effect)(
                result, item["name"], **item["options"])
            result = processed if item["opacity"] == 1 else Image.blend(result, processed, item["opacity"])
    return result


def path_points(path):
    points = path["points"]
    if path["kind"] == "polyline":
        result = [tuple(point) for point in points]
    else:
        result = [tuple(points[0])]
        for index in range(0, len(points) - 1, 3):
            a, b, c, d = points[index:index + 4]
            steps = max(8, min(256, math.ceil((math.dist(a, b) + math.dist(b, c) + math.dist(c, d)) / 2)))
            for step in range(1, steps + 1):
                t, u = step / steps, 1 - step / steps
                result.append(tuple(u**3*a[axis] + 3*u*u*t*b[axis] + 3*u*t*t*c[axis] + t**3*d[axis]
                                    for axis in (0, 1)))
            if len(result) > 50_000:
                raise ValueError("Path exceeds 50,000 sampled vertices.")
    if path["closed"] and result[-1] != result[0]:
        result.append(result[0])
    return result


def validate_text_recipe(recipe):
    if recipe is None:
        return
    if not isinstance(recipe, dict) or len(json.dumps(recipe, allow_nan=False)) > 131072:
        raise ValueError("Invalid editable text source.")
    from .text_tools import TextStyle, _validate_style, normalize_text
    fields = {"point", "text", "color", "size", "font_path", "opacity", "wrap", "align", "spacing",
              "letter_spacing", "stroke", "stroke_color", "shadow", "shadow_color", "rotation", "anchor"}
    if set(recipe) != fields:
        raise ValueError("Invalid editable text fields.")
    normalize_text(recipe["text"])
    if type(recipe["size"]) is not int or not 1 <= recipe["size"] <= 512:
        raise ValueError("Editable text size must be an integer from 1 to 512.")
    Image.new("RGBA", (1, 1), recipe["color"])
    point = recipe["point"]
    if (not isinstance(point, (list, tuple)) or len(point) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1_000_000 for v in point)):
        raise ValueError("Invalid editable text position.")
    if not isinstance(recipe["font_path"], str) or len(recipe["font_path"]) > 8192:
        raise ValueError("Invalid editable font path.")
    style_args = {key: value for key, value in recipe.items() if key not in ("point", "text", "color")}
    _validate_style(TextStyle(**style_args))


def transform_metadata(doc, old_size, new_size, *, crop=None, resample=True):
    """Keep editable paths and saved channels aligned with a resized canvas."""
    sx, sy = (new_size[0] / old_size[0], new_size[1] / old_size[1]) if resample else (1, 1)
    dx, dy = (-crop[0], -crop[1]) if crop else (0, 0)
    for path in doc.metadata.get("paths", {}).values():
        path["points"] = [[x * sx + dx, y * sy + dy] for x, y in path["points"]]
    for name, data in list(doc.metadata.get("channels", {}).items()):
        mask = decode_channel(data, old_size)
        if crop:
            mask = mask.crop(crop)
        elif resample:
            mask = mask.resize(new_size, Image.Resampling.NEAREST)
        else:
            enlarged = Image.new("L", new_size)
            enlarged.paste(mask, (0, 0))
            mask = enlarged
        doc.metadata["channels"][name] = encode_channel(mask)
    for layer in doc.layers:
        if layer.text_recipe:
            x, y = layer.text_recipe["point"]
            layer.text_recipe["point"] = [x * sx + dx, y * sy + dy]
            if resample:
                layer.text_recipe["size"] = max(1, min(512, round(layer.text_recipe["size"] * min(sx, sy))))
    validate_metadata(doc.metadata, new_size)


def execute_document(session, command, tokens):
    if command not in HELP:
        return None
    from .commands import CommandError, CommandResult, _args, _color, _count, _fraction, _integer, _number, _points
    doc = session.document
    if command == "tool-source":
        _count(tokens, 2)
        point = [_integer(tokens[0], 0, doc.width - 1), _integer(tokens[1], 0, doc.height - 1)]
        doc.settings["clone_source"] = point
        return CommandResult(f"Clone/heal source: {point[0]},{point[1]}.")
    if command == "text-edit":
        args, opts = _args(tokens, ("size", "color", "font", "wrap", "align"))
        _count(args, 1)
        doc.ensure_editable()
        if doc.layer.text_recipe is None:
            raise CommandError("Create an editable text layer with text ... --layer NAME first.")
        recipe = copy.deepcopy(doc.layer.text_recipe)
        recipe["text"] = args[0]
        for key, value in opts.items():
            if key in ("size", "wrap"):
                value = _integer(value, 1, 512 if key == "size" else 4096)
            elif key == "color":
                value = _color(value)
            elif key == "font":
                key, value = "font_path", str(session._path(value))
            recipe[key] = value
        def reflow():
            doc.layer.image = Image.new("RGBA", doc.size)
            recipe_args = dict(recipe)
            point = recipe_args.pop("point")
            words = recipe_args.pop("text")
            color = recipe_args.pop("color")
            doc.text(point, words, color, **recipe_args)
            doc.layer.text_recipe = recipe
        return session._edit("Edit text source", reflow)
    if command == "fx":
        action = tokens[0].lower() if tokens else "list"
        args, opts = _args(tokens[1:], ("args", "opacity"), ("json",))
        effects = doc.layer.effects
        if action == "list":
            _count(args, 0)
            if set(opts) - {"json"}:
                raise CommandError(HELP[command])
            return CommandResult(json.dumps(effects, indent=2) if opts.get("json") else
                "\n".join(f"{i+1}. {e['kind']} {e['name']} · {'visible' if e['enabled'] else 'hidden'} · {e['opacity']:.0%} · {json.dumps(e['options'])}"
                          for i, e in enumerate(effects)) or "No editable layer effects.")
        doc.ensure_editable()
        replacement = copy.deepcopy(effects)
        if "json" in opts:
            raise CommandError("--json applies to fx list.")
        options = None
        if "args" in opts:
            if len(opts["args"]) > 16_384:
                raise CommandError("Effect arguments must fit in 16 KiB.")
            options = json.loads(opts["args"])
            if not isinstance(options, dict):
                raise CommandError("Effect arguments must be a JSON object.")
        if action == "add":
            _count(args, 2)
            replacement.append({"kind": args[0], "name": args[1], "options": options or {}, "enabled": True,
                                "opacity": _fraction(opts.get("opacity", 1))})
        elif action == "bake":
            _count(args, 0)
            if opts:
                raise CommandError(HELP[command])
            baked = doc.layer.pixels_with_effects().copy()
            def bake():
                doc.layer.image, doc.layer.effects = baked, []
            return session._edit("Bake layer effects", bake)
        else:
            _count(args, 2 if action == "reorder" else 1)
            index = _integer(args[0], 1, len(replacement)) - 1
            if action == "edit":
                if options is not None:
                    replacement[index]["options"] = options
                if "opacity" in opts:
                    replacement[index]["opacity"] = _fraction(opts["opacity"])
            elif opts:
                raise CommandError("Effect options apply to add or edit.")
            elif action in ("show", "hide"):
                replacement[index]["enabled"] = action == "show"
            elif action == "remove":
                replacement.pop(index)
            elif action == "reorder":
                target = _integer(args[1], 1, len(replacement)) - 1
                replacement.insert(target, replacement.pop(index))
            else:
                raise CommandError(HELP[command])
        validate_effects(replacement)
        return session._edit("Edit layer effects", lambda: setattr(doc.layer, "effects", replacement))
    if command == "channel":
        action = tokens[0].lower() if tokens else "list"
        args, opts = _args(tokens[1:], ("from", "mode"), ("json",))
        channels = doc.metadata.get("channels", {})
        if action == "list":
            _count(args, 0)
            if set(opts) - {"json"}:
                raise CommandError(HELP[command])
            return CommandResult(json.dumps(list(channels)) if opts.get("json") else
                                 "\n".join(channels) or "No saved channels.")
        _count(args, 1)
        name = _name(args[0])
        replacement = dict(channels)
        if action == "save":
            if set(opts) - {"from"}:
                raise CommandError(HELP[command])
            source = opts.get("from", "selection")
            if source == "selection":
                if doc.selection is None:
                    raise CommandError("Select an area before saving a selection channel.")
                image = doc.selection
            else:
                components = {"red": "R", "green": "G", "blue": "B", "alpha": "A"}
                if source not in components:
                    raise CommandError("Channel source is selection, red, green, blue or alpha.")
                image = doc.composite().getchannel(components[source])
            replacement[name] = encode_channel(image)
            validate_metadata({"channels": replacement}, doc.size)
            return session._edit("Save channel", lambda: doc.metadata.__setitem__("channels", replacement))
        if name not in channels:
            raise CommandError("Saved channel not found.")
        if action == "load":
            if set(opts) - {"mode"}:
                raise CommandError(HELP[command])
            image = decode_channel(channels[name], doc.size)
            return session._edit("Load channel selection", lambda: doc.set_selection(image, opts.get("mode", "replace")))
        if opts:
            raise CommandError(HELP[command])
        if action == "mask":
            doc.ensure_editable()
            image = decode_channel(channels[name], doc.size)
            return session._edit("Channel to layer mask", lambda: setattr(doc.layer, "mask", image))
        if action == "delete":
            replacement.pop(name)
            return session._edit("Delete channel", lambda: doc.metadata.__setitem__("channels", replacement))
        raise CommandError(HELP[command])
    action = tokens[0].lower() if tokens else "list"
    args, opts = _args(tokens[1:], ("width", "color", "opacity", "mode"), ("closed", "json"))
    paths = doc.metadata.get("paths", {})
    if action == "list":
        _count(args, 0)
        if set(opts) - {"json"}:
            raise CommandError(HELP[command])
        return CommandResult(json.dumps(paths, indent=2) if opts.get("json") else
                             "\n".join(f"{name}: {item['kind']}, {len(item['points'])} points" for name, item in paths.items()) or "No editable paths.")
    if not args:
        raise CommandError(HELP[command])
    name = _name(args[0])
    replacement = copy.deepcopy(paths)
    if action in ("new", "curve"):
        if set(opts) - {"closed"}:
            raise CommandError(HELP[command])
        replacement[name] = {"kind": "bezier" if action == "curve" else "polyline",
                             "points": [list(p) for p in _points(args[1:], 4 if action == "curve" else 2)],
                             "closed": bool(opts.get("closed"))}
    else:
        if name not in paths:
            raise CommandError("Editable path not found.")
        path = replacement[name]
        if action in ("stroke", "fill", "select"):
            _count(args, 1)
            allowed = {"mode"} if action == "select" else {"width", "color", "opacity"}
            if set(opts) - allowed:
                raise CommandError(HELP[command])
            mask = Image.new("L", doc.size)
            draw = ImageDraw.Draw(mask)
            points = path_points(path)
            if action in ("fill", "select"):
                if len(points) < 3:
                    raise CommandError("Path fill and selection require at least three sampled points.")
                draw.polygon(points, fill=255)
            else:
                draw.line(points, fill=255, width=_integer(opts.get("width", 1), 1, 128))
            if action == "select":
                return session._edit("Path to selection", lambda: doc.set_selection(mask, opts.get("mode", "replace")))
            color, opacity = _color(opts.get("color", session.setting("foreground"))), _fraction(opts.get("opacity", 1))
            return session._edit("Paint editable path", lambda: doc.paint_mask(mask, color, opacity))
        if opts:
            raise CommandError(HELP[command])
        if action == "point":
            _count(args, 3)
            index = _integer(args[1], 1, len(path["points"])) - 1
            path["points"][index] = list(_points([args[2]])[0])
        elif action == "move":
            _count(args, 3)
            dx, dy = _number(args[1], -32768, 32768), _number(args[2], -32768, 32768)
            path["points"] = [[x + dx, y + dy] for x, y in path["points"]]
        elif action == "close":
            _count(args, 1)
            path["closed"] = True
        elif action == "delete":
            _count(args, 1)
            replacement.pop(name)
        else:
            raise CommandError(HELP[command])
    validate_metadata({"paths": replacement}, doc.size)
    return session._edit("Edit path geometry", lambda: doc.metadata.__setitem__("paths", replacement))
