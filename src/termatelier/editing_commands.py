"""Transactional pixel, path, selection and color editing command adapters."""
from __future__ import annotations

from collections import Counter
import json
import math

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageOps


HELP = {
    "pixel": "pixel X Y [COLOR] [--active | --merged] [--json] — inspect RGBA (merged by default), or replace an active-layer pixel inside the selection",
    "polygon": "polygon X,Y X,Y X,Y ... [--filled] [--color COLOR] [--width 1..128] [--opacity N] — closed polygon",
    "polyline": "polyline X,Y X,Y ... [--color COLOR] [--width 1..128] [--opacity N] — connected open path",
    "bezier": "bezier START_X,Y CONTROL1_X,Y CONTROL2_X,Y END_X,Y [--steps 8..2048] [--color COLOR] [--width 1..128] [--opacity N] — cubic curve",
    "align": "align left|center-x|right|top|center-y|bottom|center [--selection] — move active-layer alpha bounds within the canvas or selection bounds",
    "selection": "selection bounds [--json] | grow 0..64 | shrink 0..64 | border 0..64 | threshold 0..255 — inspect or edit the current selection; border is an inner pixel border",
    "adjust": "adjust gamma 0.1..10 | levels BLACK WHITE [GAMMA] | temperature -1..1 | alpha 0..4 — selected-layer color adjustments; alpha multiplies pixel opacity, temperature adds a warm/cool tint",
    "colors": "colors [--limit 1..64] [--active] [--json] — dominant visible RGB colors from at most 256×256 merged samples",
}

_MAX_COORDINATE = 32768
_MAX_VERTICES = 2048


def _morphology(mask, radius, grow):
    if radius == 0:
        return mask.copy()
    # Outside the canvas is unselected; a full-canvas selection must also
    # shrink away from its edges, rather than repeating its edge pixels.
    result = ImageOps.expand(mask, border=radius, fill=0)
    combine = ImageChops.lighter if grow else ImageChops.darker
    neutral = 0 if grow else 255
    window = 2 * radius + 1
    # Square extrema are separable. Each shifted union grows a backward
    # interval by up to its current span: 1, 2, 4, ... then the exact remainder.
    # This visits each pixel O(log radius) times, including feathered coverage,
    # instead of scanning a large two-dimensional rank-filter neighborhood.
    for dx, dy in ((1, 0), (0, 1)):
        span = 1
        while span < window:
            offset = min(span, window - span)
            shifted = Image.new("L", result.size, neutral)
            shifted.paste(result, (offset * dx, offset * dy))
            result = combine(result, shifted)
            span += offset
    # Backward intervals end radius pixels after each desired center; the
    # original image already begins radius pixels into its zero padding.
    start = 2 * radius
    return result.crop((start, start, start + mask.width, start + mask.height))


def _adjusted_pixels(source, tables=None, alpha_table=None):
    if alpha_table is not None:
        adjusted = source.copy()
        adjusted.putalpha(source.getchannel("A").point(alpha_table))
        return adjusted
    adjusted = source.convert("RGB").point(tables).convert("RGBA")
    adjusted.putalpha(source.getchannel("A"))
    return adjusted


def execute_extra(session, command, tokens):
    """Return a result for a recognized command, or ``None`` for another adapter.

    Central imports stay inside dispatch so HELP is safe to import while the
    main command module is initializing. No commands evaluate code or shells.
    """
    if command not in HELP:
        return None
    from .commands import CommandError, CommandResult, _args, _color, _count, _integer, _number

    doc = session.document

    if command == "pixel":
        args, opts = _args(tokens, flags=("active", "merged", "json"))
        _count(args, 2, 3)
        point = (_integer(args[0], 0, doc.width - 1), _integer(args[1], 0, doc.height - 1))
        if opts.get("active") and opts.get("merged"):
            raise CommandError("Choose either --active or --merged sampling.")
        changed = False
        source_name = "active" if opts.get("active") else "merged"
        if len(args) == 3:
            if opts.get("active") or opts.get("merged"):
                raise CommandError("--active and --merged are for inspecting pixels, not replacing them.")
            target = ImageColor.getcolor(_color(args[2]), "RGBA")
            doc.ensure_editable()
            coverage = doc.selection.getpixel(point) if doc.selection is not None else 255
            original = doc.layer.image.getpixel(point)
            replacement = tuple((old * (255 - coverage) + new * coverage + 127) // 255
                                for old, new in zip(original, target))
            if original != replacement:
                session._edit("Replace pixel", lambda: doc.layer.image.putpixel(point, replacement))
                changed = True
            source_name = "active"
        pixel = (doc.layer.image.getpixel(point) if source_name == "active" else
                 doc.composite((point[0], point[1], point[0] + 1, point[1] + 1)).getpixel((0, 0)))
        color = "#{:02x}{:02x}{:02x}{:02x}".format(*pixel)
        data = {"x": point[0], "y": point[1], "rgba": list(pixel), "color": color, "source": source_name}
        text = (json.dumps(data) if opts.get("json") else
                f"Pixel {point[0]},{point[1]}: {color} · RGBA {pixel} · {source_name}")
        return CommandResult(text, changed)

    if command in ("polygon", "polyline", "bezier"):
        options = ("color", "width", "opacity") + (("steps",) if command == "bezier" else ())
        flags = ("filled",) if command == "polygon" else ()
        args, opts = _args(tokens, options, flags)
        minimum = 3 if command == "polygon" else 2
        _count(args, 4 if command == "bezier" else minimum,
               4 if command == "bezier" else _MAX_VERTICES)
        points = []
        for value in args:
            pair = value.split(",")
            _count(pair, 2)
            points.append(tuple(_integer(item, -_MAX_COORDINATE, _MAX_COORDINATE) for item in pair))
        color, opacity = session._paint_options(opts)
        width = _integer(opts.get("width", session.setting("brush_size")), 1, 128)
        if command == "bezier":
            length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            steps = _integer(opts.get("steps", min(2048, max(8, math.ceil(length / 2)))), 8, 2048)
            p0, p1, p2, p3 = points
            points = []
            for index in range(steps + 1):
                t, u = index / steps, 1 - index / steps
                points.append(tuple(round(u ** 3 * p0[axis] + 3 * u ** 2 * t * p1[axis]
                                          + 3 * u * t ** 2 * p2[axis] + t ** 3 * p3[axis])
                                    for axis in (0, 1)))
        if command == "polygon":
            mask = Image.new("L", doc.size)
            ImageDraw.Draw(mask).polygon(points, fill=255 if opts.get("filled") else None,
                                         outline=255, width=width)
        else:
            mask = doc.stroke_mask(points, width, 1)
        return session._edit(command.capitalize(), lambda: doc.paint_mask(mask, color, opacity))

    if command == "align":
        args, opts = _args(tokens, flags=("selection",))
        _count(args, 1)
        mode = args[0].lower()
        if mode not in ("left", "center-x", "right", "top", "center-y", "bottom", "center"):
            raise CommandError(HELP["align"])
        bounds = doc.layer.image.getchannel("A").getbbox()
        if bounds is None:
            raise CommandError("The active layer has no visible pixels to align.")
        target = (0, 0, doc.width, doc.height)
        if opts.get("selection"):
            target = doc.selection.getbbox() if doc.selection is not None else None
            if target is None:
                raise CommandError("Create a nonempty selection before aligning to its bounds.")
        dx = dy = 0
        if mode == "left": dx = target[0] - bounds[0]
        elif mode == "right": dx = target[2] - bounds[2]
        elif mode in ("center-x", "center"): dx = (target[0] + target[2] - bounds[0] - bounds[2]) // 2
        if mode == "top": dy = target[1] - bounds[1]
        elif mode == "bottom": dy = target[3] - bounds[3]
        elif mode in ("center-y", "center"): dy = (target[1] + target[3] - bounds[1] - bounds[3]) // 2
        doc.ensure_editable()
        if dx == 0 and dy == 0:
            return CommandResult("Layer is already aligned.")
        return session._edit(f"Align {mode}", lambda: doc.move(dx, dy))

    if command == "selection":
        args, opts = _args(tokens, flags=("json",))
        _count(args, 1, 2)
        operation = args[0].lower()
        if operation == "bounds":
            _count(args, 1)
            bounds = doc.selection.getbbox() if doc.selection is not None else None
            data = {"active": doc.selection is not None, "bounds": list(bounds) if bounds else None,
                    "width": bounds[2] - bounds[0] if bounds else 0,
                    "height": bounds[3] - bounds[1] if bounds else 0}
            text = (json.dumps(data) if opts.get("json") else
                    f"Selection bounds: {bounds} · {data['width']}×{data['height']} pixels" if bounds else
                    "Selection is empty." if data["active"] else "No selection.")
            return CommandResult(text)
        _count(args, 2)
        if operation not in ("grow", "shrink", "border", "threshold") or opts:
            raise CommandError(HELP["selection"])
        if doc.selection is None:
            raise CommandError("Create a selection before modifying its mask.")
        amount = _integer(args[1], 0, 255 if operation == "threshold" else 64)
        if operation == "threshold":
            result = doc.selection.point([255 if value >= amount else 0 for value in range(256)])
        elif operation == "border":
            result = ImageChops.subtract(doc.selection, _morphology(doc.selection, amount, False))
        else:
            result = _morphology(doc.selection, amount, operation == "grow")
        return session._edit(f"Selection {operation}", lambda: doc.set_selection(result))

    if command == "adjust":
        _count(tokens, 2, 4)
        operation = tokens[0].lower()
        tables = alpha_table = None
        if operation == "gamma":
            _count(tokens, 2)
            gamma = _number(tokens[1], .1, 10)
            tables = [round(255 * (value / 255) ** (1 / gamma)) for value in range(256)] * 3
        elif operation == "levels":
            _count(tokens, 3, 4)
            black, white = _integer(tokens[1], 0, 254), _integer(tokens[2], 1, 255)
            if white <= black:
                raise CommandError("The white level must be greater than the black level.")
            gamma = _number(tokens[3], .1, 10) if len(tokens) == 4 else 1
            tables = [round(255 * max(0, min(1, (value - black) / (white - black))) ** (1 / gamma))
                      for value in range(256)] * 3
        elif operation == "temperature":
            _count(tokens, 2)
            amount = _number(tokens[1], -1, 1) * 64
            tables = [max(0, min(255, round(value + offset)))
                      for offset in (amount, 0, -amount) for value in range(256)]
        elif operation == "alpha":
            _count(tokens, 2)
            value = tokens[1]
            amount = _number(value[:-1], 0, 400) / 100 if value.endswith("%") else _number(value, 0, 4)
            alpha_table = [min(255, round(value * amount)) for value in range(256)]
        else:
            raise CommandError(HELP["adjust"])
        doc.ensure_editable()
        adjusted = _adjusted_pixels(doc.layer.image, tables, alpha_table)
        coverage = doc.selection
        if alpha_table is None:
            visible = doc.layer.image.getchannel("A").point([0] + [255] * 255)
            coverage = ImageChops.multiply(visible, coverage) if coverage is not None else visible
        result = Image.composite(adjusted, doc.layer.image, coverage) if coverage is not None else adjusted
        return session._edit(f"Adjust {operation}", lambda: setattr(doc.layer, "image", result))

    if command == "colors":
        args, opts = _args(tokens, ("limit",), ("json", "active"))
        _count(args, 0)
        limit = _integer(opts.get("limit", 8), 1, 64)
        scale = min(1, 256 / doc.width, 256 / doc.height)
        size = (max(1, math.floor(doc.width * scale)), max(1, math.floor(doc.height * scale)))
        affine = (doc.width / size[0], 0, 0, 0, doc.height / size[1], 0)
        image = (doc.layer.image.transform(size, Image.Transform.AFFINE, affine, Image.Resampling.NEAREST)
                 if opts.get("active") else doc.composite_view(size, affine))
        raw = image.tobytes()
        counts = Counter((raw[index], raw[index + 1], raw[index + 2])
                         for index in range(0, len(raw), 4) if raw[index + 3])
        colors = [{"color": "#{:02x}{:02x}{:02x}".format(*color), "samples": count}
                  for color, count in counts.most_common(limit)]
        data = {"sample_size": list(size), "visible_samples": sum(counts.values()), "colors": colors}
        return CommandResult(json.dumps(data) if opts.get("json") else
                             "Sampled colors: " + (", ".join(item["color"] for item in colors) or "none visible"))

    return None
