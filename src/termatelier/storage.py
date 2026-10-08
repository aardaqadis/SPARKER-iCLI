"""Versioned .tart ZIP projects; atomic persistence and flattened exports."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

from PIL import Image, ImageColor

from .model import BLENDS, MAX_LAYERS, Document, Layer, valid_size

FORMAT = "org.termatelier.project"
VERSION = 2
MAX_PROJECT_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 12 * 1024 * 1024


def project_roots():
    """Directories that must never receive flattened image or text exports."""
    module_path = Path(__file__).resolve()
    roots = []
    configured = os.environ.get("SPARKER_PROJECT_ROOT")
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    # Source checkouts and extracted releases have a src/termatelier package;
    # installed packages instead protect their own package directory.
    package_root = module_path.parent
    project = package_root.parent.parent if package_root.parent.name == "src" else package_root
    roots.append(project)
    # The local workspace may contain a thin launcher around the project.
    if (project.parent / "run.ps1").is_file() and (project.parent / "run.cmd").is_file():
        roots.append(project.parent)
    return tuple(dict.fromkeys(roots))


def validate_export_directory(path):
    """Resolve symlinks and reject export destinations inside application files."""
    resolved = Path(os.path.expandvars(str(path))).expanduser().resolve()
    if any(resolved.is_relative_to(root) for root in project_roots()):
        raise ValueError("Exports cannot be placed in the program/project folder. "
                         "Choose a folder outside the SPARKER iCLI workspace.")
    return resolved


def _runtime_config(config):
    if config is None:
        from .config import RuntimeConfig
        config = RuntimeConfig.load()
    return config


def default_export_directory(config=None):
    config = _runtime_config(config)
    directory = os.environ.get("SPARKER_EXPORT_DIR") or config.get("export.directory")
    return validate_export_directory(directory)


def resolve_export_path(path=None, config=None):
    """Put ordinary relative names under the external export directory.

    Absolute paths and explicitly ./ or ../ anchored paths retain their location,
    but all destinations are checked after resolution, including symlinks.
    Merely resolving a name does not create any directories.
    """
    raw = "Untitled.png" if path is None else os.fspath(path)
    if not raw.strip():
        raise ValueError("An export filename is required.")
    requested = Path(os.path.expandvars(raw)).expanduser()
    anchored = raw.startswith(("./", ".\\", "../", "..\\"))
    if requested.is_absolute() or anchored:
        resolved = requested.resolve()
    else:
        resolved = (default_export_directory(config) / requested).resolve()
    validate_export_directory(resolved)
    return resolved


def _validate_manifest(data):
    """Validate the editable structure before allocating any decoded images."""
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a JSON object.")
    if data.get("format") != FORMAT or type(data.get("version")) is not int or data["version"] not in (1, VERSION):
        raise ValueError("Unsupported project format or version.")
    width, height = data["width"], data["height"]
    valid_size(width, height)
    entries = data["layers"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_LAYERS:
        raise ValueError("Invalid layer count.")
    if width * height * (5*len(entries)+1) > MAX_PROJECT_BYTES:
        raise ValueError("Decoded project exceeds the 128 MiB pixel budget.")

    def reference(name, optional=False):
        if optional and name is None:
            return
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid image reference.")

    reference(data.get("selection"), optional=True)
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid layer entry.")
        opacity = entry.get("opacity", 1)
        if type(opacity) not in (float, int) or not 0 <= opacity <= 1:
            raise ValueError("Layer opacity is outside 0–1.")
        if entry.get("blend", "normal") not in BLENDS:
            raise ValueError("Unsupported blend mode.")
        if any(type(entry.get(key, default)) is not bool for key, default in (("visible", True), ("locked", False))):
            raise ValueError("Invalid layer flags.")
        if not isinstance(entry["name"], str) or len(entry["name"]) > 256:
            raise ValueError("Invalid layer name.")
        reference(entry["image"])
        reference(entry.get("mask"), optional=True)
        from .document_tools import validate_effects, validate_text_recipe
        validate_effects(entry.get("effects", []))
        validate_text_recipe(entry.get("text_recipe"))
    active = data["active"]
    if type(active) is not int or not 0 <= active < len(entries):
        raise ValueError("Invalid active layer.")
    metadata, settings = data.get("metadata", {}), data.get("settings", {})
    if not isinstance(metadata, dict) or not isinstance(settings, dict):
        raise ValueError("Invalid metadata.")
    from .document_tools import validate_metadata
    validate_metadata(metadata, (width, height))
    if not isinstance(metadata.get("title", "Untitled"), str):
        raise ValueError("Invalid project title.")
    for key in ("guides_x", "guides_y"):
        values = metadata.get(key, [])
        if not isinstance(values, list) or len(values) > 100 or any(type(v) is not int for v in values):
            raise ValueError("Invalid guides.")
    spacing = metadata.get("grid_spacing", 8)
    if type(spacing) is not int or not 1 <= spacing <= 4096:
        raise ValueError("Invalid grid spacing.")
    if "palette" in settings:
        palette = settings["palette"]
        if not isinstance(palette, list) or not 1 <= len(palette) <= 64:
            raise ValueError("Invalid palette.")
        for color in palette:
            if not isinstance(color, str):
                raise ValueError("Invalid palette color.")
            ImageColor.getrgb(color)
    if "retouch_options" in settings:
        from .native_options import validate_retouch_options
        validate_retouch_options(settings["retouch_options"])
    for name, maximum in (("clone_source", 1), ("foreground_marks", 32)):
        if name in settings:
            from .painting_tools import checked_points
            points = [settings[name]] if name == "clone_source" else settings[name]
            if not isinstance(points, list) or len(points) > maximum:
                raise ValueError("Invalid stored source points.")
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(v) not in (int, float) for v in point):
                    raise ValueError("Invalid stored source point coordinates.")
            if points: checked_points(points, 1, maximum)


def _reject_nonfinite(value):
    raise ValueError(f"Non-finite JSON number: {value}.")


def png_bytes(image):
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def atomic_write(path, writer):
    path = Path(path).expanduser().resolve()
    if not path.parent.is_dir():
        raise ValueError("The destination folder does not exist.")
    fd, temporary = tempfile.mkstemp(prefix=".termatelier-", dir=path.parent)
    os.close(fd)
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def save_project(doc, path):
    if Path(path).suffix.lower() != ".tart":
        raise ValueError("Native project files use the .tart extension.")
    manifest = {"format": FORMAT, "version": VERSION, "width": doc.width, "height": doc.height,
                "active": doc.active, "metadata": doc.metadata, "settings": doc.settings, "layers": []}
    for index, layer in enumerate(doc.layers):
        manifest["layers"].append({"name": layer.name, "image": f"layers/{index}.png",
                                   "visible": layer.visible, "locked": layer.locked,
                                   "opacity": layer.opacity, "blend": layer.blend,
                                   "mask": f"masks/{index}.png" if layer.mask is not None else None,
                                   "effects": layer.effects, "text_recipe": layer.text_recipe})
    manifest["selection"] = "selection.png" if doc.selection is not None else None
    _validate_manifest(manifest)
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)
    if len(manifest_json.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest is too large.")
    for layer in doc.layers:
        if layer.image.mode != "RGBA" or layer.image.size != doc.size:
            raise ValueError("Layers must be canvas-sized RGBA images.")
        if layer.mask is not None and (layer.mask.mode != "L" or layer.mask.size != doc.size):
            raise ValueError("Layer masks must be canvas-sized grayscale images.")
    if doc.selection is not None and (doc.selection.mode != "L" or doc.selection.size != doc.size):
        raise ValueError("Selection must be a canvas-sized grayscale image.")
    def write(temporary):
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_json)
            for index, layer in enumerate(doc.layers):
                archive.writestr(f"layers/{index}.png", png_bytes(layer.image))
                if layer.mask is not None: archive.writestr(f"masks/{index}.png", png_bytes(layer.mask))
            if doc.selection is not None: archive.writestr("selection.png", png_bytes(doc.selection))
            if sum(info.file_size for info in archive.infolist()) > MAX_PROJECT_BYTES:
                raise ValueError("Project exceeds the 128 MiB archive limit.")
    atomic_write(path, write)
    doc.saved_revision = doc.revision


def load_project(path):
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 132 or len({x.filename for x in infos}) != len(infos):
                raise ValueError("Invalid archive entry count or duplicate entries.")
            if sum(x.file_size for x in infos) > MAX_PROJECT_BYTES:
                raise ValueError("Project exceeds the 128 MiB archive limit.")
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                raise ValueError("Manifest is too large.")
            data = json.loads(archive.read("manifest.json"), parse_constant=_reject_nonfinite)
            _validate_manifest(data)
            width, height = data["width"], data["height"]
            entries = data["layers"]
            def read_image(name, mode):
                if not isinstance(name, str): raise ValueError("Invalid image reference.")
                with Image.open(io.BytesIO(archive.read(name))) as image:
                    if image.size != (width, height) or image.format != "PNG":
                        raise ValueError("Project images must be canvas-sized PNGs.")
                    return image.convert(mode)
            layers = []
            for entry in entries:
                opacity = entry.get("opacity", 1)
                blend = entry.get("blend", "normal")
                name = entry["name"]
                layers.append(Layer(name, read_image(entry["image"], "RGBA"), entry.get("visible", True),
                                    entry.get("locked", False), opacity, blend,
                                    read_image(entry["mask"], "L") if entry.get("mask") else None,
                                    entry.get("effects", []), entry.get("text_recipe")))
            active = data["active"]
            doc = Document(width, height)
            doc.layers, doc.active = layers, active
            doc.selection = read_image(data["selection"], "L") if data.get("selection") else None
            metadata = data.get("metadata", {})
            settings = data.get("settings", {})
            doc.metadata.update(metadata)
            doc.settings.update(settings)
            return doc
    except (KeyError, TypeError, zipfile.BadZipFile, json.JSONDecodeError, OSError,
            RuntimeError, NotImplementedError, EOFError, OverflowError,
            Image.DecompressionBombError) as error:
        raise ValueError(f"Invalid or unreadable project: {error}") from error


def open_image(path):
    if Path(path).suffix.lower() == ".ora":
        from .ora_tools import load_ora
        return load_ora(path).composite()
    with Image.open(Path(path).expanduser()) as image:
        valid_size(*image.size)
        # Honor camera orientation; use the first frame for animated imports.
        from PIL import ImageOps
        return ImageOps.exif_transpose(image).convert("RGBA")


def import_document(path):
    if Path(path).suffix.lower() == ".ora":
        from .ora_tools import load_ora
        return load_ora(path)
    image = open_image(path)
    doc = Document(*image.size)
    doc.layers = [Layer(Path(path).stem, image)]
    doc.active = 0
    doc.metadata["title"] = Path(path).stem
    doc.revision = doc.serial = 1
    return doc


MAX_EXPORT_PIXELS = 16_777_216


def export_dimensions(size, scale=1, *, extension=None):
    """Validate crisp integer enlargement before allocating export pixels."""
    if type(scale) is not int or not 1 <= scale <= 16:
        raise ValueError("Image export scale must be an integer from 1 to 16.")
    if (not isinstance(size, (tuple, list)) or len(size) != 2 or
            any(type(edge) is not int or edge < 1 for edge in size)):
        raise ValueError("Export dimensions must be two positive integers.")
    width, height = (edge * scale for edge in size)
    max_edge = 16383 if extension and str(extension).lower() == ".webp" else 16384
    if max(width, height) > max_edge or width * height > MAX_EXPORT_PIXELS:
        raise ValueError(f"Choose a smaller image export scale: {width}×{height} exceeds "
                         f"{max_edge:,} pixels per side or {MAX_EXPORT_PIXELS:,} pixels total.")
    return width, height


def export_image(doc, path=None, *, allow_lossy=False, config=None, scale=None):
    config = _runtime_config(config)
    path = resolve_export_path(path, config)
    extension = path.suffix.lower()
    formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP",
               ".bmp": "BMP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF"}
    if extension not in formats:
        raise ValueError("Export as .png, .jpg, .webp, .bmp, .gif, .tif, .txt or .ansi.")
    if extension in (".jpg", ".jpeg", ".bmp", ".gif") and config.get("export.lossless") and not allow_lossy:
        raise ValueError("This format cannot preserve every RGBA pixel. Use PNG, TIFF or "
                         "lossless WebP, or explicitly allow lossy export.")
    size = export_dimensions(doc.size, config.get("export.scale") if scale is None else scale,
                             extension=extension)
    # Export editable pixel data at full resolution. Terminal minification,
    # zoom and overlays are presentation only and must never discard detail.
    image = doc.composite()
    if image.mode != "RGBA" or image.size != doc.size:
        raise ValueError("The image must have the original canvas dimensions and RGBA pixels.")
    if size != doc.size:
        # Each original pixel becomes an exact N×N block, including hidden RGB
        # and alpha. Integer nearest sampling adds no blur or invented colors.
        image = image.resize(size, Image.Resampling.NEAREST)
    if extension in (".jpg", ".jpeg", ".bmp"):
        background = Image.new("RGBA", size, "white")
        image = Image.alpha_composite(background, image).convert("RGB")
    options = {"quality": 95} if extension in (".jpg", ".jpeg") else {}
    if extension == ".webp":
        # exact preserves even invisible RGB data in fully transparent pixels.
        options.update(lossless=True, exact=True, method=6)
    elif extension in (".tif", ".tiff"):
        options["compression"] = "tiff_deflate"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, lambda tmp: image.save(tmp, formats[extension], **options))
    return path


def export_text(doc, path, columns=100, ansi=False, *, config=None):
    path = resolve_export_path(path, config)
    columns = max(1, min(500, int(columns)))
    image = doc.composite()
    background = Image.new("RGBA", doc.size, "white")
    image = Image.alpha_composite(background, image).convert("RGB")
    width = min(columns, image.width)
    height = max(1, round(image.height * width / image.width / 2))
    if ansi:
        image = image.resize((width, height*2), Image.Resampling.LANCZOS)
        lines = []
        for y in range(0, image.height, 2):
            parts = []
            for x in range(width):
                a, b = image.getpixel((x, y)), image.getpixel((x, y+1))
                parts.append(f"\x1b[38;2;{a[0]};{a[1]};{a[2]}m\x1b[48;2;{b[0]};{b[1]};{b[2]}m▀")
            lines.append("".join(parts) + "\x1b[0m")
    else:
        image = image.resize((width, height), Image.Resampling.LANCZOS).convert("L")
        ramp = "@%#*+=-:. "
        lines = ["".join(ramp[round(image.getpixel((x, y))*9/255)] for x in range(width)) for y in range(height)]
    content = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, lambda tmp: Path(tmp).write_text(content, encoding="utf-8"))
    return path


def export(doc, path=None, columns=100, *, allow_lossy=False, config=None, scale=None):
    config = _runtime_config(config)
    path = resolve_export_path(path, config)
    suffix = path.suffix.lower()
    if suffix == ".ora":
        if scale is not None and (type(scale) is not int or scale != 1):
            raise ValueError("OpenRaster layers use the original canvas size; omit --scale or use 1.")
        from .ora_tools import save_ora
        return save_ora(doc, path, config=config)
    if suffix in (".txt", ".ansi"):
        if scale is not None and (type(scale) is not int or scale != 1):
            raise ValueError("Export scale applies to image files; use --columns for text exports.")
        return export_text(doc, path, columns, suffix == ".ansi", config=config)
    return export_image(doc, path, allow_lossy=allow_lossy, config=config, scale=scale)
