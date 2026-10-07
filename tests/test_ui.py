from pathlib import Path
import pytest
from textual.widgets import Input, OptionList
from textual import events
from termatelier.app import Studio
from termatelier.model import Document
from termatelier.storage import load_project


@pytest.mark.asyncio
async def test_mouse_draw_undo_redo_and_menu(tmp_path):
    app = Studio(Document(32, 24))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        await pilot.click("#canvas", offset=(5, 4))
        await pilot.pause()
        assert app.doc.layer.image.getchannel("A").getbbox() is not None
        assert len(app.doc.undo_stack) == 1
        await pilot.press("ctrl+z")
        assert app.doc.layer.image.getchannel("A").getbbox() is None
        await pilot.press("ctrl+y")
        assert app.doc.layer.image.getchannel("A").getbbox() is not None
        await pilot.click("#menu-0")
        await pilot.pause()
        menu = app.screen.query_one("#menu-options", OptionList)
        assert menu.get_option("files").id == "files"
        assert menu.get_option("run_script").id == "run_script"
        assert menu.get_option("export_folder").id == "export_folder"
        await pilot.press("escape")
        app.project_path = tmp_path/"mouse.tart"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert load_project(app.project_path).layer.image.tobytes() == app.doc.layer.image.tobytes()
        assert not app.doc.dirty


@pytest.mark.asyncio
async def test_form_validation_layer_properties_and_text():
    app = Studio(Document(48, 32))
    async with app.run_test(size=(120, 50)) as pilot:
        app.action_layer_properties()
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "Ink"
        app.screen.query_one("#opacity", Input).value = "200"
        await pilot.click("#form-apply")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        app.screen.query_one("#opacity", Input).value = "50"
        app.screen.query_one("#opacity", Input).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.doc.layer.name == "Ink" and app.doc.layer.opacity == .5
        app.text_dialog((1, 1))
        await pilot.pause()
        app.screen.query_one("#text", Input).value = "Art"
        await pilot.click("#form-apply")
        await pilot.pause()
        assert app.doc.layer.image.getchannel("A").getbbox() is not None


@pytest.mark.asyncio
async def test_keyboard_tools_zoom_and_smaller_terminal():
    app = Studio(Document(64, 48))
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.press("e")
        assert app.tool == "eraser"
        await pilot.press("0")
        fit = app.canvas.zoom
        await pilot.press("plus")
        assert app.canvas.zoom > fit
        await pilot.press("x")
        assert app.foreground == "#ffffff"
        assert app.canvas.screen_image().size == (app.canvas.size.width, app.canvas.size.height*2)


@pytest.mark.asyncio
async def test_selection_mouse_and_shapes():
    app = Studio(Document(32, 24))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool("select_rect")
        # Pilot mouse-down/up and hover exercise capture rather than direct engine calls.
        await pilot.mouse_down("#canvas", offset=(3, 2))
        await pilot.hover("#canvas", offset=(10, 5))
        await pilot.mouse_up("#canvas", offset=(10, 5))
        await pilot.pause()
        assert app.doc.selection.getbbox() == (3, 4, 11, 11)
        app.action_tool("rectangle")
        app.filled = True
        await pilot.mouse_down("#canvas", offset=(0, 0))
        await pilot.hover("#canvas", offset=(15, 8))
        await pilot.mouse_up("#canvas", offset=(15, 8))
        await pilot.pause()
        assert app.doc.layer.image.getchannel("A").getbbox() == (3, 4, 11, 11)
        assert len(app.doc.undo_stack) == 2


@pytest.mark.asyncio
async def test_cancel_drag_and_unsaved_guard():
    app = Studio(Document(32, 24))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        await pilot.mouse_down("#canvas", offset=(5, 3))
        await pilot.hover("#canvas", offset=(10, 3))
        await pilot.press("escape")
        assert app.doc.pending is None and not app.canvas.dragging
        assert app.doc.layer.image.getchannel("A").getbbox() is None
        await pilot.click("#canvas", offset=(5, 3))
        app.action_quit()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert len(app.screen_stack) == 1 and app.doc.dirty


@pytest.mark.asyncio
async def test_save_reopen_export_through_dialogs(tmp_path):
    from PIL import Image
    app = Studio(Document(24, 20))
    project, output = tmp_path/"workflow.tart", tmp_path/"workflow.png"
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        await pilot.click("#canvas", offset=(5, 4))
        app.action_save_as()
        await pilot.pause()
        app.screen.query_one("#path", Input).value = str(project)
        await pilot.press("enter")
        await pilot.pause()
        assert project.is_file() and not app.doc.dirty
        saved = app.doc.composite().tobytes()
        app.action_export()
        await pilot.pause()
        app.screen.query_one("#path", Input).value = str(output)
        await pilot.press("enter")
        await pilot.pause()
        with Image.open(output) as image: assert image.convert("RGBA").tobytes() == saved
        app.replace_document(Document(8, 8))
        app.action_open()
        await pilot.pause()
        app.screen.query_one("#path", Input).value = str(project)
        await pilot.press("enter")
        await pilot.pause()
        assert app.doc.size == (24, 20) and app.doc.composite().tobytes() == saved


@pytest.mark.asyncio
async def test_pan_pick_gradient_and_move_mouse():
    app = Studio(Document(40, 24))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool("gradient")
        await pilot.mouse_down("#canvas", offset=(1, 2))
        await pilot.hover("#canvas", offset=(20, 2))
        await pilot.mouse_up("#canvas", offset=(20, 2))
        assert app.doc.layer.image.getpixel((1, 4))[:3] == (231, 147, 53)
        app.action_tool("picker")
        await pilot.click("#canvas", offset=(20, 2))
        assert app.foreground == "#ffffff"
        app.action_tool("move")
        await pilot.mouse_down("#canvas", offset=(3, 2))
        await pilot.hover("#canvas", offset=(8, 3))
        await pilot.mouse_up("#canvas", offset=(8, 3))
        assert app.doc.layer.image.getpixel((0, 0))[3] == 0
        app.action_tool("hand")
        await pilot.mouse_down("#canvas", offset=(8, 3))
        await pilot.hover("#canvas", offset=(12, 4))
        await pilot.mouse_up("#canvas", offset=(12, 4))
        assert app.canvas.pan_x == -4 and app.canvas.pan_y == -2
        assert len(app.doc.undo_stack) == 2
