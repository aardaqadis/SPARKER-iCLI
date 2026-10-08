"""Actual SDK transport handshakes and reversible image-tool workflows."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from PIL import Image
import pytest

from termatelier.ai_commands import execute_ai, returned_image
from termatelier.commands import CommandError, CommandSession, tokenize
from termatelier.config import RuntimeConfig
from termatelier.mcp_client import (MCPError, MCPRegistry, request, request_async,
                                    sanitize, validate_arguments, validate_connection)
from termatelier.model import Document
from termatelier.storage import png_bytes


SERVER_SOURCE = r'''
import asyncio, base64, io, os, sys
from PIL import Image as PILImage
from mcp.server.fastmcp import FastMCP, Image, Context
from mcp.types import CallToolResult, TextContent
from starlette.responses import Response
mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
server = FastMCP("SDK Image Fixture", host="127.0.0.1", port=port,
                log_level="ERROR", stateless_http=True, json_response=True)
@server.tool()
def echo_image(image_base64: str) -> Image:
    """Return identical pixels; deterministic protocol fixture, not an AI model."""
    return Image(data=base64.b64decode(image_base64), format="png")
@server.tool()
def upscale_image(image_base64: str, factor: int) -> Image:
    """Fixture enlargement used only to verify returned dimensions."""
    image = PILImage.open(io.BytesIO(base64.b64decode(image_base64))).convert("RGBA")
    image = image.resize((image.width * factor, image.height * factor), PILImage.Resampling.NEAREST)
    stream = io.BytesIO(); image.save(stream, "PNG")
    return Image(data=stream.getvalue(), format="png")
@server.tool()
def grayscale_mask() -> Image:
    image = PILImage.new("L", (8,6), 0); image.putpixel((3,2), 127)
    stream = io.BytesIO(); image.save(stream,"PNG")
    return Image(data=stream.getvalue(), format="png")
@server.tool()
def local_image(image_path: str) -> dict:
    return {"image_path": image_path}
@server.tool()
def remote_image_url() -> dict:
    return {"image_url": "https://example.invalid/result.png"}
@server.tool()
def downloadable_image_url() -> dict:
    return {"image_url": f"http://127.0.0.1:{port}/fixture-image"}
@server.custom_route("/fixture-image", methods=["GET"])
async def fixture_image(request):
    if request.headers.get("authorization"):
        return Response("MCP credentials must not reach image downloads", status_code=403)
    image = PILImage.new("RGBA", (8,6), (25,51,87,143))
    stream = io.BytesIO(); image.save(stream,"PNG")
    return Response(stream.getvalue(), media_type="image/png")
@server.tool()
def credential_echo(ctx: Context) -> str:
    request = getattr(ctx.request_context, "request", None)
    return (request.headers.get("authorization", "") if request else os.environ.get("SPARKER_TEST_TOKEN", ""))
@server.tool()
def fail() -> CallToolResult:
    return CallToolResult(isError=True, content=[TextContent(type="text", text="fixture rejected input")])
@server.tool()
async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "finished"
if __name__ == "__main__":
    server.run(transport="streamable-http" if mode == "http" else "stdio")
'''


@pytest.fixture
def sdk():
    return pytest.importorskip("mcp")


@pytest.fixture
def server_script(tmp_path, sdk):
    path = tmp_path / "sdk_fixture_server.py"
    path.write_text(SERVER_SOURCE, encoding="utf-8")
    return path


@pytest.fixture
def stdio(server_script):
    return {"name": "fixture", "transport": "stdio", "command": sys.executable,
            "args": [str(server_script)], "timeout": 15}


@pytest.fixture
def http_server(server_script):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(server_script), "http", str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("SDK HTTP fixture failed to start")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail("SDK HTTP fixture did not become ready")
        yield {"name": "http-fixture", "transport": "http", "url": f"http://127.0.0.1:{port}/mcp", "timeout": 15}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def run(session, line):
    tokens = tokenize(line)
    return execute_ai(session, tokens.pop(0), tokens)


def seeded_session(tmp_path):
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    session = CommandSession(Document(8, 6), config=config, base_dir=tmp_path)
    for y in range(6):
        for x in range(8):
            session.document.layer.image.putpixel((x, y), (x*21, y*31, 17+x*y, (x*33+y*13)%256))
    return session


@pytest.mark.parametrize("bad", [
    {"name": "bad name", "url": "https://example.com/mcp"},
    {"name": "ok", "url": "https://user:password@example.com/mcp"},
    {"name": "ok", "url": "https://example.com/mcp?token=secret"},
    {"name": "ok", "url": "file:///tmp/mcp"},
    {"name": "ok", "url": "https://example.com/mcp", "auth_env": "token value!"},
    {"name": "ok", "url": "https://example.com/mcp", "timeout": float("nan")},
    {"name": "ok", "transport": "stdio", "command": "python", "args": "-m server"},
    {"name": "ok", "transport": "stdio", "command": "python", "args": ["bad\narg"]},
    {"name": "ok", "transport": "stdio", "command": "python", "env_names": ["BAD=secret"]},
    {"name": "ok", "url": "https://example.com/mcp", "token": "never-store-me"},
])
def test_connection_validation(bad):
    with pytest.raises(MCPError):
        validate_connection(bad)


def test_registry_save_does_not_connect_or_store_auth_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_TEST_TOKEN", "a-private-fixture-token")
    session = seeded_session(tmp_path)
    result = run(session, "mcp add service --url https://example.invalid/mcp --auth-env SPARKER_TEST_TOKEN")
    assert "No connection was made" in result.text
    registry = MCPRegistry.load(session.config)
    assert registry.path == tmp_path / "mcp-servers.json"
    assert "a-private-fixture-token" not in registry.path.read_text()
    assert "SPARKER_TEST_TOKEN" in registry.path.read_text()
    run(session, "ai map background-remove service remove_bg --input-key image --input-mode data-uri")
    assert MCPRegistry.load(session.config).mappings["background-remove"]["tool"] == "remove_bg"
    run(session, "mcp remove service")
    assert not MCPRegistry.load(session.config).servers
    assert not MCPRegistry.load(session.config).mappings


def test_invalid_registry_does_not_fall_back_silently(tmp_path):
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    (tmp_path / "mcp-servers.json").write_text('{"format":"other","version":1}')
    with pytest.raises(MCPError, match="format"):
        MCPRegistry.load(config)


def test_unconfigured_commands_explain_settings_without_editing(tmp_path):
    session = seeded_session(tmp_path)
    original = session.document.layer.image.tobytes()
    assert "existing service" in run(session, "ai settings").text
    with pytest.raises(CommandError, match="ai settings"):
        run(session, "ai background-remove")
    assert session.document.layer.image.tobytes() == original
    assert not session.document.undo_stack


@pytest.mark.parametrize("transport_fixture", ["stdio", "http_server"])
def test_real_sdk_initialize_discovery_and_exact_rgba_call(transport_fixture, request: pytest.FixtureRequest):
    connection = request.getfixturevalue(transport_fixture)
    from termatelier.mcp_client import request as sdk_request
    metadata = sdk_request(connection)
    assert metadata["implementation"]["name"] == "SDK Image Fixture"
    assert metadata["protocol"]
    tool = next(tool for tool in metadata["tools"] if tool["name"] == "echo_image")
    assert tool["inputSchema"]["required"] == ["image_base64"]
    image = Image.new("RGBA", (7, 5), (13, 71, 241, 0))
    image.putpixel((3, 4), (21, 67, 91, 123))
    response = sdk_request(connection, "echo_image", {"image_base64": base64.b64encode(png_bytes(image)).decode()})
    decoded, _ = returned_image(response["result"], connection)
    assert decoded.tobytes() == image.tobytes()


def test_discovered_schema_rejects_missing_arguments_before_call(stdio):
    with pytest.raises(MCPError, match="Required fields"):
        request(stdio, "upscale_image", {})
    with pytest.raises(MCPError, match="not exposed"):
        request(stdio, "imaginary_background_remover", {})


def test_schema_validation_blocks_external_refs(sdk):
    with pytest.raises(MCPError, match="External schema"):
        validate_arguments({"name": "remote_ref", "inputSchema": {"$ref": "https://example.invalid/schema"}}, {})


def test_actual_tool_images_apply_with_undo_and_selection(tmp_path, stdio):
    session = seeded_session(tmp_path)
    registry = MCPRegistry.load(session.config)
    registry.add(stdio)
    registry.map("background-remove", {"server": "fixture", "tool": "echo_image", "input_key": "image_base64", "input_mode": "base64"})
    doc = session.document
    old_pixels = doc.layer.image.tobytes()
    doc.selection = Image.new("L", doc.size, 0)
    doc.selection.paste(255, (2, 1, 5, 4))
    count = len(doc.layers)
    result = run(session, "ai background-remove --layer Returned")
    assert result.changed and not result.document_replaced
    assert doc.layer.name == "Returned"
    assert len(doc.layers) == count + 1
    assert doc.layer.image.getpixel((0, 0))[3] == 0
    assert doc.layer.image.getpixel((3, 2)) == Image.frombytes("RGBA", doc.size, old_pixels).getpixel((3, 2))
    assert doc.undo()
    assert len(doc.layers) == count
    assert doc.layer.image.tobytes() == old_pixels
    assert doc.redo()
    assert doc.layer.name == "Returned"


def test_upscaled_result_new_canvas_and_lossless_external_output(tmp_path, stdio):
    session = seeded_session(tmp_path)
    registry = MCPRegistry.load(session.config)
    registry.add(stdio)
    registry.map("upscale", {"server": "fixture", "tool": "upscale_image", "input_key": "image_base64", "input_mode": "base64"})
    original = session.document.layer.rendered()
    result = run(session, 'ai upscale --args \'{"factor":2}\' --new')
    assert result.changed and result.document_replaced
    assert session.document.size == (16, 12)
    assert session.document.dirty
    expected = original.resize((16,12), Image.Resampling.NEAREST)
    assert session.document.layer.image.tobytes() == expected.tobytes()
    session.document = Document(8, 6)
    session.document.layer.image = original
    for extension in ("png", "webp", "tiff"):
        result = run(session, f'ai upscale --args \'{{"factor":2}}\' --output enlarged.{extension}')
        assert not result.changed
        with Image.open(tmp_path / "exports" / f"enlarged.{extension}") as encoded:
            assert encoded.size == (16, 12)
            assert encoded.convert("RGBA").tobytes() == expected.tobytes()


def test_mask_result_and_locked_layer_are_transactional(tmp_path, stdio):
    session = seeded_session(tmp_path)
    MCPRegistry.load(session.config).add(stdio)
    pixels = session.document.layer.image.tobytes()
    result = run(session, "mcp call fixture grayscale_mask --mask")
    assert result.changed
    assert session.document.layer.mask.getpixel((3, 2)) == 127
    assert session.document.layer.image.tobytes() == pixels
    assert session.document.undo()
    assert session.document.layer.mask is None
    session.document.layer.locked = True
    with pytest.raises(CommandError, match="locked"):
        run(session, "mcp call fixture grayscale_mask --mask")
    assert session.document.layer.image.tobytes() == pixels


def test_wrong_size_tool_failure_bad_args_preserve_document(tmp_path, stdio):
    session = seeded_session(tmp_path)
    registry = MCPRegistry.load(session.config); registry.add(stdio)
    registry.map("upscale", {"server": "fixture", "tool": "upscale_image", "input_key": "image_base64", "input_mode": "base64"})
    original = session.document.layer.image.tobytes()
    with pytest.raises(CommandError, match="--new"):
        run(session, 'ai upscale --args \'{"factor":2}\'')
    with pytest.raises(CommandError, match="reported an error"):
        run(session, "mcp call fixture fail")
    with pytest.raises(CommandError, match="JSON object"):
        run(session, "mcp call fixture echo_image --args []")
    assert session.document.layer.image.tobytes() == original
    assert not session.document.undo_stack


def test_service_paths_are_read_only_with_explicit_local_opt_in(tmp_path, stdio):
    image = Image.new("RGBA", (8, 6), (12, 47, 91, 131))
    path = tmp_path / "existing.png"; image.save(path)
    session = seeded_session(tmp_path); MCPRegistry.load(session.config).add(stdio)
    arguments = json.dumps({"image_path": str(path)})
    response = run(session, f"mcp call fixture local_image --args '{arguments}'")
    assert not response.changed
    response = run(session, f"mcp call fixture local_image --args '{arguments}' --accept-file --layer Local")
    assert response.changed
    assert session.document.layer.image.tobytes() == image.tobytes()
    with pytest.raises(MCPError, match="Remote service paths"):
        returned_image({"structuredContent": {"image_path": str(path)}}, {"transport": "http"}, accept_file=True)


def test_result_urls_are_metadata_and_never_downloaded(tmp_path, stdio):
    session = seeded_session(tmp_path); MCPRegistry.load(session.config).add(stdio)
    response = run(session, "mcp call fixture remote_image_url")
    assert "https://example.invalid/result.png" in response.text
    assert not response.changed
    with pytest.raises(CommandError, match="no inline image"):
        run(session, "mcp call fixture remote_image_url --output no-image.png")
    assert not (tmp_path / "exports" / "no-image.png").exists()


def test_stdio_token_env_and_http_bearer_values_are_redacted(tmp_path, stdio, http_server, monkeypatch):
    secret = "test-secret-should-never-be-shown"
    monkeypatch.setenv("SPARKER_TEST_TOKEN", secret)
    session = seeded_session(tmp_path)
    registry = MCPRegistry.load(session.config)
    stdio["auth_env"] = http_server["auth_env"] = "SPARKER_TEST_TOKEN"
    registry.add(stdio); registry.add(http_server)
    for name in ("fixture", "http-fixture"):
        result = run(session, f"mcp call {name} credential_echo")
        assert "[redacted]" in result.text
        assert secret not in result.text
    assert secret not in registry.path.read_text()


def test_missing_auth_env_does_not_start_process(stdio, monkeypatch):
    monkeypatch.delenv("SPARKER_TEST_TOKEN", raising=False)
    stdio["auth_env"] = "SPARKER_TEST_TOKEN"
    stdio["command"] = "not-an-executable"
    with pytest.raises(MCPError, match="not set"):
        request(stdio)


def test_actual_sdk_timeout_closes_request(stdio):
    # Leave enough startup time for SDK initialization before the slow tool.
    stdio["timeout"] = 3
    started = time.monotonic()
    with pytest.raises(MCPError, match="timed out"):
        request(stdio, "slow", {"seconds": 20})
    assert time.monotonic() - started < 9


def test_project_output_and_lossy_extensions_rejected_before_network(tmp_path):
    session = seeded_session(tmp_path)
    MCPRegistry.load(session.config).add({"name": "configured", "url": "https://example.invalid/mcp"})
    project_path = Path(__file__).parents[1] / "should-not-write.png"
    with pytest.raises((CommandError, ValueError), match="project folder"):
        run(session, f'mcp call configured echo --output "{project_path}"')
    with pytest.raises(CommandError, match="lossless"):
        run(session, "mcp call configured echo --output rejected.jpg")


async def test_async_client_runs_inside_active_event_loop(stdio):
    metadata = await request_async(stdio)
    assert metadata["implementation"]["name"] == "SDK Image Fixture"
    metadata_sync = await asyncio.to_thread(request, stdio)
    assert len(metadata_sync["tools"]) == len(metadata["tools"])


def test_optional_sdk_absence_keeps_settings_commands_usable(tmp_path, monkeypatch):
    monkeypatch.setattr("termatelier.mcp_client.sdk_available", lambda: False)
    session = seeded_session(tmp_path)
    run(session, "mcp add configured --url https://example.invalid/mcp")
    assert "configured" in run(session, "mcp list --json").text
    with pytest.raises(CommandError, match="mcp extra"):
        run(session, "mcp tools configured")


@pytest.mark.parametrize("result", [
    {"content": [{"type":"image", "mimeType":"image/png", "data":"not-valid-base64!!!"}]},
    {"content": [{"type":"image", "mimeType":"image/svg+xml", "data":"YWJj"}]},
    {"structuredContent": {"image_base64": "YWJj"}},
])
def test_malformed_tool_images_are_rejected(result):
    with pytest.raises(MCPError):
        returned_image(result, {"transport":"http"})


def test_sanitized_service_text_has_no_tokens_or_terminal_escape_codes(monkeypatch):
    monkeypatch.setenv("SPARKER_TEST_TOKEN", "private-test-token")
    data = "\x1b[31m\x9d8;;https://example.invalid\x07 private-test-token https://example.invalid/result?token=leak"
    rendered = sanitize(data, {"auth_env":"SPARKER_TEST_TOKEN"})
    assert "private-test-token" not in rendered
    assert "token=leak" not in rendered
    assert "\x1b" not in rendered and "\x9d" not in rendered


def test_image_result_download_is_explicit_bounded_and_does_not_forward_auth(tmp_path, http_server, monkeypatch):
    monkeypatch.setenv("SPARKER_TEST_TOKEN", "private-token-for-mcp-only")
    http_server["auth_env"] = "SPARKER_TEST_TOKEN"
    session = seeded_session(tmp_path)
    MCPRegistry.load(session.config).add(http_server)
    text_result = run(session, "mcp call http-fixture downloadable_image_url")
    assert not text_result.changed
    assert "fixture-image" in text_result.text
    result = run(session, "mcp call http-fixture downloadable_image_url --download-result --layer Downloaded")
    assert result.changed
    assert session.document.layer.image.getpixel((3,2)) == (25,51,87,143)
    assert session.document.undo()


def test_replace_bakes_rendered_mask_and_opacity_once_with_undo(tmp_path, stdio):
    session = seeded_session(tmp_path)
    doc = session.document
    doc.layer.opacity = .5
    doc.layer.mask = Image.new("L", doc.size, 127)
    expected = doc.layer.rendered()
    registry = MCPRegistry.load(session.config); registry.add(stdio)
    registry.map("denoise", {"server":"fixture", "tool":"echo_image", "input_key":"image_base64", "input_mode":"base64"})
    run(session, "ai denoise --replace")
    assert doc.layer.rendered().tobytes() == expected.tobytes()
    assert doc.layer.opacity == 1 and doc.layer.mask is None
    assert doc.undo()
    assert doc.layer.opacity == .5 and doc.layer.mask.getpixel((0,0)) == 127


def test_output_failure_rolls_back_combined_document_edit(tmp_path, stdio, monkeypatch):
    session = seeded_session(tmp_path)
    registry = MCPRegistry.load(session.config); registry.add(stdio)
    registry.map("denoise", {"server":"fixture", "tool":"echo_image", "input_key":"image_base64", "input_mode":"base64"})
    before = session.document.layer.image.tobytes()
    layers = len(session.document.layers)
    def fail(*args):
        raise OSError("injected write failure")
    monkeypatch.setattr("termatelier.ai_commands._output_image", fail)
    with pytest.raises(CommandError, match="injected write failure"):
        run(session, "ai denoise --output combined.png --layer Result")
    assert len(session.document.layers) == layers
    assert session.document.layer.image.tobytes() == before
    assert not session.document.undo_stack
