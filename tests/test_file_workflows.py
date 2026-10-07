"""Exercise painter file operations through the shared browser and real key events."""
import copy
import json
from pathlib import Path

from PIL import Image
import pytest
from textual.widgets import Input, Select, Static

from termatelier.app import Studio
from termatelier.dialogs import Confirm, Form
from termatelier.file_explorer import FileExplorer
from termatelier.model import Document, Layer
from termatelier.storage import load_project


def patterned_document():
    document = Document(31, 19)
    image = Image.new("RGBA", document.size)
    image.putdata([((x * 17 + y * 3) % 256, (y * 19 + x) % 256,
                    (x * 7 + y * 11) % 256, (x * 13 + y * 23) % 256)
                   for y in range(document.height) for x in range(document.width)])
    document.layers = [Layer("Original pixels", image)]
    document.active = 0
    document.metadata["title"] = "Odd-sized alpha study"
    return document


async def choose_path(app, pilot, path):
    assert isinstance(app.screen, FileExplorer)
    field = app.screen.query_one("#path", Input)
    field.value = str(path)
    field.focus()
    await pilot.press("enter")
    await pilot.pause()


def document_state(document):
    return (document.size,
            [(layer.name, layer.image.tobytes(), layer.opacity, layer.blend,
              layer.visible, layer.locked, layer.mask.tobytes() if layer.mask else None)
             for layer in document.layers],
            document.active, document.selection.tobytes() if document.selection else None,
            copy.deepcopy(document.metadata), copy.deepcopy(document.settings),
            document.revision, document.saved_revision, len(document.undo_stack), len(document.redo_stack))


@pytest.mark.asyncio
async def test_f6_hub_and_modal_shortcuts_preserve_painting(tmp_path):
    app = Studio(patterned_document())
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        with app.doc.edit("Existing edit"):
            app.doc.layer.image.putpixel((2, 3), (240, 30, 90, 255))
        before, tool = document_state(app.doc), app.tool
        await pilot.press("f6")
        await pilot.pause()
        browser = app.screen
        assert isinstance(browser, FileExplorer) and browser.allow_operations
        assert browser.query_one("#file-operation", Select).value == "open"
        assert browser.navigate(tmp_path)
        browser.query_one("#file-search", Input).focus()
        await pilot.press("b", "e", "f", "ctrl+z", "ctrl+y", "ctrl+s", "ctrl+o", "ctrl+e", "delete")
        await pilot.pause()
        assert app.screen is browser
        assert document_state(app.doc) == before and app.tool == tool
        browser.query_one("#file-operation", Select).value = "save"
        await pilot.pause()
        assert browser.operation == "save"
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert len(app.screen_stack) == 1 and document_state(app.doc) == before


@pytest.mark.asyncio
async def test_first_save_open_and_import_undo_use_browser(tmp_path):
    document = patterned_document()
    document.selection = Image.new("L", document.size, 180)
    document.layer.mask = Image.new("L", document.size, 230)
    app = Studio(document)
    project = tmp_path / "editable study.tart"
    source = tmp_path / "import source.png"
    Image.new("RGBA", (7, 5), (17, 80, 240, 150)).save(source)
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FileExplorer) and app.screen.operation == "save"
        await choose_path(app, pilot, project)
        assert app.project_path == project and not app.doc.dirty
        restored = load_project(project)
        assert restored.layer.image.tobytes() == document.layer.image.tobytes()
        assert restored.layer.mask.tobytes() == document.layer.mask.tobytes()
        assert restored.selection.tobytes() == document.selection.tobytes()
        assert restored.metadata == document.metadata
        saved_pixels = app.doc.composite().tobytes()
        app.action_import()
        await pilot.pause()
        assert app.screen.operation == "import"
        await choose_path(app, pilot, source)
        assert len(app.doc.layers) == 2 and app.doc.layer.name == source.stem
        assert app.doc.layer.image.getpixel((6, 4)) == (17, 80, 240, 150)
        assert app.doc.layer.image.getpixel((7, 4)) == (0, 0, 0, 0)
        assert app.doc.dirty and len(app.doc.undo_stack) == 1
        await pilot.press("ctrl+z")
        assert len(app.doc.layers) == 1 and app.doc.composite().tobytes() == saved_pixels
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.screen.operation == "open"
        await choose_path(app, pilot, project)
        assert app.project_path == project and app.doc.composite().tobytes() == saved_pixels
        assert app.doc.layer.mask.tobytes() == restored.layer.mask.tobytes()
        assert len(app.doc.undo_stack) == 0


@pytest.mark.asyncio
async def test_browser_png_is_pixel_exact_and_text_columns_only_affect_text(tmp_path):
    app = Studio(patterned_document())
    png, text = tmp_path / "lossless.png", tmp_path / "text drawing.txt"
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        expected = app.doc.composite().tobytes()
        app.canvas.zoom = .63
        app.canvas.grid = True
        app.doc.metadata.update(guides_x=[4], guides_y=[6])
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert app.screen.operation == "export"
        app.screen.query_one("#columns", Input).value = "11"
        await choose_path(app, pilot, png)
        with Image.open(png) as exported:
            assert exported.mode == "RGBA" and exported.size == (31, 19)
            assert exported.tobytes() == expected
        await pilot.press("ctrl+e")
        await pilot.pause()
        app.screen.query_one("#columns", Input).value = "11"
        await choose_path(app, pilot, text)
        lines = text.read_text(encoding="utf-8").splitlines()
        assert len(lines) == round(19 * 11 / 31 / 2)
        assert all(len(line) == 11 for line in lines)
        assert app.doc.size == (31, 19) and app.doc.composite().tobytes() == expected


@pytest.mark.parametrize("operation,suffix", [("export", ".png"), ("save", ".tart"), ("debug", ".json")])
@pytest.mark.asyncio
async def test_browser_overwrite_cancel_preserves_file_then_confirm_writes(tmp_path, operation, suffix):
    app = Studio(patterned_document())
    target = tmp_path / ("existing" + suffix)
    previous = b"An existing file must survive cancellation."
    target.write_bytes(previous)
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        launch = {"export": app.action_export, "save": app.action_save_as, "debug": app.action_export_debug}[operation]
        launch()
        await pilot.pause()
        await choose_path(app, pilot, target)
        assert isinstance(app.screen, Confirm) and target.read_bytes() == previous
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert target.read_bytes() == previous and app.project_path is None
        launch()
        await pilot.pause()
        await choose_path(app, pilot, target)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert target.read_bytes() != previous
        if operation == "save":
            assert load_project(target).layer.image.tobytes() == app.doc.layer.image.tobytes()
            assert app.project_path == target
        elif operation == "export":
            with Image.open(target) as exported:
                assert exported.size == (31, 19) and exported.tobytes() == app.doc.composite().tobytes()
        else:
            report = json.loads(target.read_text(encoding="utf-8"))
            assert report["document"]["width"] == 31 and report["program"]["name"] == "SPARKER iCLI"


@pytest.mark.asyncio
async def test_unsaved_open_cancel_preserves_identity_pixels_and_history(tmp_path):
    original = patterned_document()
    replacement = tmp_path / "replacement.png"
    Image.new("RGBA", (9, 7), "blue").save(replacement)
    app = Studio(original)
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        with original.edit("Unsaved dot"):
            original.layer.image.putpixel((3, 2), (255, 0, 0, 255))
        before = document_state(original)
        await pilot.press("ctrl+o")
        await pilot.pause()
        await choose_path(app, pilot, replacement)
        assert isinstance(app.screen, Confirm) and app.doc is original
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert app.doc is original and document_state(original) == before
        await pilot.press("ctrl+o")
        await pilot.pause()
        await choose_path(app, pilot, replacement)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert app.doc is not original and app.doc.size == (9, 7)
        assert app.doc.layer.image.getpixel((0, 0)) == (0, 0, 255, 255)
        assert app.project_path is None


@pytest.mark.asyncio
async def test_script_picker_executes_and_atomic_failure_restores_editable_state(tmp_path):
    successful = tmp_path / "paint.sparker"
    successful.write_text("pencil 3,2 --color red --size 1\nmeta title Script-painted\n", encoding="utf-8")
    failing = tmp_path / "failed.sparker"
    failing.write_text("new 7x5\npencil 1,1 --color orange\ninvalid-command\n", encoding="utf-8")
    app = Studio(Document(31, 19))
    project = tmp_path / "script study.tart"
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        app.action_run_script()
        await pilot.pause()
        assert app.screen.operation == "script"
        await choose_path(app, pilot, successful)
        assert app.doc.layer.image.getpixel((3, 2)) == (255, 0, 0, 255)
        assert app.doc.metadata["title"] == "Script-painted" and app.doc.dirty
        app.save_to(project)
        await pilot.pause()
        app.store_tool_settings()
        original, before = app.doc, document_state(app.doc)
        app.action_run_script()
        await pilot.pause()
        await choose_path(app, pilot, failing)
        assert app.doc is original and document_state(app.doc) == before
        assert app.project_path == project and len(app.screen_stack) == 1
        assert load_project(project).layer.image.tobytes() == app.doc.layer.image.tobytes()


@pytest.mark.asyncio
async def test_export_folder_and_debug_picker_enforce_external_destination(tmp_path):
    app = Studio(patterned_document())
    chosen = tmp_path / "chosen exports"
    chosen.mkdir()
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        app.action_export_folder()
        await pilot.pause()
        assert app.screen.operation == "directory"
        await choose_path(app, pilot, Path.cwd())
        assert isinstance(app.screen, FileExplorer)
        assert "project" in str(app.screen.query_one("#file-error", Static).content).lower()
        await choose_path(app, pilot, chosen)
        assert app.config.get("export.directory") == str(chosen)
        settings = json.loads(app.config.path.read_text(encoding="utf-8"))
        assert settings["settings"]["export.directory"] == str(chosen)
        app.action_export_debug()
        await pilot.pause()
        browser = app.screen
        assert browser.operation == "debug" and browser.current_directory == chosen
        forbidden = Path.cwd() / "must-not-write.json"
        await choose_path(app, pilot, forbidden)
        assert app.screen is browser and not forbidden.exists()
        assert "project" in str(browser.query_one("#file-error", Static).content).lower()
        report_path = chosen / "runtime.json"
        await choose_path(app, pilot, report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["program"]["name"] == "SPARKER iCLI" and report["venv"]["path"]
        assert report["document"]["width"] == 31 and report["document"]["height"] == 19
        assert report["settings"]["export.directory"] == str(chosen)


@pytest.mark.asyncio
async def test_font_browse_cancel_preserves_open_text_form_and_typed_values(tmp_path):
    app = Studio(Document(72, 36))
    typed_font = str(tmp_path / "my unselected font.ttf")
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        before = document_state(app.doc)
        app.text_dialog((1, 2))
        await pilot.pause()
        form = app.screen
        assert isinstance(form, Form)
        form.query_one("#text", Input).value = "Keep this draft"
        form.query_one("#size", Input).value = "19"
        form.query_one("#font", Input).value = typed_font
        await pilot.click("#browse-font")
        await pilot.pause()
        assert isinstance(app.screen, FileExplorer) and app.screen.operation == "font"
        assert app.screen_stack[-2] is form and len(app.screen_stack) == 3
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is form and len(app.screen_stack) == 2
        assert form.query_one("#text", Input).value == "Keep this draft"
        assert form.query_one("#size", Input).value == "19"
        assert form.query_one("#font", Input).value == typed_font
        assert form.focused is form.query_one("#font", Input)
        assert document_state(app.doc) == before
        await pilot.click("#form-cancel")
        await pilot.pause()
        assert len(app.screen_stack) == 1 and document_state(app.doc) == before


@pytest.mark.asyncio
async def test_font_picker_choice_returns_to_form_and_paints_with_selected_font(tmp_path):
    font_path = next((candidate for candidate in [Path("C:/Windows/Fonts/arial.ttf"),
                     Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")] if candidate.is_file()), None)
    if font_path is None:
        pytest.skip("No system TrueType fixture is available.")
    app = Studio(Document(72, 36))
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        app.text_dialog((2, 2))
        await pilot.pause()
        form = app.screen
        form.query_one("#text", Input).value = "Art"
        await pilot.click("#browse-font")
        await pilot.pause()
        await choose_path(app, pilot, font_path)
        assert app.screen is form and form.query_one("#font", Input).value == str(font_path.resolve())
        await pilot.click("#form-apply")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.doc.layer.image.getchannel("A").getbbox() is not None
        assert app.doc.dirty and len(app.doc.undo_stack) == 1
        await pilot.press("ctrl+z")
        assert app.doc.layer.image.getchannel("A").getbbox() is None


@pytest.mark.parametrize("operation", ["open", "import", "save", "export", "script", "debug", "directory", "font"])
@pytest.mark.asyncio
async def test_file_hub_dispatches_selected_operation_to_real_workflow(tmp_path, operation):
    image = tmp_path / "hub source.png"
    Image.new("RGBA", (7, 5), (15, 50, 200, 170)).save(image)
    script = tmp_path / "hub paint.sparker"
    script.write_text("pencil 2,3 --color red --size 1\n", encoding="utf-8")
    folder = tmp_path / "hub exports"
    folder.mkdir()
    font = tmp_path / "hub font.ttf"
    font.write_bytes(b"Not loaded merely by choosing a font path.")
    targets = {"open": image, "import": image, "save": tmp_path / "hub.tart",
               "export": tmp_path / "hub.png", "script": script,
               "debug": tmp_path / "hub.json", "directory": folder, "font": font}
    app = Studio(patterned_document())
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        original, pixels = app.doc, app.doc.composite().tobytes()
        await pilot.press("f6")
        await pilot.pause()
        browser = app.screen
        browser.query_one("#file-operation", Select).value = operation
        await pilot.pause()
        assert browser.operation == operation
        await choose_path(app, pilot, targets[operation])
        if operation == "open":
            assert app.doc is not original and app.doc.size == (7, 5)
            assert app.doc.layer.image.getpixel((1, 1)) == (15, 50, 200, 170)
        elif operation == "import":
            assert app.doc is original and len(app.doc.layers) == 2
            assert app.doc.layer.image.getpixel((1, 1)) == (15, 50, 200, 170)
        elif operation == "save":
            assert app.project_path == targets[operation]
            assert load_project(targets[operation]).composite().tobytes() == pixels
        elif operation == "export":
            with Image.open(targets[operation]) as exported:
                assert exported.size == (31, 19) and exported.tobytes() == pixels
        elif operation == "script":
            assert app.doc.layer.image.getpixel((2, 3)) == (255, 0, 0, 255)
            assert app.doc.dirty and len(app.doc.undo_stack) == 1
        elif operation == "debug":
            report = json.loads(targets[operation].read_text(encoding="utf-8"))
            assert report["document"]["width"] == 31 and report["document"]["height"] == 19
        elif operation == "directory":
            assert app.config.get("export.directory") == str(folder)
        else:
            assert isinstance(app.screen, Form)
            assert app.screen.query_one("#font", Input).value == str(font)
            assert app.doc is original and app.doc.composite().tobytes() == pixels
            assert app.screen.query_one("#form-error", Static).content == ""
            await pilot.press("escape")
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_browser_requires_explicit_lossy_choice_and_keeps_image_dimensions(tmp_path):
    app = Studio(patterned_document())
    target = tmp_path / "explicit lossy.jpg"
    async with app.run_test(size=(140, 52)) as pilot:
        await pilot.pause()
        expected = app.doc.composite().tobytes()
        await pilot.press("ctrl+e")
        await pilot.pause()
        browser = app.screen
        await choose_path(app, pilot, target)
        assert app.screen is browser and not target.exists()
        assert "RGBA" in str(browser.query_one("#file-error", Static).content)
        browser.query_one("#allow-lossy", Select).value = "yes"
        await pilot.pause()
        await choose_path(app, pilot, target)
        with Image.open(target) as exported:
            assert exported.size == (31, 19) and exported.mode == "RGB"
        assert app.doc.composite().tobytes() == expected and app.doc.size == (31, 19)
