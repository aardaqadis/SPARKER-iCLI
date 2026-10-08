"""Native painting server protocol, file fidelity and atomic edit boundaries."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path
import sys

from PIL import Image
import pytest

from termatelier.config import RuntimeConfig
from termatelier.mcp_server import SessionStore
from termatelier.storage import load_project


def store(tmp_path, **options):
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    config.set("export.directory", str(tmp_path / "exports"), persist=False)
    return SessionStore(config, **options)


def test_native_server_creates_edits_saves_and_exports_exact_pixels(tmp_path):
    session_store = store(tmp_path)
    identity = session_store.create(12, 9, True, "Protocol painting")["session_id"]
    data = session_store.edit(identity, ["pixel 3 4 #12345680", "layer add Ink", "pencil 0,0 11,8 --color orange --size 1"])
    assert data["width"] == 12 and data["height"] == 9
    assert data["dirty"] and len(data["layers"]) == 2
    doc = session_store.sessions[identity].document
    expected = doc.composite()
    native = tmp_path / "painting.tart"
    session_store.save(identity, str(native))
    assert load_project(native).composite().tobytes() == expected.tobytes()
    for extension in ("png", "webp", "tiff"):
        output = session_store.export(identity, f"painting.{extension}")
        with Image.open(output["path"]) as decoded:
            assert decoded.size == doc.size
            assert decoded.convert("RGBA").tobytes() == expected.tobytes()
    scaled = session_store.export(identity, "enlarged.png", scale=3)
    with Image.open(scaled["path"]) as decoded:
        assert decoded.size == (36,27)
        assert decoded.convert("RGBA").tobytes() == expected.resize((36,27), Image.Resampling.NEAREST).tobytes()
    reopened = session_store.open(str(native))["session_id"]
    assert session_store.sessions[reopened].document.composite().tobytes() == expected.tobytes()
    preview = session_store.preview(identity)
    with Image.open(io.BytesIO(preview)) as decoded:
        assert decoded.convert("RGBA").tobytes() == expected.tobytes()
    session_store.close(identity)
    with pytest.raises(ValueError, match="Unknown"):
        session_store.info(identity)


@pytest.mark.parametrize("command", [
    "script private.sparker", "config set debug.enabled true", "debug export private.json",
    "mcp call provider remove_background", "ai background-remove", "open secret.png",
    "save forbidden.tart", "export forbidden.png", "clipboard load secret.png",
    "quit", "new 12x9", "text 0 0 --file private.txt", "python malicious.py",
])
def test_server_edit_excludes_file_settings_service_and_shell_operations(tmp_path, command):
    session_store = store(tmp_path)
    identity = session_store.create(8,6)["session_id"]
    doc = session_store.sessions[identity].document
    before = doc.composite().tobytes()
    with pytest.raises(ValueError):
        session_store.edit(identity, ["pixel 2 3 red", command])
    assert doc.composite().tobytes() == before
    assert not doc.undo_stack


def test_failed_batch_restores_pixels_layers_clipboard_and_history(tmp_path):
    session_store = store(tmp_path)
    identity = session_store.create(8,6)["session_id"]
    session_store.edit(identity, ["pixel 2 3 red", "copy"])
    doc = session_store.sessions[identity].document
    original = doc.layer.image.tobytes()
    previous_history = len(doc.undo_stack)
    clipboard = doc.clipboard[0].tobytes()
    with pytest.raises(ValueError, match="outside"):
        session_store.edit(identity, ["pixel 2 3 blue", "copy", "layer add Added", "pixel 999 999 green"])
    assert doc.layer.image.tobytes() == original
    assert doc.clipboard[0].tobytes() == clipboard
    assert len(doc.undo_stack) == previous_history
    assert len(doc.layers) == 2
    assert doc.pending is None


def test_aggregate_session_budget_and_session_limit_release_memory(tmp_path):
    session_store = store(tmp_path, max_sessions=1, max_bytes=8*1024*1024)
    identity = session_store.create(32,24)["session_id"]
    with pytest.raises(ValueError, match="session limit"):
        session_store.create(8,6)
    session_store.close(identity)
    with pytest.raises(ValueError, match="memory budget"):
        session_store.create(2048,1024)
    assert not session_store.sessions
    identity = session_store.create(32,24)["session_id"]
    before = session_store.info(identity)
    with pytest.raises(ValueError, match="memory budget"):
        session_store.edit(identity, ["canvas 2048x1024"])
    assert session_store.info(identity) == before


def test_server_openraster_and_project_export_policy(tmp_path):
    pytest.importorskip("numpy")
    session_store = store(tmp_path)
    identity = session_store.create(16,12)["session_id"]
    session_store.edit(identity, ["pixel 3 4 teal", "layer add Overlay", "pencil 0,0 15,11 --size 1"])
    expected = session_store.sessions[identity].document.composite()
    target = tmp_path / "editable.ora"
    session_store.save(identity, str(target))
    reopened = session_store.open(str(target))["session_id"]
    assert session_store.sessions[reopened].document.composite().tobytes() == expected.tobytes()
    project = Path(__file__).parents[1] / "must-not-write.png"
    with pytest.raises(ValueError, match="project folder"):
        session_store.export(identity, str(project))


async def test_real_sdk_native_server_handshake_edit_preview_export_and_reopen(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = {"SPARKER_CONFIG_FILE": str(tmp_path / "settings.json"),
           "SPARKER_EXPORT_DIR": str(tmp_path / "exports"),
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    parameters = StdioServerParameters(command=sys.executable, args=["-m","termatelier.mcp_server"], env=env)
    with open(os.devnull, "w") as errlog:
        async with stdio_client(parameters, errlog=errlog) as (read,write):
            async with ClientSession(read,write) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "SPARKER iCLI"
                tools = await client.list_tools()
                assert {"sparker_create","sparker_edit","sparker_export","sparker_preview"}.issubset({tool.name for tool in tools.tools})
                async def call(name, args):
                    result = await client.call_tool(name,args)
                    assert not result.isError, result.content
                    return result.structuredContent or json.loads(result.content[0].text)
                created = await call("sparker_create", {"width":10,"height":7,"transparent":True})
                identity = created["session_id"]
                edited = await call("sparker_edit", {"session_id":identity,"commands":["pixel 4 3 #2468ac80","line 0 0 9 6 --color orange --width 1"]})
                assert edited["dirty"]
                result = await client.call_tool("sparker_preview", {"session_id":identity})
                assert not result.isError
                block = next(block for block in result.content if block.type == "image")
                with Image.open(io.BytesIO(base64.b64decode(block.data))) as decoded:
                    expected = decoded.convert("RGBA")
                exported = await call("sparker_export", {"session_id":identity,"path":"protocol.png"})
                with Image.open(exported["path"]) as decoded:
                    assert decoded.size == (10,7)
                    assert decoded.convert("RGBA").tobytes() == expected.tobytes()
                native = tmp_path / "protocol.tart"
                await call("sparker_save", {"session_id":identity,"path":str(native)})
                reopened = await call("sparker_open", {"path":str(native)})
                assert len(reopened["layers"]) == 1
                rejection = await client.call_tool("sparker_edit", {"session_id":identity,"commands":["config set debug.enabled true"]})
                assert rejection.isError
                await call("sparker_close", {"session_id":identity})
