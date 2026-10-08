"""Full-resolution exports keep every pixel, independent of CLI view state."""

from PIL import Image, ImageDraw
import pytest
from textual.widgets import Button, Input, Select, Static

from termatelier.app import Studio
from termatelier.cli_app import CLIApp
from termatelier.commands import CommandSession
from termatelier.config import RuntimeConfig
from termatelier.file_explorer import FileChoice, FileExplorer
from termatelier.model import Document, Layer
from termatelier.rendering import ExportView
from termatelier.storage import export, load_project, save_project


def painting(size):
    image = Image.new("RGBA", size, (27, 78, 210, 255))
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.polygon([(0, height//2), (width//3, 1), (width-1, height//2)],
                 fill=(170, 177, 192, 255), outline=(19, 24, 36, 255), width=1)
    draw.line([(0, 0), (width-1, height-1)], fill=(240, 177, 35, 255), width=1)
    for index in range(3, width, max(2, width//19)):
        draw.rectangle((index, height//2, index+1, height-1), fill=(91, 196, 138, 255))
    doc = Document(*size)
    doc.layers = [Layer("Painting", image)]
    doc.active = 0
    return doc


@pytest.mark.parametrize("size,zoom", [((31, 19), .73), ((803, 603), .055)])
@pytest.mark.parametrize("mode", ["nearest", "bilinear", "bicubic"])
@pytest.mark.asyncio
async def test_standard_export_preserves_native_detail_and_matches_one_to_one_canvas(tmp_path, size, zoom, mode):
    doc = painting(size)
    original = doc.layer.image.tobytes()
    app = Studio(doc)
    async with app.run_test(size=(150, 50)) as pilot:
        await pilot.pause()
        app.config.set("view.resampling", mode, persist=False)
        canvas = app.canvas
        canvas.zoom, canvas.pan_x, canvas.pan_y = zoom, 0, 0
        expected = doc.composite()
        # Export after changing view state, before any pending redraw. Panning,
        # selections, guides and unfinished previews must not crop or tint it.
        canvas.pan_x, canvas.pan_y = size[0]/2, -11.25
        canvas.grid = canvas.guides = True
        doc.metadata.update(guides_x=[2], guides_y=[3])
        doc.select("rectangle", (0, 0), (5, 5))
        canvas.preview = Image.new("RGBA", size, (245, 226, 133, 255))
        target = tmp_path / f"{size[0]}-{mode}.png"
        app._export_choice(FileChoice(target, "export"))
        assert doc.export_view == ExportView(zoom, mode)
        # At 1:1 zoom the terminal exposes the same original pixels as the
        # exported file. Guides, selections and transient previews are removed.
        canvas.zoom, canvas.pan_x, canvas.pan_y = 1, 0, 0
        canvas.grid = canvas.guides = False
        canvas.preview = None
        doc.selection = None
        one_to_one = canvas.screen_image()
        visible = (0, 0, min(one_to_one.width, size[0]), min(one_to_one.height, size[1]))
        assert one_to_one.crop(visible).tobytes() == expected.crop(visible).tobytes()
    with Image.open(target) as restored:
        assert restored.size == size
        restored = restored.convert("RGBA")
        assert restored.tobytes() == expected.tobytes()
        assert restored.getchannel("A").getextrema() == (255, 255)
        colors = {color for _, color in restored.getcolors(size[0]*size[1])}
        assert not ({(54, 54, 54, 255), (72, 72, 72, 255)} & colors)
    assert doc.layer.image.tobytes() == original
    assert doc.size == size


@pytest.mark.asyncio
async def test_f3_and_command_workspace_exports_use_current_unpainted_view(tmp_path):
    doc = painting((157, 93))
    cfg = RuntimeConfig({"preview.enabled": False})
    app = Studio(doc, config=cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # No refresh between changing these values and entering the CLI.
        app.canvas.zoom = .37
        cfg.set("view.resampling", "bilinear", persist=False)
        await pilot.press("f3")
        assert doc.export_view == ExportView(.37, "bilinear")
        screen = app.screen
        target = tmp_path / "f3.png"
        screen.submit_command(f'export "{target}"')
        expected = doc.composite()
        with Image.open(target) as restored:
            assert restored.convert("RGBA").tobytes() == expected.tobytes()
        # View commands keep exports current while the painting screen is hidden.
        screen.submit_command("view zoom 23")
        screen.submit_command("view resampling bicubic")
        assert doc.export_view.zoom == pytest.approx(.23)
        assert doc.export_view.resampling == "bicubic"
        changed = tmp_path / "view-command.png"
        screen.submit_command(f'export "{changed}"')
        expected = doc.composite()
        with Image.open(changed) as restored:
            assert restored.convert("RGBA").tobytes() == expected.tobytes()
        await pilot.press("f3")
        assert app.doc is doc


@pytest.mark.asyncio
async def test_standalone_cli_uses_same_document_export_view_and_preserves_alpha(tmp_path):
    doc = painting((113, 71))
    doc.layer.image.putpixel((30, 20), (91, 121, 18, 80))
    doc.export_view = ExportView(.29, "nearest")
    expected = doc.composite()
    before = doc.layer.image.tobytes()
    session = CommandSession(doc, config=RuntimeConfig({"preview.enabled": False}))
    app = CLIApp(session)
    async with app.run_test(size=(110, 35)) as pilot:
        await pilot.pause()
        target = tmp_path / "standalone.png"
        app.workspace.submit_command(f'export "{target}"')
        with Image.open(target) as restored:
            assert restored.convert("RGBA").tobytes() == expected.tobytes()
    assert doc.layer.image.tobytes() == before
    assert doc.export_view == ExportView(.29, "nearest")


@pytest.mark.asyncio
async def test_native_save_retains_editable_pixels_without_session_export_view(tmp_path):
    doc = painting((31, 19))
    app = Studio(doc)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.pause()
        app.canvas.zoom = .45
        app.store_tool_settings()
        assert doc.export_view == ExportView(.45, "nearest")
        path = tmp_path / "editable.tart"
        save_project(doc, path)
    loaded = load_project(path)
    assert loaded.layer.image.tobytes() == doc.layer.image.tobytes()
    assert loaded.settings == doc.settings
    assert getattr(loaded, "export_view", None) is None
    result = export(loaded, tmp_path / "fresh.png")
    with Image.open(result) as restored:
        assert restored.convert("RGBA").tobytes() == loaded.composite().tobytes()


@pytest.mark.asyncio
async def test_cli_resize_explorer_shows_canvas_dimensions_and_exports_that_size(tmp_path):
    directory = tmp_path / "exports"
    directory.mkdir()
    cfg = RuntimeConfig({"preview.enabled": False, "export.directory": str(directory)})
    session = CommandSession(painting((220, 161)), config=cfg)
    app = CLIApp(session)
    async with app.run_test(size=(105, 42)) as pilot:
        await pilot.pause()
        screen = app.workspace
        screen.submit_command("canvas 317x213")
        assert screen.document.size == (317, 213)
        pixels = screen.document.composite().tobytes()
        expected = screen.document.composite()
        await pilot.press("ctrl+e")
        await pilot.pause()
        explorer = app.screen
        assert isinstance(explorer, FileExplorer)
        dimensions = explorer.query_one("#file-canvas-size", Static)
        assert dimensions.display
        assert str(dimensions.content) == "Image: 317×213 px (canvas)"
        explorer.query_one("#columns", Input).value = "7"
        target = directory / "resized.png"
        explorer.query_one("#path", Input).value = str(target)
        await pilot.pause()
        assert await pilot.click("#file-submit")
        await pilot.pause()
        assert app.screen is screen
        with Image.open(target) as restored:
            assert restored.size == (317, 213)
            assert restored.convert("RGBA").tobytes() == expected.tobytes()
        assert screen.document.composite().tobytes() == pixels

        # Text columns control text output only and do not resize the painting.
        screen.submit_command("files export")
        await pilot.pause()
        explorer = app.screen
        text_path = directory / "resized.txt"
        explorer.query_one("#path", Input).value = str(text_path)
        explorer.query_one("#columns", Input).value = "7"
        await pilot.pause()
        assert not explorer.query_one("#file-canvas-size", Static).display
        await pilot.pause(explorer.query_one("#file-submit", Button).active_effect_duration + .02)
        assert await pilot.click("#file-submit")
        await pilot.pause()
        assert app.screen is screen
        assert all(len(line) == 7 for line in text_path.read_text(encoding="utf-8").splitlines())
        assert screen.document.size == (317, 213)


@pytest.mark.asyncio
async def test_studio_export_and_file_hub_show_the_current_canvas_size():
    app = Studio(painting((173, 97)))
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        app.action_export()
        await pilot.pause()
        assert str(app.screen.query_one("#file-canvas-size", Static).content) == "Image: 173×97 px (canvas)"
        await pilot.press("escape")
        await pilot.pause()
        app.action_files()
        await pilot.pause()
        explorer = app.screen
        assert not explorer.query_one("#file-canvas-size", Static).display
        explorer.query_one("#file-operation", Select).value = "export"
        await pilot.pause()
        dimensions = explorer.query_one("#file-canvas-size", Static)
        assert dimensions.display
        assert str(dimensions.content) == "Image: 173×97 px (canvas)"
