"""Bounded, shared typography layout for terminal commands and raster tools."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re

from PIL import Image, ImageColor, ImageDraw, ImageFont

MAX_TEXT_LENGTH = 16_384
MAX_TEXT_FILE_BYTES = 65_536
MAX_FONT_FILE_BYTES = 32 * 1024 * 1024
MAX_TEXT_PIXELS = 4_194_304
MAX_TEXT_EDGE = 8192
ANCHORS = {
    "top-left": (0, 0), "top-center": (.5, 0), "top-right": (1, 0),
    "center-left": (0, .5), "center": (.5, .5), "center-right": (1, .5),
    "bottom-left": (0, 1), "bottom-center": (.5, 1), "bottom-right": (1, 1),
}


@dataclass(frozen=True)
class TextStyle:
    size: int = 12
    font_path: str = ""
    wrap: int | None = None
    align: str = "left"
    spacing: float = 2
    letter_spacing: float = 0
    stroke: int = 0
    stroke_color: str = "#000000"
    shadow: tuple[int, int] | None = None
    shadow_color: str = "#00000080"
    rotation: float = 0
    opacity: float = 1
    anchor: str = "top-left"


@dataclass(frozen=True)
class RenderedText:
    image: Image.Image
    lines: tuple[str, ...]
    style: TextStyle
    font_name: tuple[str, str]

    def origin(self, point):
        horizontal, vertical = ANCHORS[self.style.anchor]
        return (round(point[0] - self.image.width * horizontal),
                round(point[1] - self.image.height * vertical))

    def measurement(self, point=(0, 0)):
        x, y = self.origin(point)
        bounds = self.image.getchannel("A").getbbox()
        return {
            "size": list(self.image.size), "width": self.image.width,
            "height": self.image.height, "origin": [x, y],
            "bounds": [bounds[0] + x, bounds[1] + y, bounds[2] + x, bounds[3] + y]
            if bounds is not None else None,
            "lines": list(self.lines), "line_count": len(self.lines),
            "font": {"family": self.font_name[0], "style": self.font_name[1],
                     "path": self.style.font_path or "Pillow default"},
            "anchor": self.style.anchor, "rotation": self.style.rotation,
        }


def read_text_file(path):
    """A bounded UTF-8 read; never interpret source text as commands."""
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_TEXT_FILE_BYTES + 1)
    if len(raw) > MAX_TEXT_FILE_BYTES:
        raise ValueError("Text files must contain at most 65,536 bytes.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("Text files must use UTF-8 encoding.") from error


def normalize_text(text, escapes=False):
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise ValueError("Text must contain at most 16,384 characters.")
    if escapes:
        # Deliberately support only useful text escapes, not Python evaluation.
        text = re.sub(r"\\([nrt\\])", lambda match: {
            "n": "\n", "r": "\r", "t": "\t", "\\": "\\"}[match[1]], text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError("Expanded text must contain at most 16,384 characters.")
    if any(ord(character) < 32 and character != "\n" for character in text):
        raise ValueError("Text contains unsupported control characters.")
    return text


def load_font(font_path="", size=12):
    if type(size) is not int or not 1 <= size <= 512:
        raise ValueError("Text size must be an integer from 1 to 512.")
    if not font_path:
        return ImageFont.load_default(size=size)
    path = Path(font_path).expanduser()
    if not path.is_file():
        raise ValueError(f"Font file does not exist: {path}")
    if path.stat().st_size > MAX_FONT_FILE_BYTES:
        raise ValueError("Font files must contain at most 32 MiB.")
    return ImageFont.truetype(str(path), size)


def font_directories():
    home = Path.home()
    result = [home / ".fonts", home / ".local/share/fonts"]
    if os.name == "nt":
        result += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                   Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Microsoft/Windows/Fonts"]
    else:
        result += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                   Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]
    return result


def discover_fonts(query="", limit=100):
    """Discover installed font files in known locations, with bounded traversal."""
    if not isinstance(query, str) or len(query) > 256 or not 1 <= limit <= 500:
        raise ValueError("Use a font query up to 256 characters and --limit 1..500.")
    found, seen = [], set()
    remaining = 16_384
    query = query.casefold()
    for directory in font_directories():
        if not directory.is_dir():
            continue
        for root, directories, files in os.walk(directory, followlinks=False):
            remaining -= 1
            if remaining < 0:
                return found
            directories[:] = sorted(name for name in directories if not name.startswith("."))[:128]
            for filename in sorted(files):
                remaining -= 1
                if remaining < 0:
                    return found
                path = Path(root) / filename
                if path.suffix.casefold() not in (".ttf", ".otf", ".ttc"):
                    continue
                canonical = str(path.resolve())
                if canonical in seen or query not in filename.casefold():
                    continue
                seen.add(canonical)
                found.append({"name": path.stem, "path": canonical})
                if len(found) >= limit:
                    return found
    return found


def _validate_style(style):
    if style.align not in ("left", "center", "right"):
        raise ValueError("Text alignment must be left, center or right.")
    if style.anchor not in ANCHORS:
        raise ValueError("Text anchor must be " + ", ".join(ANCHORS) + ".")
    if style.wrap is not None and (type(style.wrap) is not int or not 1 <= style.wrap <= 4096):
        raise ValueError("Text wrap width must be an integer from 1 to 4096 pixels.")
    for name, value, low, high in (("line spacing", style.spacing, 0, 512),
                                  ("letter spacing", style.letter_spacing, 0, 128),
                                  ("rotation", style.rotation, -360, 360),
                                  ("opacity", style.opacity, 0, 1)):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Text {name} must be a finite number from {low} to {high}.")
    if type(style.stroke) is not int or not 0 <= style.stroke <= 128:
        raise ValueError("Text stroke must be an integer from 0 to 128 pixels.")
    if style.shadow is not None and (len(style.shadow) != 2 or any(
            type(value) is not int or not -4096 <= value <= 4096 for value in style.shadow)):
        raise ValueError("Text shadow must use two integer offsets from -4096 to 4096.")
    ImageColor.getcolor(style.stroke_color, "RGBA")
    ImageColor.getcolor(style.shadow_color, "RGBA")


def _advance(font, text, letter_spacing):
    if not letter_spacing:
        return float(font.getlength(text))
    return sum(float(font.getlength(char)) for char in text) + max(0, len(text) - 1) * letter_spacing


def _wrap_lines(text, font, width, letter_spacing):
    if width is None:
        return text.split("\n")
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for token in re.findall(r"\S+|\s+", paragraph):
            if token.isspace():
                if current:
                    current += token
                continue
            if current and _advance(font, current + token, letter_spacing) > width:
                lines.append(current.rstrip())
                current = ""
            # Break long words rather than silently exceeding requested wrapping.
            for character in token:
                if current and _advance(font, current + character, letter_spacing) > width:
                    lines.append(current.rstrip())
                    current = ""
                current += character
        lines.append(current.rstrip())
    return lines


def _glyphs(font, line, letter_spacing):
    if not letter_spacing:
        yield 0, line
        return
    x = 0
    for character in line:
        yield x, character
        x += float(font.getlength(character)) + letter_spacing


def _safe_size(width, height):
    width, height = max(1, math.ceil(width)), max(1, math.ceil(height))
    if width > MAX_TEXT_EDGE or height > MAX_TEXT_EDGE or width * height > MAX_TEXT_PIXELS:
        raise ValueError("Text layout exceeds the 4,194,304-pixel budget. Use --wrap, smaller --size or less text.")
    return width, height


def render_text(text, color, style=None):
    """Render a transparent RGBA text patch; measurement uses this same image."""
    style = style or TextStyle()
    _validate_style(style)
    text = normalize_text(text)
    foreground = ImageColor.getcolor(color, "RGBA")
    font = load_font(style.font_path, style.size)
    lines = _wrap_lines(text, font, style.wrap, style.letter_spacing)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + style.spacing
    advances = [_advance(font, line, style.letter_spacing) for line in lines]
    body_width = max(style.wrap or 0, *advances)
    body_height = ascent + descent + (len(lines) - 1) * line_height
    # Include logical line boxes, italic bearings, outline and negative shadow.
    min_x, min_y, max_x, max_y = 0, 0, body_width, body_height
    placements = []
    for index, (line, advance) in enumerate(zip(lines, advances)):
        x = (body_width - advance) * {"left": 0, "center": .5, "right": 1}[style.align]
        baseline = ascent + index * line_height
        for offset, glyph in _glyphs(font, line, style.letter_spacing):
            box = font.getbbox(glyph, anchor="ls", stroke_width=style.stroke)
            placements.append((x + offset, baseline, glyph))
            if glyph.strip():
                min_x, min_y = min(min_x, x + offset + box[0]), min(min_y, baseline + box[1])
                max_x, max_y = max(max_x, x + offset + box[2]), max(max_y, baseline + box[3])
    if style.shadow is not None:
        dx, dy = style.shadow
        min_x, min_y, max_x, max_y = (min(min_x, min_x + dx), min(min_y, min_y + dy),
                                    max(max_x, max_x + dx), max(max_y, max_y + dy))
    left, top = math.floor(min_x), math.floor(min_y)
    size = _safe_size(math.ceil(max_x) - left, math.ceil(max_y) - top)
    # Check expanded rotation size before Pillow allocates its destination.
    if style.rotation % 360:
        angle = math.radians(style.rotation)
        _safe_size(abs(size[0] * math.cos(angle)) + abs(size[1] * math.sin(angle)) + 2,
                   abs(size[0] * math.sin(angle)) + abs(size[1] * math.cos(angle)) + 2)
    ink = Image.new("RGBA", size)
    draw = ImageDraw.Draw(ink)
    outline = ImageColor.getcolor(style.stroke_color, "RGBA")
    for x, baseline, glyph in placements:
        draw.text((x - left, baseline - top), glyph, font=font, anchor="ls", fill=foreground,
                  stroke_width=style.stroke, stroke_fill=outline)
    if style.shadow is not None:
        dx, dy = style.shadow
        # Derive the silhouette before compositing, so semitransparent foreground
        # and outline blend with the shadow correctly rather than overwriting it.
        silhouette = Image.new("L", size)
        shadow_draw = ImageDraw.Draw(silhouette)
        for x, baseline, glyph in placements:
            shadow_draw.text((x - left + dx, baseline - top + dy), glyph, font=font,
                             anchor="ls", fill=255, stroke_width=style.stroke, stroke_fill=255)
        shadow_color = ImageColor.getcolor(style.shadow_color, "RGBA")
        shadow = Image.new("RGBA", size, shadow_color)
        shadow.putalpha(silhouette.point(lambda alpha: round(alpha * shadow_color[3] / 255)))
        ink = Image.alpha_composite(shadow, ink)
    if style.rotation % 360:
        ink = ink.rotate(style.rotation, resample=Image.Resampling.BICUBIC, expand=True)
    if style.opacity != 1:
        ink.putalpha(ink.getchannel("A").point(lambda alpha: round(alpha * style.opacity)))
    return RenderedText(ink, tuple(lines), style, font.getname())
