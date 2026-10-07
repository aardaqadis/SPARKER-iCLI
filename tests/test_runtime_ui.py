import json
from pathlib import Path

from PIL import Image
import pytest
from textual.widgets import Input, Select, Static

from termatelier.app import Studio
from termatelier.config import RuntimeConfig
from termatelier.model import Document


@pytest.mark.asyncio
async def test_console_preferences_apply_to_tools_history_and_preview(tmp_path):
    cfg = RuntimeConfig.load()
    cfg.set("brush.size", 9)
    cfg.set("history.max_steps", 3)
    app = Studio(Document(31, 19), config=cfg)
    assert app.brush_size == 9 and app.doc.history_limit == 3
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_console()
        await pilot.pause()
        app.execute_command("config set brush.size 11")
        app.execute_command("config set brush.opacity 0.4")
        app.execute_command("config set history.max_steps 1")
        assert app.brush_size == 11 and app.opacity == .4
        assert app.doc.history_limit == 1
        app.execute_command("rectangle 1 1 8 8 --filled")
        app.execute_command("rectangle 10 1 18 8 --filled")
        assert len(app.doc.undo_stack) == 1
        pixels = app.doc.composite().tobytes()
        app.execute_command("view resampling bicubic")
        assert cfg.get("view.resampling") == "bicubic"
        assert app.doc.composite().tobytes() == pixels
        app.execute_command("config set view.zoom_min 0.1")
        app.execute_command("view zoom 10")
        assert app.canvas.zoom == .1
        app.execute_command("view zoom 1")
        assert app.canvas.zoom == .1
        app.execute_command("debug off")
        assert not cfg.get("debug.enabled")
        await pilot.pause()
        assert app.screen.query_one("#cli-command-line", Input).has_focus


@pytest.mark.asyncio
async def test_debug_telemetry_refreshes_while_dialog_open(tmp_path, monkeypatch):
    state = tmp_path / "debug.json"
    monkeypatch.setenv("SPARKER_DEBUG_STATE", str(state))
    app = Studio(Document(31, 19))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_brush_options()
        await pilot.pause()
        app.publish_debug(force=True)
        data = json.loads(state.read_text())["diagnostics"]
        assert data["program"]["name"] == "SPARKER iCLI"
        assert "config_exists" in data["venv"]
        assert data["document"]["width"] == 31
        assert data["terminal"]["columns"] == 120
        assert data["runtime"]["tool"] == "brush"
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_export_dialog_external_default_lossless_and_project_rejection(tmp_path):
    cfg = RuntimeConfig.load()
    folder = tmp_path / "external" / "exports"
    cfg.set("export.directory", str(folder))
    app = Studio(Document(31, 19), config=cfg)
    original = app.doc.composite().tobytes()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.canvas.zoom = .125
        app.canvas.pan_x = 12
        app.action_export()
        await pilot.pause()
        assert app.screen.query_one("#path", Input).value == str(folder / "painting.png")
        app.screen.query_one("#path", Input).value = "full.png"
        await pilot.press("enter")
        await pilot.pause()
        with Image.open(folder / "full.png") as image:
            assert image.size == (31, 19)
            assert image.tobytes() == original
        app.action_export()
        await pilot.pause()
        app.screen.query_one("#path", Input).value = str(Path(__file__).resolve().parents[1] / "forbidden.png")
        await pilot.press("enter")
        await pilot.pause()
        assert "outside" in str(app.screen.query_one("#file-error", Static).render())
        assert len(app.screen_stack) == 2
        app.screen.query_one("#path", Input).value = "lossy.jpg"
        await pilot.press("enter")
        await pilot.pause()
        assert not (folder / "lossy.jpg").exists()
        app.screen.query_one("#allow-lossy", Select).value = "yes"
        await pilot.press("enter")
        await pilot.pause()
        with Image.open(folder / "lossy.jpg") as image:
            assert image.size == (31, 19)


@pytest.mark.asyncio
async def test_invalid_external_export_override_keeps_editor_usable(monkeypatch):
    app = Studio(Document(31, 19))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        monkeypatch.setenv("SPARKER_EXPORT_DIR", str(Path(__file__).resolve().parents[1]))
        app.action_export()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        app.action_console()
        await pilot.pause()
        app.execute_command("rectangle 1 1 5 5 --filled --color red")
        assert app.doc.layer.image.getpixel((2, 2)) == (255, 0, 0, 255)
