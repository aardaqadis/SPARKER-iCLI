"""Export scaling in both file browsers preserves every editable canvas pixel."""
import copy

from PIL import Image
import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from termatelier.app import Studio
from termatelier.cli_app import CLIApp
from termatelier.commands import CommandSession
from termatelier.config import RuntimeConfig
from termatelier.file_explorer import FileChoice, FileExplorer
from termatelier.model import Document, Layer


class ExplorerApp(App):
    def __init__(self, explorer):
        super().__init__()
        self.explorer, self.result = explorer, "pending"

    def on_mount(self):
        self.push_screen(self.explorer, lambda choice: setattr(self, "result", choice))


def painting():
    doc = Document(31, 19)
    image = Image.new("RGBA", doc.size)
    image.putdata([((x * 7 + y) % 256, (y * 19 + x) % 256,
                    (x * 11 + y * 3) % 256, (x * 29 + y * 17) % 256)
                   for y in range(doc.height) for x in range(doc.width)])
    doc.layers = [Layer("Fine detail", image)]
    doc.active = 0
    return doc


def state(doc):
    return (doc.size, [(layer.name, layer.image.tobytes(), layer.opacity, layer.blend,
                       layer.mask.tobytes() if layer.mask is not None else None) for layer in doc.layers],
            doc.active, doc.selection.tobytes() if doc.selection is not None else None,
            copy.deepcopy(doc.metadata), doc.revision, doc.saved_revision, len(doc.undo_stack))


def caption(explorer):
    return str(explorer.query_one("#file-canvas-size", Static).content)


async def pick(app, pilot, path):
    explorer = app.screen
    assert isinstance(explorer, FileExplorer)
    explorer.query_one("#path", Input).value = str(path)
    await pilot.pause()
    await pilot.pause(explorer.query_one("#file-submit", Button).active_effect_duration + .02)
    assert await pilot.click("#file-submit")
    await pilot.pause()


@pytest.mark.asyncio
async def test_live_scale_caption_default_preference_and_choice(tmp_path):
    cfg = RuntimeConfig({"export.directory": str(tmp_path), "export.scale": 2})
    explorer = FileExplorer("export", tmp_path, "painting.png", config=cfg, canvas_size=(31, 19))
    app = ExplorerApp(explorer)
    async with app.run_test(size=(100, 38)) as pilot:
        await pilot.pause()
        field = explorer.query_one("#export-scale", Input)
        assert field.value == "2" and field.display
        assert caption(explorer) == "Image: 62×38 px · 2× crisp · canvas 31×19"
        field.value = "1"
        await pilot.pause()
        assert caption(explorer) == "Image: 31×19 px (canvas)"
        field.value = "4"
        await pilot.pause()
        assert caption(explorer) == "Image: 124×76 px · 4× crisp · canvas 31×19"
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(tmp_path / "painting.png", "export", scale=4)
        assert not (tmp_path / "painting.png").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".txt", ".ansi"])
async def test_text_exports_hide_scale_and_ignore_invalid_hidden_input(tmp_path, suffix):
    explorer = FileExplorer("export", tmp_path, "painting.png", canvas_size=(31, 19))
    app = ExplorerApp(explorer)
    async with app.run_test(size=(100, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#export-scale", Input).value = "invalid"
        explorer.query_one("#path", Input).value = "painting" + suffix
        explorer.query_one("#columns", Input).value = "11"
        await pilot.pause()
        assert not explorer.query_one("#export-scale", Input).display
        assert not explorer.query_one("#file-scale-label", Static).display
        assert not explorer.query_one("#file-canvas-size", Static).display
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(tmp_path / ("painting" + suffix), "export", columns=11, scale=1)


@pytest.mark.asyncio
async def test_operation_switch_hides_scale_and_restores_image_caption(tmp_path):
    explorer = FileExplorer("export", tmp_path, "painting.png", allow_operations=True, canvas_size=(31, 19))
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#export-scale", Input).value = "3"
        explorer.query_one("#file-operation", Select).value = "save"
        await pilot.pause()
        assert not explorer.query_one("#export-scale", Input).display
        assert not explorer.query_one("#file-canvas-size", Static).display
        explorer.query_one("#file-operation", Select).value = "export"
        await pilot.pause()
        assert explorer.query_one("#export-scale", Input).display
        assert caption(explorer) == "Image: 93×57 px · 3× crisp · canvas 31×19"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "17", "1.5", "", "wrong"])
async def test_bad_scale_rejected_before_dismissal_or_overwrite(tmp_path, value):
    destination = tmp_path / "existing.png"
    destination.write_bytes(b"original file")
    explorer = FileExplorer("export", tmp_path, destination.name, canvas_size=(31, 19))
    app = ExplorerApp(explorer)
    async with app.run_test(size=(100, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#export-scale", Input).value = value
        await pilot.pause()
        explorer.submit()
        await pilot.pause()
        assert app.screen is explorer and app.result == "pending"
        assert "scale" in str(explorer.query_one("#file-error", Static).content).lower()
        assert destination.read_bytes() == b"original file"


@pytest.mark.asyncio
async def test_image_budget_and_webp_edge_limit_are_live_and_block_selection(tmp_path):
    explorer = FileExplorer("export", tmp_path, "painting.png", canvas_size=(4096, 1024))
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#export-scale", Input).value = "3"
        await pilot.pause()
        assert "crisp" not in caption(explorer) and "scale" in caption(explorer).lower()
        explorer.submit()
        await pilot.pause()
        assert app.screen is explorer and app.result == "pending"
        assert str(explorer.query_one("#file-error", Static).content)
        assert not (tmp_path / "painting.png").exists()
    explorer = FileExplorer("export", tmp_path, "painting.png", canvas_size=(4096, 1))
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#export-scale", Input).value = "4"
        await pilot.pause()
        assert "16384×4" in caption(explorer)
        explorer.query_one("#file-format", Select).value = ".webp"
        await pilot.pause()
        assert "crisp" not in caption(explorer) and "16,383" in caption(explorer)
        explorer.submit()
        await pilot.pause()
        assert app.screen is explorer and app.result == "pending"
        assert "16,383" in str(explorer.query_one("#file-error", Static).content)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["studio", "cli"])
async def test_file_exports_scale_exact_original_pixels_and_keep_canvas_unchanged(tmp_path, kind):
    cfg = RuntimeConfig({"preview.enabled": False, "export.directory": str(tmp_path), "export.scale": 1})
    doc = painting()
    app = Studio(doc, config=cfg) if kind == "studio" else CLIApp(CommandSession(doc, config=cfg))
    async with app.run_test(size=(110, 42)) as pilot:
        await pilot.pause()
        before = state(doc)
        expected = doc.composite().resize((93, 57), Image.Resampling.NEAREST)
        await pilot.press("ctrl+e")
        await pilot.pause()
        app.screen.query_one("#export-scale", Input).value = "3"
        target = tmp_path / f"{kind}-scaled.png"
        await pick(app, pilot, target)
        with Image.open(target) as restored:
            assert restored.size == (93, 57)
            assert restored.convert("RGBA").tobytes() == expected.tobytes()
        assert state(doc) == before

        # Hidden enlargement never changes text output or rejects its selection.
        await pilot.press("ctrl+e")
        await pilot.pause()
        app.screen.query_one("#export-scale", Input).value = "invalid"
        app.screen.query_one("#columns", Input).value = "7"
        text = tmp_path / f"{kind}-text.txt"
        await pick(app, pilot, text)
        assert all(len(line) == 7 for line in text.read_text(encoding="utf-8").splitlines())
        assert state(doc) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (80, 32), (100, 38)])
async def test_scale_and_export_buttons_fit_compact_browsers(tmp_path, size):
    explorer = FileExplorer("export", tmp_path, "painting.png", canvas_size=(31, 19))
    app = ExplorerApp(explorer)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for selector in ("#export-scale", "#file-submit", "#file-cancel", "#allow-lossy"):
            widget = explorer.query_one(selector)
            assert widget.region.width > 0
            assert widget.region.right <= size[0] and widget.region.bottom <= size[1]
        assert await pilot.click("#export-scale")
        assert explorer.query_one("#export-scale", Input).has_focus
