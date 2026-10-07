import json
import pytest
from textual.widgets import Input, RichLog
from termatelier.app import Studio
from termatelier.cli_app import CLIWorkspaceScreen
from termatelier.model import Document


@pytest.mark.asyncio
async def test_embedded_commands_edit_mouse_document_and_recall(tmp_path):
    app = Studio(Document(24, 20))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        field = app.screen.query_one("#cli-command-line", Input)
        assert isinstance(app.screen, CLIWorkspaceScreen) and field.has_focus
        assert app.screen.size == app.size
        field.value = 'rectangle 2 2 8 8 --filled --color orange'
        await pilot.press("enter")
        assert app.doc.layer.image.getpixel((4, 4)) == (255, 165, 0, 255)
        field.value = 'undo'
        await pilot.press("enter")
        assert app.doc.layer.image.getchannel("A").getbbox() is None
        field.value = 'redo'
        await pilot.press("enter")
        assert app.doc.layer.image.getpixel((4, 4)) == (255, 165, 0, 255)
        await pilot.press("up")
        assert field.value == "redo"
        await pilot.press("up")
        assert field.value == "undo"
        field.value = "col"
        await pilot.press("ctrl+space")
        assert field.value == "color "
        field.value = "view panels"
        await pilot.press("enter")
        assert app.screen_stack[0].has_class("zen")
        field.value = "view history"
        await pilot.press("enter")
        assert app.screen_stack[0].has_class("show-history")
        await pilot.press("escape")
        assert len(app.screen_stack) == 1 and app.canvas.has_focus


@pytest.mark.asyncio
async def test_console_tool_settings_and_error_leave_app_usable():
    app = Studio(Document(24, 20))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_console()
        await pilot.pause()
        field = app.screen.query_one("#cli-command-line", Input)
        field.value = 'color red'
        await pilot.press("enter")
        assert app.foreground == "#ff0000"
        field.value = 'brush --size 7 --opacity 50%'
        await pilot.press("enter")
        assert app.brush_size == 7 and app.opacity == .5
        field.value = 'tool eraser'
        await pilot.press("enter")
        assert app.tool == "eraser"
        before = app.doc.snapshot()
        field.value = 'filter unknown-effect'
        await pilot.press("enter")
        assert app.doc.revision == before[-1]
        assert app.doc.pending is None
        assert field.has_focus


@pytest.mark.asyncio
async def test_console_replacement_requires_unsaved_confirmation():
    app = Studio(Document(24, 20))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_console()
        await pilot.pause()
        app.execute_command('rectangle 2 2 8 8 --filled --color red')
        field = app.screen.query_one("#cli-command-line", Input)
        field.value = 'new 8x8'
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 3
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert app.doc.size == (24, 20)


@pytest.mark.asyncio
async def test_first_frame_closes_launcher_handshake(tmp_path, monkeypatch):
    state = tmp_path/"startup.json"
    state.write_text(json.dumps({"phase": "opening", "status": "Opening editor"}))
    monkeypatch.setenv("SPARKER_STARTUP_STATE", str(state))
    app = Studio(Document(8, 8))
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        assert json.loads(state.read_text())["phase"] == "ready"
