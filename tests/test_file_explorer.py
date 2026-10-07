from pathlib import Path

from PIL import Image
import pytest
from textual.app import App
from textual.widgets import Checkbox, DataTable, Input, Select, Static

from termatelier.config import RuntimeConfig
from termatelier.file_explorer import (FileChoice, FileExplorer, TEXT_PREVIEW_BYTES,
                                      file_preview, folder_name)
from termatelier.model import Document
from termatelier.storage import save_project


class ExplorerApp(App):
    def __init__(self, screen):
        super().__init__()
        self.explorer = screen
        self.result = "pending"

    def on_mount(self):
        self.push_screen(self.explorer, self.selected)

    def selected(self, result):
        self.result = result


def error_text(explorer):
    return str(explorer.query_one("#file-error", Static).content)


@pytest.mark.asyncio
async def test_navigation_history_search_hidden_and_sort(tmp_path):
    (tmp_path / "child").mkdir()
    Image.new("RGBA", (5, 3), "red").save(tmp_path / "zebra.png")
    Image.new("RGBA", (8, 4), "blue").save(tmp_path / ".hidden.png")
    (tmp_path / "notes.txt").write_text("literal [red] text")
    explorer = FileExplorer("open", tmp_path)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        assert [path.name for path in explorer.entries] == ["child", "zebra.png"]
        assert explorer.query_one("#path", Input).value == ""
        explorer.query_one("#file-type", Select).value = "all"
        await pilot.pause()
        assert "notes.txt" in [path.name for path in explorer.entries]
        explorer.query_one("#file-hidden", Checkbox).value = True
        await pilot.pause()
        assert ".hidden.png" in [path.name for path in explorer.entries]
        explorer.query_one("#file-search", Input).value = "ZEB"
        await pilot.pause()
        assert explorer.entries == [tmp_path / "zebra.png"]
        explorer.query_one("#file-search", Input).value = ""
        await pilot.pause()
        assert explorer.navigate(tmp_path / "child")
        assert explorer.current_directory == tmp_path / "child"
        explorer.action_back()
        assert explorer.current_directory == tmp_path
        explorer.action_forward()
        assert explorer.current_directory == tmp_path / "child"
        explorer.action_up()
        assert explorer.current_directory == tmp_path
        assert explorer.query_one("#file-forward").disabled
        await pilot.press("escape")
        assert app.result is None


@pytest.mark.asyncio
async def test_selection_preview_and_enter_return_path(tmp_path):
    image_path = tmp_path / "painting.png"
    original = Image.new("RGBA", (31, 19), (4, 90, 170, 123))
    original.save(image_path)
    before = image_path.read_bytes()
    explorer = FileExplorer("open", tmp_path)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        table = explorer.query_one("#file-table", DataTable)
        table.focus()
        await pilot.press("enter")
        assert explorer.query_one("#path", Input).value == "painting.png"
        assert "31 × 19" in str(explorer.query_one("#file-info", Static).content)
        preview = explorer.query_one("#file-preview", Static)
        assert "▀" in str(preview.content)
        assert max(len(row) for row in str(preview.content).splitlines()) <= preview.content_size.width
        await pilot.press("enter")
        assert app.result == FileChoice(image_path.resolve(), "open")
    assert image_path.read_bytes() == before
    with Image.open(image_path) as preserved:
        assert preserved.size == (31, 19) and preserved.getpixel((0, 0)) == (4, 90, 170, 123)


def test_bounded_literal_text_and_safe_binary_previews(tmp_path):
    source = tmp_path / "source.sparker"
    source.write_text("[red]do not style[/red]\x1b[31m\n" + "a" * 6000)
    info, preview = file_preview(source)
    assert "[red]do not style[/red]" in preview.plain
    assert "\\x1b[31m" in preview.plain and "\x1b" not in preview.plain
    assert len(preview.plain) < TEXT_PREVIEW_BYTES + 100
    assert "limited to 4 KiB" in preview.plain
    binary = tmp_path / "binary.dat"
    binary.write_bytes(b"\0do not decode")
    assert "Binary file" in file_preview(binary)[0].plain
    assert file_preview(binary)[1].plain == ""


def test_image_preview_fits_width_and_preserves_aspect(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGBA", (100, 50), "orange").save(source)
    _, preview = file_preview(source, max_width=20)
    rows = preview.plain.splitlines()
    assert all(len(row) == 20 for row in rows)
    assert len(rows) == 5  # Two source pixels vertically per terminal row.


def test_native_project_preview_and_invalid_project(tmp_path):
    project = tmp_path / "layers.tart"
    document = Document(16, 12)
    save_project(document, project)
    info, preview = file_preview(project)
    assert "16 × 12 pixels" in info.plain and "layers" in info.plain
    assert "▀" in preview.plain
    invalid = tmp_path / "invalid.tart"
    invalid.write_text("not a project")
    assert "Preview unavailable" in file_preview(invalid)[0].plain


@pytest.mark.asyncio
async def test_validator_normalizes_and_errors_remain_inside_browser(tmp_path):
    (tmp_path / "commands.sparker").write_text("info\n")
    def validator(choice):
        if choice.path.name != "commands.sparker":
            raise ValueError("Choose the reviewed script.")
        return FileChoice(choice.path, choice.operation, 80, choice.allow_lossy)
    (tmp_path / "other.sparker").write_text("info\n")
    explorer = FileExplorer("script", tmp_path, validator=validator)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#path", Input).value = "missing.sparker"
        explorer.submit()
        assert "does not exist" in error_text(explorer) and app.result == "pending"
        explorer.query_one("#path", Input).value = "other.sparker"
        explorer.submit()
        assert "reviewed script" in error_text(explorer) and app.result == "pending"
        explorer.query_one("#path", Input).value = "commands.sparker"
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(tmp_path / "commands.sparker", "script", 80)


@pytest.mark.asyncio
async def test_missing_default_export_does_not_create_and_preserves_destination(tmp_path):
    intended = tmp_path / "not-created" / "exports"
    config = RuntimeConfig(values={"export.directory": str(intended)})
    explorer = FileExplorer("export", config=config, default_name="painting.png")
    app = ExplorerApp(explorer)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        assert not intended.exists()
        assert explorer.current_directory == tmp_path
        assert explorer.query_one("#path", Input).value == str(intended / "painting.png")
        explorer.query_one("#path", Input).value = "renamed.png"
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(intended / "renamed.png", "export")
        assert not intended.exists()


@pytest.mark.asyncio
async def test_export_extension_columns_lossy_and_outside_project_guard(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "application"
    project.mkdir()
    explorer = FileExplorer("export", outside, project_dir=project)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        explorer.query_one("#path", Input).value = "painting.jpg"
        explorer.submit()
        assert "RGBA" in error_text(explorer) and app.result == "pending"
        explorer.query_one("#allow-lossy", Select).value = "yes"
        explorer.query_one("#path", Input).value = str(project / "blocked.png")
        explorer.submit()
        assert "project folder" in error_text(explorer) and app.result == "pending"
        explorer.query_one("#path", Input).value = "painting"
        explorer.query_one("#file-format", Select).value = ".webp"
        await pilot.pause()
        explorer.query_one("#columns", Input).value = "0"
        explorer.submit()
        assert "1..500" in error_text(explorer)
        explorer.query_one("#columns", Input).value = "120"
        explorer.query_one("#allow-lossy", Select).value = "no"
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(outside / "painting.webp", "export", 120, False)
        assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_new_folder_explicit_creation_validation_and_address(tmp_path):
    explorer = FileExplorer("save", tmp_path, default_name="editable.tart")
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        assert list(tmp_path.iterdir()) == []
        explorer.action_new_folder()
        await pilot.pause()
        field = app.screen.query_one("#folder-name", Input)
        field.value = "../escape"
        await pilot.press("enter")
        assert app.screen is not explorer and not (tmp_path.parent / "escape").exists()
        field.value = "Artwork"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is explorer and explorer.current_directory == tmp_path / "Artwork"
        assert (tmp_path / "Artwork").is_dir()
        explorer.create_folder("nested")
        with pytest.raises(FileExistsError):
            explorer.create_folder("nested")
        assert not explorer.navigate(tmp_path / "missing")
        assert "does not exist" in error_text(explorer)


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "CON", "lpt1.txt", "trail.", "x\x00"])
def test_folder_name_rejects_traversal_and_reserved_names(name):
    with pytest.raises(ValueError):
        folder_name(name)


@pytest.mark.asyncio
async def test_hub_switch_operations_and_permission_error(tmp_path, monkeypatch):
    explorer = FileExplorer("open", tmp_path, allow_operations=True)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        explorer.query_one("#file-operation", Select).value = "save"
        await pilot.pause()
        assert explorer.operation == "save"
        explorer.query_one("#path", Input).value = "editable"
        explorer.query_one("#file-operation", Select).value = "export"
        await pilot.pause()
        assert explorer.operation == "export" and explorer.query_one("#path", Input).value.endswith("editable.png")
        original = __import__("os").scandir
        def denied(path):
            if Path(path) == tmp_path:
                raise PermissionError("Access denied in fixture")
            return original(path)
        monkeypatch.setattr("termatelier.file_explorer.os.scandir", denied)
        explorer.refresh_files()
        assert "Access denied" in error_text(explorer) and app.result == "pending"


@pytest.mark.asyncio
async def test_directory_picker_current_folder_and_project_guard(tmp_path):
    project = tmp_path / "application"
    project.mkdir()
    explorer = FileExplorer("directory", tmp_path, project_dir=project)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        assert explorer.entries == [project]
        explorer.query_one("#path", Input).value = str(project)
        explorer.submit()
        assert "project folder" in error_text(explorer)
        explorer.query_one("#path", Input).value = ""
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(tmp_path, "directory")


@pytest.mark.asyncio
async def test_relaxed_lossless_preference_and_specific_type_alias_filter(tmp_path):
    Image.new("RGB", (3, 2), "red").save(tmp_path / "source.jpeg")
    Image.new("RGBA", (3, 2), "green").save(tmp_path / "source.png")
    config = RuntimeConfig(values={"export.lossless": False, "export.directory": str(tmp_path)})
    explorer = FileExplorer("export", tmp_path, config=config)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        explorer.query_one("#file-type", Select).value = ".png"
        await pilot.pause()
        assert explorer.entries == [tmp_path / "source.png"]
        explorer.query_one("#file-type", Select).value = ".jpg"
        await pilot.pause()
        assert explorer.entries == [tmp_path / "source.jpeg"]
        explorer.query_one("#path", Input).value = "confirmed.jpg"
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(tmp_path / "confirmed.jpg", "export", allow_lossy=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(90, 30), (120, 40)])
async def test_hub_controls_reachable_in_compact_terminal(tmp_path, size):
    explorer = FileExplorer("open", tmp_path, allow_operations=True)
    app = ExplorerApp(explorer)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for selector in ("#path", "#file-submit", "#file-search", "#file-operation", "#file-type", "#file-sort", "#file-hidden"):
            widget = explorer.query_one(selector)
            assert widget.region.width > 0
            assert widget.region.right <= size[0], selector
            assert widget.region.bottom <= size[1], selector
        assert explorer.query_one("#file-search", Input).region.width >= 12
        await pilot.click("#file-search")
        assert explorer.query_one("#file-search", Input).has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("operation, filename", [("open", "painting.png"), ("import", "painting.png"),
    ("save", "editable.tart"), ("script", "steps.sparker"), ("font", "font.ttf"), ("debug", "debug.json")])
async def test_invalid_export_options_do_not_block_other_operations(tmp_path, operation, filename):
    Image.new("RGBA", (7, 5), "orange").save(tmp_path / "painting.png")
    (tmp_path / "steps.sparker").write_text("info\n")
    (tmp_path / "font.ttf").write_bytes(b"font decoding belongs to the caller")
    config = RuntimeConfig(values={"export.directory": str(tmp_path)})
    explorer = FileExplorer("export", tmp_path, "painting.png", config=config, allow_operations=True)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        explorer.query_one("#columns", Input).value = "invalid"
        explorer.query_one("#allow-lossy", Select).value = "yes"
        explorer.submit()
        assert "Text columns" in error_text(explorer) and app.result == "pending"
        explorer.query_one("#file-operation", Select).value = operation
        await pilot.pause()
        explorer.query_one("#path", Input).value = str(tmp_path / filename)
        await pilot.pause()
        explorer.submit()
        await pilot.pause()
        assert app.result == FileChoice(tmp_path / filename, operation, 100, False)


@pytest.mark.asyncio
async def test_typed_and_selected_export_formats_sync_without_alias_rewrite(tmp_path):
    Image.new("RGB", (8, 6), "orange").save(tmp_path / "selected.jpeg")
    Image.new("RGBA", (8, 6), "blue").save(tmp_path / "selected.tif")
    explorer = FileExplorer("export", tmp_path, "initial.webp", allow_operations=True)
    app = ExplorerApp(explorer)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        field = explorer.query_one("#path", Input)
        selector = explorer.query_one("#file-format", Select)
        assert field.value == "initial.webp" and selector.value == ".webp"
        for filename, selected_format in [("typed.jpeg", ".jpg"), ("typed.tif", ".tiff"),
                ("typed.webp", ".webp"), ("typed.PNG", ".png"), ("typed.ansi", ".ansi")]:
            field.value = filename
            await pilot.pause()
            assert selector.value == selected_format
            assert field.value == filename
        for filename, selected_format in [("selected.jpeg", ".jpg"), ("selected.tif", ".tiff")]:
            explorer.select_file(tmp_path / filename)
            await pilot.pause()
            assert selector.value == selected_format
            assert field.value == filename
        selector.value = ".webp"
        await pilot.pause()
        assert field.value == "selected.webp"
        assert selector.value == ".webp"
        # Rapid edits leave the latest input authoritative over queued events.
        field.value = "rapid.png"
        field.value = "rapid.jpeg"
        field.value = "rapid.tif"
        await pilot.pause()
        assert field.value == "rapid.tif" and selector.value == ".tiff"
