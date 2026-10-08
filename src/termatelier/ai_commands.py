"""Existing-service MCP image commands; no embedded AI models or fake AI filters."""
from __future__ import annotations

import base64
import binascii
import copy
import io
import json
from pathlib import Path
import warnings

from PIL import Image, ImageChops

from .mcp_client import (INPUT_MODES, MAX_MESSAGE_BYTES, MCPError, MCPRegistry,
                         OPERATIONS, request, sanitize)
from .model import Document, Layer, MAX_DOCUMENT_BYTES, MAX_LAYERS, valid_size
from .storage import atomic_write, png_bytes, resolve_export_path

HELP = {
    "mcp": "mcp settings | list [--json] | add NAME --url URL [--auth-env ENV_NAME] [--timeout 1..600] | add NAME --command EXECUTABLE [--args JSON_ARRAY] [--env-names JSON_ARRAY] | remove NAME | tools [SERVER] [--json] | call SERVER TOOL [--args JSON_OBJECT] [--output PATH] [--layer NAME | --replace | --new | --mask] [--accept-file] [--download-result] [--json] — actual existing MCP service tools",
    "ai": "ai settings | tools [SERVER] [--json] | mappings [--json] | map OPERATION SERVER TOOL --input-key FIELD [--input-mode base64|data-uri|path|url|none] | unmap OPERATION | background-remove|upscale|denoise|inpaint|generate|restore|colorize [--server NAME --tool TOOL --input-key FIELD --input-mode MODE] [--input IMAGE_OR_PUBLIC_URL] [--source active|merged] [--args JSON_OBJECT] [--output PATH] [--layer NAME | --replace | --new | --mask] [--accept-file] [--download-result] [--json] | call SERVER TOOL [--args JSON_OBJECT] — schema-validated image tools supplied by your MCP service; settings prompts for connection details",
}


def _object(value, label="Arguments", array=False):
    if not isinstance(value, str) or len(value) > MAX_MESSAGE_BYTES:
        raise MCPError(f"{label} is too large.")
    try:
        data = json.loads(value, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, RecursionError):
        raise MCPError(f"{label} must be valid finite JSON.") from None
    if not isinstance(data, list if array else dict):
        raise MCPError(f"{label} must be a JSON {'array' if array else 'object'}.")
    return data


def _open_image(source, allow_export_size=False):
    try:
        if isinstance(source, (str, Path)):
            source = Path(source)
            if source.stat().st_size > MAX_MESSAGE_BYTES:
                raise MCPError("Image file exceeds the 32 MiB input limit.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if allow_export_size:
                    width, height = image.size
                    if not (1 <= width <= 16384 and 1 <= height <= 16384 and width * height <= 16777216):
                        raise MCPError("Returned image exceeds 16,777,216 pixels or 16,384 pixels per side.")
                else:
                    valid_size(*image.size)
                if getattr(image, "n_frames", 1) != 1:
                    raise MCPError("Animated tool images are not supported; return a single image.")
                image.load()
                original_mode = image.mode
                return image.convert("RGBA"), original_mode
    except MCPError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MCPError("The tool image could not be decoded within SPARKER's pixel budget.") from None


def _decode_image(value, mime=None):
    if mime and mime not in ("image/png", "image/webp", "image/jpeg", "image/tiff", "image/bmp", "image/gif"):
        raise MCPError("MCP images must be PNG, WebP, JPEG, TIFF, BMP or a single-frame GIF.")
    if not isinstance(value, str):
        raise MCPError("Returned image data must be base64 text.")
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or not header.endswith(";base64"):
            raise MCPError("Returned data URI is not a base64 image.")
    if len(value) > (MAX_MESSAGE_BYTES * 4 // 3 + 8):
        raise MCPError("Returned image exceeds the 32 MiB limit.")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise MCPError("Returned image contains invalid base64.") from None
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MCPError("Returned image exceeds the 32 MiB limit.")
    return _open_image(io.BytesIO(raw), allow_export_size=True)


def returned_image(result, connection, accept_file=False):
    """Decode only explicit image values. Arbitrary text/URLs are never executed."""
    for block in result.get("content", []):
        if block.get("type") == "image":
            return _decode_image(block.get("data"), block.get("mimeType"))
        if block.get("type") == "resource":
            resource = block.get("resource", {})
            if resource.get("mimeType", "").startswith("image/") and "blob" in resource:
                return _decode_image(resource["blob"], resource["mimeType"])
    structured = result.get("structuredContent")
    candidates = [structured] if isinstance(structured, dict) else []
    for block in result.get("content", []):
        if block.get("type") == "text" and len(block.get("text", "")) <= MAX_MESSAGE_BYTES:
            try:
                value = json.loads(block["text"])
                if isinstance(value, dict):
                    candidates.append(value)
            except (ValueError, RecursionError):
                pass
    for candidate in candidates:
        if isinstance(candidate.get("result"), dict):
            candidates.append(candidate["result"])
        for key in ("image_base64", "image", "mask", "data", "base64"):
            value = candidate.get(key)
            if isinstance(value, dict) and isinstance(value.get("data"), str):
                return _decode_image(value["data"], value.get("mimeType", value.get("mime_type")))
            if isinstance(value, str) and not value.startswith(("http://", "https://", "file://")):
                return _decode_image(value, candidate.get("mimeType", candidate.get("mime_type")))
        path = candidate.get("image_path", candidate.get("output_path"))
        if path is not None and accept_file:
            if connection["transport"] != "stdio":
                raise MCPError("Remote service paths cannot be read on this computer. Ask the service for inline image data or a URL.")
            if not isinstance(path, str) or len(path) > 4096:
                raise MCPError("Returned image path is invalid.")
            return _open_image(Path(path).expanduser().resolve(), allow_export_size=True)
    return None


def _summary(response, connection):
    result = copy.deepcopy(response)
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "image":
            block["data"] = "[image data]"
        elif block.get("type") == "resource" and "blob" in block.get("resource", {}):
            block["resource"]["blob"] = "[image data]"
        elif block.get("type") == "text":
            try:
                value = json.loads(block.get("text", ""))
                if isinstance(value, (dict, list)):
                    block["text"] = json.dumps(sanitize(value, connection))
            except (ValueError, RecursionError):
                pass
    return sanitize(result, connection)


def _download_image_result(result, connection):
    """Only explicit --download-result fetches service-returned HTTP(S) pixels.

    Service credentials are deliberately absent from this separate image request,
    even for a redirect or an image hosted on the same origin.
    """
    from urllib.parse import urlsplit
    import httpx
    urls = []
    def visit(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("image_url", "output_url", "download_url", "url") and isinstance(child, str):
                    urls.append(child)
                elif isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:512]:
                visit(child, depth + 1)
    visit(result.get("structuredContent", {}))
    for block in result.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "").strip()
            if text.startswith(("http://", "https://")) and not any(char.isspace() for char in text):
                urls.append(text)
            else:
                try:
                    visit(json.loads(text))
                except (ValueError, RecursionError):
                    pass
    urls = list(dict.fromkeys(urls))
    if len(urls) != 1:
        raise MCPError("--download-result requires exactly one explicit image result URL. Ask the service for a single image or inline image data.")
    url = urls[0]
    parts = urlsplit(url)
    if len(url) > 8192 or parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise MCPError("Returned image URL must use HTTP(S) without embedded credentials.")
    try:
        data = bytearray()
        with httpx.Client(timeout=connection["timeout"], follow_redirects=True, max_redirects=3, headers={"Accept-Encoding":"identity"}) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise MCPError("Image host returned compressed transport data despite requesting identity encoding.")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_MESSAGE_BYTES:
                    raise MCPError("Downloaded tool image exceeds the 32 MiB limit.")
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > MAX_MESSAGE_BYTES:
                        raise MCPError("Downloaded tool image exceeds the 32 MiB limit.")
                    data.extend(chunk)
        return _open_image(io.BytesIO(data), allow_export_size=True)
    except MCPError:
        raise
    except (httpx.HTTPError, ValueError):
        raise MCPError("Could not download the image result. The URL may have expired or the image host requires separate authentication; no MCP token is forwarded.") from None


def _output_image(image, path):
    formats = {".png": "PNG", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF"}
    extension = path.suffix.lower()
    if extension not in formats:
        raise MCPError("AI image output must use lossless .png, .webp, .tif or .tiff.")
    options = {"lossless": True, "exact": True, "method": 6} if extension == ".webp" else {"compression": "tiff_deflate"} if extension in (".tif", ".tiff") else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, lambda temporary: image.save(temporary, formats[extension], **options))


def execute_ai(session, command, tokens):
    if command not in HELP:
        return None
    from .commands import CommandError, CommandResult, _args, _count
    try:
        return _execute_ai(session, command, tokens, CommandResult, _args, _count)
    except (MCPError, ValueError, OSError) as error:
        raise CommandError(str(error)) from None


def _execute_ai(session, command, tokens, Result, args_parser, count):
    registry = MCPRegistry.load(session.config)
    action = tokens[0].lower() if tokens else "settings"
    tail = tokens[1:]
    if action == "settings":
        count(tail, 0, 0)
        return Result("Open AI / MCP settings in the painter or full-screen CLI to enter your existing service's endpoint or executable. In batch use mcp add NAME --url URL or --command EXECUTABLE. No service is configured automatically.")
    if action in ("list", "mappings"):
        args, opts = args_parser(tail, flags=("json",))
        count(args, 0, 0)
        data = list(registry.servers.values()) if action == "list" else registry.mappings
        if opts.get("json"):
            return Result(json.dumps(sanitize(data), indent=2))
        if not data:
            return Result("No MCP services configured. Open ai settings to connect your existing service." if action == "list" else "No AI tool mappings. Discover tools with mcp tools SERVER, then use ai map.")
        if action == "list":
            return Result("\n".join(f"{server['name']} · {server['transport']} · {server.get('url', server.get('command'))} · auth environment: {server.get('auth_env') or 'none'}" for server in data))
        return Result("\n".join(f"{operation}: {mapping['server']}/{mapping['tool']} · {mapping['input_key']} ({mapping['input_mode']})" for operation, mapping in data.items()))
    if command == "mcp" and action == "add":
        args, opts = args_parser(tail, options=("url", "command", "args", "auth-env", "env-names", "timeout"))
        count(args, 1, 1)
        if bool(opts.get("url")) == bool(opts.get("command")):
            raise MCPError("Choose exactly one --url or --command transport.")
        try:
            timeout = float(opts.get("timeout", 90))
        except ValueError:
            raise MCPError("MCP timeout must be 1–600 seconds.") from None
        data = {"name": args[0], "transport": "http" if opts.get("url") else "stdio", "auth_env": opts.get("auth-env", ""), "timeout": timeout}
        if opts.get("url"):
            if "args" in opts or "env-names" in opts:
                raise MCPError("--args and --env-names are only for stdio.")
            data["url"] = opts["url"]
        else:
            data.update(command=opts["command"], args=_object(opts.get("args", "[]"), "Executable arguments", array=True), env_names=_object(opts.get("env-names", "[]"), "Environment names", array=True))
        server = registry.add(data)
        return Result(f"Saved MCP connection {server['name']}. No connection was made. Discover its tools with mcp tools {server['name']}.")
    if command == "mcp" and action == "remove":
        count(tail, 1, 1)
        registry.remove(tail[0])
        return Result(f"Removed MCP connection {tail[0]} and its AI mappings.")
    if command == "ai" and action == "map":
        args, opts = args_parser(tail, options=("input-key", "input-mode"))
        count(args, 3, 3)
        mapping = {"server": args[1], "tool": args[2], "input_key": opts.get("input-key", ""), "input_mode": opts.get("input-mode", "base64")}
        registry.map(args[0], mapping)
        return Result(f"Mapped {args[0]} to {args[1]}/{args[2]}. Tool arguments will be checked against the service's actual schema when invoked.")
    if command == "ai" and action == "unmap":
        count(tail, 1, 1)
        if tail[0] not in registry.mappings:
            raise MCPError("That AI operation has no mapping.")
        del registry.mappings[tail[0]]
        registry.save()
        return Result(f"Removed AI mapping {tail[0]}.")
    if action == "tools":
        args, opts = args_parser(tail, flags=("json",))
        count(args, 0, 1)
        server = registry.server(args[0] if args else None)
        data = request(server)
        if opts.get("json"):
            return Result(json.dumps(sanitize(data, server), indent=2))
        return Result(f"{data['implementation']['name']} · MCP {data['protocol']}\n" + ("\n".join(f"{tool['name']}: {sanitize(tool.get('description', ''), server)}" for tool in data["tools"]) or "No tools exposed."))
    if action != "call" and (command != "ai" or action not in OPERATIONS):
        raise MCPError("Unknown MCP/AI action. Use help mcp or help ai.")

    args, opts = args_parser(tail, options=("args", "server", "tool", "input-key", "input-mode", "input", "source", "output", "layer"), flags=("replace", "new", "mask", "accept-file", "download-result", "json"))
    convenience = action != "call"
    if convenience:
        count(args, 0, 0)
        mapping = registry.mappings.get(action, {})
        server_name = opts.get("server", mapping.get("server"))
        tool_name = opts.get("tool", mapping.get("tool"))
        input_key = opts.get("input-key", mapping.get("input_key"))
        mode = opts.get("input-mode", mapping.get("input_mode", "base64"))
        if not tool_name or (mode != "none" and not input_key):
            raise MCPError(f"Configure {action} in ai settings or use ai map. No tool or image field is guessed; mcp tools SERVER --json shows the service's schema.")
    else:
        count(args, 2, 2)
        if any(key in opts for key in ("server", "tool", "input-key", "input-mode", "input", "source")):
            raise MCPError("For a generic call supply SERVER TOOL and exact --args JSON; image input helpers belong to named AI operations.")
        server_name, tool_name = args
        input_key, mode = None, "none"
    server = registry.server(server_name)
    if mode not in INPUT_MODES:
        raise MCPError("Unknown image input mode.")
    if mode == "path" and server["transport"] != "stdio":
        raise MCPError("Remote services cannot read local paths; use base64, data-uri or a public URL.")
    if opts.get("source", "active") not in ("active", "merged"):
        raise MCPError("--source must be active or merged.")
    if sum(bool(opts.get(key)) for key in ("layer", "replace", "new", "mask")) > 1:
        raise MCPError("Choose one of --layer, --replace, --new or --mask.")
    doc = session.document
    if opts.get("replace") or opts.get("mask"):
        doc.ensure_editable()
    requested_output = opts.get("output")
    output = resolve_export_path(requested_output, config=session.config) if requested_output else None
    if output and output.suffix.lower() not in (".png", ".webp", ".tif", ".tiff"):
        raise MCPError("AI outputs use lossless PNG, WebP or TIFF.")
    arguments = _object(opts.get("args", "{}"))
    if input_key in arguments:
        raise MCPError("Image input field is already in --args. Supply it once.")
    if convenience and mode != "none":
        if mode == "url":
            from urllib.parse import urlsplit
            value = opts.get("input", "")
            parts = urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
                raise MCPError("URL input requires --input with an explicit HTTP(S) image URL.")
        elif mode == "path":
            if not opts.get("input"):
                raise MCPError("Local path input requires --input IMAGE. The canvas is never saved to a hidden service file.")
            path = session._path(opts["input"])
            _open_image(path)
            value = str(path)
        else:
            if opts.get("input"):
                image, _ = _open_image(session._path(opts["input"]))
            else:
                image = doc.layer.rendered() if opts.get("source", "active") == "active" else doc.composite()
            value = base64.b64encode(png_bytes(image)).decode("ascii")
            if mode == "data-uri":
                value = "data:image/png;base64," + value
        arguments[input_key] = value
    elif convenience and (opts.get("input") or opts.get("source")):
        raise MCPError("Input mode none does not take an image or source.")

    response = request(server, tool_name, arguments)
    decoded = returned_image(response["result"], server, accept_file=bool(opts.get("accept-file")))
    if decoded is None and opts.get("download-result"):
        decoded = _download_image_result(response["result"], server)
    summary = _summary(response, server)
    apply = convenience or command == "ai" or any(opts.get(key) for key in ("layer", "replace", "new", "mask"))
    if decoded is None:
        if output or (apply and any(opts.get(key) for key in ("layer", "replace", "new", "mask"))):
            raise MCPError("Service returned no inline image. A result URL is retained as text; use --download-result to fetch one explicit image URL. For a trusted local stdio image_path use --accept-file.")
        return Result(json.dumps(summary, indent=2))
    image, original_mode = decoded
    summary["image"] = {"width": image.width, "height": image.height, "mode": "RGBA"}
    # Validate document mutations before writing any requested output file.
    # Export-only results can be larger than an editable document.
    edits_document = apply and (not output or any(opts.get(key) for key in ("layer", "replace", "new", "mask")))
    if edits_document:
        valid_size(*image.size)
        if not opts.get("new") and image.size != doc.size:
            raise MCPError(f"Tool returned {image.width}×{image.height}, canvas is {doc.width}×{doc.height}. Use --new for a new canvas, or --output PATH to save full-size pixels.")
        if not any(opts.get(key) for key in ("new", "replace", "mask")):
            if len(doc.layers) >= MAX_LAYERS or doc.width * doc.height * (5 * (len(doc.layers) + 1) + 1) > MAX_DOCUMENT_BYTES:
                raise MCPError("Adding the AI result would exceed the document layer or pixel-memory budget. Use --output or --new.")
            if len(opts.get("layer", "")) > 256:
                raise MCPError("Layer names must contain at most 256 characters.")
    if output:
        summary["output"] = str(output)
        if not any(opts.get(key) for key in ("layer", "replace", "new", "mask")):
            apply = False
    changed, replaced = False, False
    if apply:
        if opts.get("new"):
            replacement = Document(*image.size)
            replacement.layers = [Layer(f"AI · {action if convenience else tool_name}", image)]
            replacement.active = 0
            replacement.clipboard = doc.clipboard
            replacement.settings = copy.deepcopy(doc.settings)
            replacement.metadata["title"] = "AI result"
            replacement.metadata["ai_service"] = server["name"]
            replacement.metadata["ai_tool"] = tool_name
            replacement.revision = replacement.serial = 1
            if output:
                _output_image(image, output)
            session.document = replacement
            session.project_path = None
            changed = replaced = True
        else:
            label = f"AI · {action if convenience else tool_name}"
            with doc.edit(label):
                if opts.get("replace"):
                    # The service receives rendered pixels. Bake the previous
                    # rendering outside a selection and do not apply its mask,
                    # opacity or effect stack a second time to returned pixels.
                    previous = doc.layer.rendered()
                    doc.layer.image = Image.composite(image, previous, doc.selection) if doc.selection is not None else image
                    doc.layer.mask = None
                    doc.layer.opacity = 1.0
                    if hasattr(doc.layer, "effects"):
                        doc.layer.effects = []
                    if hasattr(doc.layer, "text_recipe"):
                        doc.layer.text_recipe = None
                elif opts.get("mask"):
                    mask = image.convert("L") if original_mode in ("1", "L", "LA") else image.getchannel("A")
                    if doc.selection is not None:
                        original = doc.layer.mask or Image.new("L", doc.size, 255)
                        mask = Image.composite(mask, original, doc.selection)
                    doc.layer.mask = mask
                else:
                    layer_pixels = image
                    if doc.selection is not None:
                        layer_pixels = image.copy()
                        layer_pixels.putalpha(ImageChops.multiply(image.getchannel("A"), doc.selection))
                    doc.add_layer(opts.get("layer", label), layer_pixels)
                if output:
                    _output_image(image, output)
            changed = True
    elif output:
        _output_image(image, output)
    text = json.dumps(summary, indent=2) if opts.get("json") or not changed else f"Applied {server['name']}/{tool_name} · {image.width}×{image.height} pixels" + (f"\nSaved: {output}" if output else "")
    return Result(text, changed=changed, document_replaced=replaced)
