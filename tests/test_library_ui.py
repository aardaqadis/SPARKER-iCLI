import pytest
from PIL import Image
from textual.widgets import Input, Select

from termatelier.app import Studio
from termatelier.model import Document
from termatelier.tool_browser import ToolLibraryBrowser
from termatelier.tool_library import get_tool


@pytest.mark.asyncio
async def test_browser_search_preview_choose_and_mouse_stamp_share_undo():
    app = Studio(Document(80, 64))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_tool_library()
        await pilot.pause()
        browser = app.screen
        assert isinstance(browser, ToolLibraryBrowser)
        assert len(browser.matches) >= 1001
        browser.query_one("#library-search", Input).value = "oak wreath net"
        await pilot.pause()
        assert len(browser.matches) == 1
        assert browser.selected == "botanical.oak.wreath.net"
        await pilot.click("#library-use")
        await pilot.pause()
        assert app.tool == "library" and app.library_tool == "botanical.oak.wreath.net"
        app.action_actual()
        await pilot.click("#canvas", offset=(24, 13))
        assert app.doc.layer.image.getbbox()
        assert len(app.doc.undo_stack) == 1 and app.doc.pending is None
        image = app.doc.layer.image.tobytes()
        app.action_undo()
        assert app.doc.layer.image.getbbox() is None
        app.action_redo()
        assert app.doc.layer.image.tobytes() == image


@pytest.mark.asyncio
async def test_library_live_brush_cancel_pattern_bounds_and_options():
    app = Studio(Document(80, 64))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.library_tool, app.tool = "natural-media.grass.slash.striated", "library"
        app.library_size = 20
        app.store_tool_settings()
        app.sync_ui()
        app.action_actual()
        await pilot.mouse_down("#canvas", offset=(18, 10))
        await pilot.hover("#canvas", offset=(34, 14))
        await pilot.press("escape")
        assert app.doc.layer.image.getbbox() is None and app.doc.pending is None
        assert not app.doc.undo_stack
        app.library_tool = "tessellation.brick.stepped.outline"
        app.store_tool_settings()
        await pilot.mouse_down("#canvas", offset=(7, 4))
        await pilot.hover("#canvas", offset=(27, 14))
        await pilot.mouse_up("#canvas", offset=(27, 14))
        assert app.doc.layer.image.crop((7, 8, 28, 29)).getbbox()
        assert app.doc.layer.image.crop((28, 0, 80, 64)).getbbox() is None
        app.action_library_options()
        await pilot.pause()
        app.screen.query_one("#size", Input).value = "48"
        app.screen.query_one("#angle", Input).value = "37"
        await pilot.press("enter")
        await pilot.pause()
        assert app.library_size == 48 and app.library_angle == 37
        assert app.doc.settings["library_size"] == 48


@pytest.mark.asyncio
async def test_browser_filters_and_effect_applies_to_selection():
    doc = Document(80, 64)
    doc.layer.image = Image.new("RGBA", doc.size, "#e79335")
    doc.select("rectangle", (0, 0), (39, 63))
    app = Studio(doc)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        before = doc.layer.image.tobytes()
        app.action_tool_library()
        await pilot.pause()
        app.screen.query_one("#library-kind", Select).value = "effect"
        app.screen.query_one("#library-search", Input).value = "effects.invert"
        await pilot.pause()
        assert len(app.screen.matches) == 1
        await pilot.click("#library-apply")
        await pilot.pause()
        assert doc.layer.image.tobytes() != before
        assert doc.layer.image.getpixel((60, 30)) == (231, 147, 53, 255)
        assert doc.layer.image.getpixel((20, 30)) == (24, 108, 202, 255)
        assert doc.undo() and doc.layer.image.tobytes() == before


@pytest.mark.asyncio
async def test_fullscreen_cli_library_selection_returns_to_mouse_document(tmp_path):
    app = Studio(Document(80, 64))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        field = app.screen.query_one("#cli-command-line", Input)
        field.value = "tool geometric.star.radial.hatching"
        await pilot.press("enter")
        field.value = "tool-options --size 24 --angle 30"
        await pilot.press("enter")
        field.value = "apply geometric.star.radial.hatching 40,32 --color orange"
        await pilot.press("enter")
        image = app.doc.layer.image.tobytes()
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        assert app.tool == "library" and app.library_size == 24 and app.library_angle == 30
        assert app.doc.layer.image.tobytes() == image
        await pilot.click("#canvas", offset=(10, 7))
        assert app.doc.layer.image.tobytes() != image
        assert len(app.doc.undo_stack) == 4
