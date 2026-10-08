"""Bounded, atomic interoperability with OpenRaster's flat raster baseline.

Specification: https://www.openraster.org/baseline/file-layout-spec.html and
https://www.openraster.org/baseline/layer-stack-spec.html (version 0.0.6).
Layer masks and native effects are baked into individual PNGs; their separate
opacity and supported blend properties remain editable. Native .tart retains
SPARKER-specific editable masks, effects, text recipes, paths and selections.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageChops

from .model import Document, Layer, MAX_LAYERS, valid_size
from .storage import atomic_write, png_bytes, resolve_export_path

MAX_ORA_BYTES = 128 * 1024 * 1024
MAX_STACK_BYTES = 256 * 1024
MAX_ENTRIES = 256
MIMETYPE = b"image/openraster"
# svg:plus is Porter-Duff Lighter, not SPARKER's additive RGB/source-over
# recipe. Mapping it to 'add' would silently change partially transparent art.
BLEND_TO_ORA = {"normal": "svg:src-over", "multiply": "svg:multiply", "screen": "svg:screen",
                "overlay": "svg:overlay", "darken": "svg:darken", "lighten": "svg:lighten",
                "difference": "svg:difference"}
ORA_TO_BLEND = {value: key for key, value in BLEND_TO_ORA.items()}


def _integer(value, name, low, high):
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?\d{1,10}", value):
        raise ValueError(f"OpenRaster {name} must be an integer.")
    result = int(value)
    if not low <= result <= high:
        raise ValueError(f"OpenRaster {name} is outside {low}..{high}.")
    return result


def _float(value, name, low, high):
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid OpenRaster {name}.") from error
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"OpenRaster {name} is outside {low}..{high}.")
    return result


def _boolean(value, name):
    if value not in ("true", "false", "1", "0"):
        raise ValueError(f"OpenRaster {name} must be true or false.")
    return value in ("true", "1")


def _xml_text(value, name):
    if (not isinstance(value, str) or len(value) > 256 or
            any((ord(c) < 32 and c not in "\t\n\r") or 0xd800 <= ord(c) <= 0xdfff or
                ord(c) in (0xfffe, 0xffff) for c in value)):
        raise ValueError(f"OpenRaster {name} must be XML text with at most 256 characters.")
    return value


def _safe_path(name, *, directory=False):
    if not isinstance(name, str) or not name or len(name) > 512 or "\x00" in name or "\\" in name or ":" in name:
        raise ValueError("Invalid OpenRaster archive reference.")
    checked = name[:-1] if directory and name.endswith("/") else name
    if not checked or any(part in ("", ".", "..") for part in checked.split("/")):
        raise ValueError("OpenRaster references must be relative paths inside the archive.")
    return name


def _validate_document(doc):
    valid_size(doc.width, doc.height)
    if not 1 <= len(doc.layers) <= MAX_LAYERS:
        raise ValueError("OpenRaster requires 1..64 raster layers.")
    if doc.width * doc.height * 4 * len(doc.layers) > MAX_ORA_BYTES:
        raise ValueError("OpenRaster decoded layers exceed the 128 MiB pixel budget.")
    if type(doc.active) is not int or not 0 <= doc.active < len(doc.layers):
        raise ValueError("Invalid OpenRaster active layer.")
    for layer in doc.layers:
        if layer.image.mode != "RGBA" or layer.image.size != doc.size:
            raise ValueError("OpenRaster layers must be canvas-sized RGBA images.")
        if layer.mask is not None and (layer.mask.mode != "L" or layer.mask.size != doc.size):
            raise ValueError("Invalid OpenRaster export layer mask.")
        _xml_text(layer.name, "layer name")
        if layer.blend not in BLEND_TO_ORA:
            raise ValueError(f"OpenRaster does not preserve the {layer.blend!r} blend mode. "
                             "Use a .tart project or flatten a copy before exporting.")
        _float(layer.opacity, "layer opacity", 0, 1)
        if type(layer.visible) is not bool or type(layer.locked) is not bool:
            raise ValueError("Invalid OpenRaster layer flags.")


def save_ora(doc, path, config=None):
    """Export an ORA without resampling or altering the editable document."""
    path = resolve_export_path(path, config)
    if path.suffix.lower() != ".ora":
        raise ValueError("OpenRaster files use the .ora extension.")
    _validate_document(doc)
    attrs = {"version": "0.0.6", "w": str(doc.width), "h": str(doc.height),
             "name": _xml_text(doc.metadata.get("title", path.stem), "image title")}
    for axis in ("x", "y"):
        key = f"resolution_{axis}"
        if key in doc.metadata:
            attrs[f"{axis}res"] = str(_float(doc.metadata[key], f"{axis} resolution", .01, 9600))
    image = ET.Element("image", attrs)
    stack = ET.SubElement(image, "stack")
    # SPARKER is bottom-first; OpenRaster is top-first.
    for index in reversed(range(len(doc.layers))):
        layer = doc.layers[index]
        ET.SubElement(stack, "layer", {"name": layer.name, "src": f"data/layer{index:03d}.png",
            "x": "0", "y": "0", "opacity": repr(float(layer.opacity)),
            "visibility": "visible" if layer.visible else "hidden",
            "composite-op": BLEND_TO_ORA[layer.blend],
            "selected": "true" if index == doc.active else "false",
            "edit-locked": "true" if layer.locked else "false"})
    xml = ET.tostring(image, encoding="utf-8", xml_declaration=True)
    if len(xml) > MAX_STACK_BYTES:
        raise ValueError("OpenRaster layer stack is too large.")

    def write(temporary):
        total = 0
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            def member(name, data):
                nonlocal total
                total += len(data)
                if total > MAX_ORA_BYTES:
                    raise ValueError("OpenRaster archive exceeds the 128 MiB data budget.")
                archive.writestr(name, data, compress_type=zipfile.ZIP_STORED)
            member("mimetype", MIMETYPE)
            member("stack.xml", xml)
            for index, layer in enumerate(doc.layers):
                pixels = layer.pixels_with_effects().copy()
                if pixels.mode != "RGBA" or pixels.size != doc.size:
                    raise ValueError("Layer effects returned invalid OpenRaster raster pixels.")
                if layer.mask is not None:
                    pixels.putalpha(ImageChops.multiply(pixels.getchannel("A"), layer.mask))
                # Opacity stays in stack.xml; applying it here would double it.
                member(f"data/layer{index:03d}.png", png_bytes(pixels))
            merged = doc.composite()
            if merged.mode != "RGBA" or merged.size != doc.size:
                raise ValueError("Invalid OpenRaster merged image.")
            member("mergedimage.png", png_bytes(merged))
            thumbnail = merged.copy()
            thumbnail.thumbnail((256, 256), Image.Resampling.BOX)
            member("Thumbnails/thumbnail.png", png_bytes(thumbnail))
        if Path(temporary).stat().st_size > MAX_ORA_BYTES:
            raise ValueError("OpenRaster ZIP exceeds 128 MiB.")

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, write)
    return path


def _archive_members(archive):
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("OpenRaster archive has an invalid member count.")
    names = set()
    total = 0
    for entry in entries:
        _safe_path(entry.filename, directory=entry.is_dir())
        if entry.filename in names:
            raise ValueError("OpenRaster archive contains duplicate member names.")
        names.add(entry.filename)
        if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("Unsupported OpenRaster ZIP compression.")
        if entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16):
            raise ValueError("Encrypted files and symlinks are not supported in OpenRaster archives.")
        total += entry.file_size
        if entry.file_size > MAX_ORA_BYTES or total > MAX_ORA_BYTES:
            raise ValueError("OpenRaster archive exceeds the 128 MiB expanded data budget.")
    first = entries[0]
    if first.filename != "mimetype" or first.header_offset != 0 or first.compress_type != zipfile.ZIP_STORED:
        raise ValueError("The first OpenRaster member must be an uncompressed mimetype.")
    if first.file_size != len(MIMETYPE) or archive.read("mimetype") != MIMETYPE:
        raise ValueError("Invalid OpenRaster mimetype.")
    for name in ("stack.xml", "mergedimage.png", "Thumbnails/thumbnail.png"):
        if name not in names:
            raise ValueError(f"OpenRaster archive is missing {name}.")
    return {entry.filename: entry for entry in entries}


def _read_png(archive, members, name, *, pixel_budget=MAX_ORA_BYTES):
    _safe_path(name)
    entry = members.get(name)
    if entry is None or entry.is_dir() or not name.lower().endswith(".png"):
        raise ValueError(f"Missing or unsupported OpenRaster raster reference: {name}.")
    try:
        encoded = archive.read(name)
        if encoded[:8] == b"\x89PNG\r\n\x1a\n" and len(encoded) > 24 and encoded[24] == 16:
            raise ValueError("16-bit OpenRaster PNGs are unsupported in this 8-bit raster editor.")
        with Image.open(io.BytesIO(encoded)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ValueError("OpenRaster raster members must be single-frame PNGs.")
            valid_size(*image.size)
            if image.width * image.height * 4 > pixel_budget:
                raise ValueError("OpenRaster raster sources exceed the 128 MiB decoded pixel budget.")
            image.load()
            return image.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"Invalid OpenRaster PNG {name}: {error}") from error


def load_ora(path):
    """Load flat PNG layers; reject unsupported groups instead of flattening them."""
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > MAX_ORA_BYTES:
        raise ValueError("OpenRaster ZIP exceeds the 128 MiB file budget.")
    try:
        with zipfile.ZipFile(path) as archive:
            members = _archive_members(archive)
            if members["stack.xml"].file_size > MAX_STACK_BYTES:
                raise ValueError("OpenRaster stack.xml exceeds 256 KiB.")
            raw_xml = archive.read("stack.xml")
            try:
                xml = raw_xml.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise ValueError("OpenRaster stack.xml must be UTF-8.") from error
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
                raise ValueError("OpenRaster DTDs and entity declarations are not supported.")
            declaration = re.match(r"\s*<\?xml\b([^?]+)\?>", xml)
            encoding = re.search(r"\bencoding\s*=\s*['\"]([^'\"]+)['\"]", declaration[1]) if declaration else None
            if encoding and encoding[1].lower().replace("-", "") != "utf8":
                raise ValueError("OpenRaster stack.xml must declare UTF-8 encoding.")
            try:
                root = ET.fromstring(xml)
            except ET.ParseError as error:
                raise ValueError(f"Invalid OpenRaster layer XML: {error}") from error
            if root.tag != "image" or len(root) != 1 or root[0].tag != "stack":
                raise ValueError("OpenRaster requires one image and one root layer stack.")
            width = _integer(root.get("w"), "width", 1, 4096)
            height = _integer(root.get("h"), "height", 1, 4096)
            valid_size(width, height)
            stack = root[0]
            if any(child.tag == "stack" for child in stack):
                raise ValueError("Nested OpenRaster layer groups are not supported; flatten groups before import.")
            if any(child.tag != "layer" or len(child) for child in stack):
                raise ValueError("This OpenRaster file contains unsupported text, filters or non-raster layers.")
            if not 1 <= len(stack) <= MAX_LAYERS or width * height * 4 * len(stack) > MAX_ORA_BYTES:
                raise ValueError("OpenRaster layers exceed the 64 layer / 128 MiB pixel budget.")
            # A root stack always has implicit opacity one and source-over.
            if stack.get("opacity", "1") != "1" and _float(stack.get("opacity"), "root opacity", 0, 1) != 1:
                raise ValueError("OpenRaster root-stack opacity is not supported.")
            if stack.get("composite-op", "svg:src-over") != "svg:src-over" or stack.get("visibility", "visible") != "visible":
                raise ValueError("OpenRaster root-stack rendering properties are invalid.")
            entries = []
            for index, element in enumerate(stack):
                name = element.get("name", f"Layer {len(stack) - index}")
                _xml_text(name, "layer name")
                src = _safe_path(element.get("src"))
                blend = element.get("composite-op", "svg:src-over")
                if blend not in ORA_TO_BLEND:
                    raise ValueError(f"Unsupported OpenRaster blend mode: {blend}.")
                visibility = element.get("visibility", "visible")
                if visibility not in ("visible", "hidden"):
                    raise ValueError("Invalid OpenRaster layer visibility.")
                entries.append({"name": name, "src": src,
                    "opacity": _float(element.get("opacity", "1"), "opacity", 0, 1),
                    "visible": visibility == "visible", "blend": ORA_TO_BLEND[blend],
                    "locked": _boolean(element.get("edit-locked", "false"), "edit-locked"),
                    "selected": _boolean(element.get("selected", "false"), "selected"),
                    "x": _integer(element.get("x", "0"), "x offset", -32768, 32768),
                    "y": _integer(element.get("y", "0"), "y offset", -32768, 32768)})
            # Required previews are validated but never replace editable layers.
            merged = _read_png(archive, members, "mergedimage.png")
            if merged.size != (width, height):
                raise ValueError("OpenRaster merged image dimensions differ from the canvas.")
            thumbnail = _read_png(archive, members, "Thumbnails/thumbnail.png")
            if max(thumbnail.size) > 256:
                raise ValueError("OpenRaster thumbnails must be at most 256×256 pixels.")
            del merged, thumbnail
            doc = Document(width, height)
            doc.layers = []
            source_bytes = 0
            active = None
            for entry in reversed(entries):
                pixels = _read_png(archive, members, entry["src"], pixel_budget=MAX_ORA_BYTES - source_bytes)
                source_bytes += pixels.width * pixels.height * 4
                if source_bytes > MAX_ORA_BYTES:
                    raise ValueError("OpenRaster raster sources exceed the 128 MiB decoded pixel budget.")
                canvas = Image.new("RGBA", doc.size)
                canvas.paste(pixels, (entry["x"], entry["y"]))
                doc.layers.append(Layer(entry["name"], canvas, entry["visible"], entry["locked"],
                                        entry["opacity"], entry["blend"]))
                if entry["selected"] and active is None:
                    active = len(doc.layers) - 1
            doc.active = len(doc.layers) - 1 if active is None else active
            doc.metadata["title"] = _xml_text(root.get("name", path.stem), "image title")
            for axis in ("x", "y"):
                if root.get(f"{axis}res") is not None:
                    doc.metadata[f"resolution_{axis}"] = _float(root.get(f"{axis}res"), f"{axis} resolution", .01, 9600)
            doc.metadata["import_format"] = "OpenRaster"
            return doc
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, KeyError) as error:
        raise ValueError(f"Invalid OpenRaster archive: {error}") from error
