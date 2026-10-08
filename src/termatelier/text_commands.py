"""Advanced text and font command adapters, using the shared document history."""
from __future__ import annotations

import json

from .text_tools import (ANCHORS, TextStyle, discover_fonts, load_font,
                         normalize_text, read_text_file, render_text)

HELP = {
    "text": 'text X Y "WORDS" | text X Y --file PATH | text measure "WORDS" [--json] — '
            '--size 1..512 --font PATH --color COLOR --opacity N --escapes '
            '--wrap PIXELS --align left|center|right --spacing PIXELS --letter-spacing PIXELS '
            '--stroke PIXELS --stroke-color COLOR --shadow DX,DY --shadow-color COLOR '
            '--rotate DEGREES --anchor top-left|top-center|top-right|center-left|center|center-right|bottom-left|bottom-center|bottom-right '
            '--layer NAME; example: text 10 10 "A title\\nSecond line" --escapes --wrap 200 --align center --stroke 1 --layer Title',
    "fonts": 'fonts [list] [QUERY] [--limit 1..500] [--json] | fonts info PATH [--size N] [--json] — '
             'installed font files; use a returned path with text --font PATH',
}

OPTIONS = ("color", "size", "font", "opacity", "file", "wrap", "align", "spacing",
           "letter-spacing", "stroke", "stroke-color", "shadow", "shadow-color", "rotate", "anchor", "layer")


def execute_fonts(session, tokens):
    from .commands import CommandError, CommandResult, _args, _count, _integer
    action = tokens[0].lower() if tokens and tokens[0].lower() in ("list", "info") else "list"
    remaining = tokens[1:] if tokens and tokens[0].lower() in ("list", "info") else tokens
    args, opts = _args(remaining, ("limit", "size"), ("json",))
    if action == "info":
        _count(args, 1)
        if "limit" in opts:
            raise CommandError("--limit applies to fonts list.")
        path = session._path(args[0])
        size = _integer(opts.get("size", 12), 1, 512)
        font = load_font(str(path), size)
        family, name = font.getname()
        ascent, descent = font.getmetrics()
        data = {"path": str(path), "family": family, "style": name, "size": size,
                "ascent": ascent, "descent": descent}
        return CommandResult(json.dumps(data, indent=2) if opts.get("json")
                             else f"{family} · {name} · {size}px\n{path}\nAscent {ascent}px; descent {descent}px.")
    _count(args, 0, 1)
    if "size" in opts:
        raise CommandError("--size applies to fonts info.")
    fonts = discover_fonts(args[0] if args else "", _integer(opts.get("limit", 100), 1, 500))
    if opts.get("json"):
        return CommandResult(json.dumps(fonts, indent=2))
    rows = [f"{font['name']} — {font['path']}" for font in fonts]
    return CommandResult("\n".join(rows) + ("\n" if rows else "No matching installed fonts.\n")
                         + 'Default font always available. Draw: text 5 5 "Hello" --font "PATH"')


def execute_text(session, tokens):
    from .commands import (CommandError, CommandResult, _args, _color, _count,
                           _fraction, _integer, _number)
    if tokens and tokens[0].lower() == "fonts":
        return execute_fonts(session, tokens[1:])
    measure = bool(tokens and tokens[0].lower() == "measure")
    args, opts = _args(tokens[1:] if measure else tokens, OPTIONS, ("escapes", "json"))
    if "json" in opts and not measure:
        raise CommandError("--json applies to text measure.")
    if measure and "layer" in opts:
        raise CommandError("text measure does not create a layer.")
    if "file" in opts:
        _count(args, 0 if measure else 2)
        words = read_text_file(session._path(opts["file"]))
    else:
        _count(args, 1 if measure else 3)
        words = args[0] if measure else args[2]
    words = normalize_text(words, bool(opts.get("escapes")))
    point = (0, 0) if measure else tuple(_integer(value, -1_000_000, 1_000_000) for value in args[:2])
    shadow = None
    if "shadow" in opts:
        coordinates = opts["shadow"].split(",")
        _count(coordinates, 2)
        shadow = tuple(_integer(value, -4096, 4096) for value in coordinates)
    anchor = opts.get("anchor", "top-left").lower()
    if anchor not in ANCHORS:
        raise CommandError("Use --anchor " + "|".join(ANCHORS) + ".")
    style = TextStyle(size=_integer(opts.get("size", 12), 1, 512),
                      font_path=str(session._path(opts["font"])) if opts.get("font") else "",
                      wrap=_integer(opts["wrap"], 1, 4096) if "wrap" in opts else None,
                      align=opts.get("align", "left").lower(),
                      spacing=_number(opts.get("spacing", 2), 0, 512),
                      letter_spacing=_number(opts.get("letter-spacing", 0), 0, 128),
                      stroke=_integer(opts.get("stroke", 0), 0, 128),
                      stroke_color=_color(opts.get("stroke-color", "black")),
                      shadow=shadow, shadow_color=_color(opts.get("shadow-color", "#00000080")),
                      rotation=_number(opts.get("rotate", 0), -360, 360),
                      opacity=_fraction(opts.get("opacity", session.setting("opacity"))), anchor=anchor)
    color = _color(opts.get("color", session.setting("foreground")))
    # All layout/font/file validation happens before opening an edit checkpoint.
    rendered = render_text(words, color, style)
    measurement = rendered.measurement(point)
    if measure:
        return CommandResult(json.dumps(measurement, indent=2) if opts.get("json") else
                             f"Text: {rendered.image.width}×{rendered.image.height}px; {len(rendered.lines)} line(s); "
                             f"visible bounds {measurement['bounds']}; {rendered.font_name[0]}.\n"
                             'Draw: text X Y "WORDS" with the same options.')
    layer = opts.get("layer")
    if layer is not None and (not layer.strip() or len(layer) > 256):
        raise CommandError("Text layer names must contain 1..256 characters.")
    with session.document.edit("Text" if layer is None else f"Text: {layer}"):
        session.document.text(point, words, color, style.size, style.font_path, style.opacity,
                              wrap=style.wrap, align=style.align, spacing=style.spacing,
                              letter_spacing=style.letter_spacing, stroke=style.stroke,
                              stroke_color=style.stroke_color, shadow=style.shadow,
                              shadow_color=style.shadow_color, rotation=style.rotation,
                              anchor=style.anchor, new_layer=layer, _rendered=rendered)
    return CommandResult(f"Text: {rendered.image.width}×{rendered.image.height}px; {len(rendered.lines)} line(s)"
                         + (f" on layer {layer}." if layer is not None else "."), changed=True)
