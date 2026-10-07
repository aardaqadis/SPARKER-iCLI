"""Real encoders, destination policy and display/export separation."""
import random
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from termatelier.model import Document, Layer
from termatelier.storage import (
    default_export_directory, export, export_image, export_text, import_document,
    load_project, resolve_export_path, save_project, validate_export_directory,
)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("SPARKER_EXPORT_DIR", str(tmp_path / "external" / "exports"))
    monkeypatch.setenv("SPARKER_PROJECT_ROOT", str(tmp_path / "checkout"))


def patterned_document(width=31, height=19):
    rng = random.Random(8533)
    image = Image.new("RGBA", (width, height))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) + ((index * 37) % 256,)
                   for index in range(width * height)])
    doc = Document(width, height)
    doc.layers = [Layer("Pixels", image)]
    doc.active = 0
    return doc


@pytest.mark.parametrize("extension", [".png", ".webp", ".tif", ".tiff"])
def test_lossless_encoders_preserve_exact_rgba_and_odd_dimensions(tmp_path, extension):
    doc = patterned_document()
    # Also check invisible RGB values. They are real image data even when alpha
    # is zero, and WebP's default encoder normally discards them.
    source = doc.layer.image.copy()
    source.putpixel((0, 0), (197, 43, 81, 0))
    raw_document = SimpleNamespace(size=source.size, composite=lambda: source)
    result = export_image(raw_document, tmp_path / ("pixels" + extension))
    with Image.open(result) as restored:
        assert restored.size == (31, 19)
        assert restored.convert("RGBA").tobytes() == source.tobytes()
    assert source.tobytes() == raw_document.composite().tobytes()


@pytest.mark.parametrize("extension", [".png", ".webp", ".tiff"])
def test_flattened_export_preserves_original_document_dimensions(tmp_path, extension):
    doc = patterned_document()
    before = doc.composite().tobytes()
    result = export(doc, tmp_path / ("canvas" + extension))
    restored = import_document(result)
    assert restored.size == (31, 19)
    assert restored.layer.image.tobytes() == before
    assert doc.size == (31, 19) and doc.composite().tobytes() == before


@pytest.mark.parametrize("extension", [".jpg", ".jpeg", ".gif", ".bmp"])
def test_lossy_formats_require_explicit_opt_in(tmp_path, extension):
    target = tmp_path / ("explicit" + extension)
    with pytest.raises(ValueError, match="explicitly allow lossy"):
        export(patterned_document(), target)
    assert not target.exists()
    assert export(patterned_document(), target, allow_lossy=True) == target
    with Image.open(target) as restored:
        assert restored.size == (31, 19)
        restored.load()


def test_default_export_folder_is_lazy_and_relative_names_are_external(tmp_path):
    directory = tmp_path / "external" / "exports"
    assert default_export_directory() == directory
    assert resolve_export_path("picture.png") == directory / "picture.png"
    assert not directory.exists()
    result = export(patterned_document(), "picture.png")
    assert result == directory / "picture.png" and result.is_file()
    assert not (Path.cwd() / "picture.png").exists()


def test_nested_relative_export_names_are_created_outside_the_project(tmp_path):
    result = export(patterned_document(), "collection/drawing.png")
    assert result == tmp_path / "external" / "exports" / "collection" / "drawing.png"
    assert result.is_file()


def test_explicit_absolute_external_destination_is_respected(tmp_path):
    target = tmp_path / "chosen" / "folder" / "drawing.png"
    assert export(patterned_document(), target) == target
    assert target.is_file()
    assert not (tmp_path / "external" / "exports").exists()


@pytest.mark.parametrize("extension", [".png", ".ansi", ".txt"])
def test_project_folder_exports_are_rejected_before_writing(tmp_path, extension):
    target = tmp_path / "checkout" / "work" / ("drawing" + extension)
    with pytest.raises(ValueError, match="program/project folder"):
        export(patterned_document(), target)
    assert not target.parent.exists()


def test_explicit_relative_project_destination_is_rejected(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    with pytest.raises(ValueError, match="program/project folder"):
        export(patterned_document(), "./drawing.png")
    assert not (checkout / "drawing.png").exists()


def test_configured_project_export_directory_is_rejected(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout" / "exports"
    monkeypatch.setenv("SPARKER_EXPORT_DIR", str(checkout))
    with pytest.raises(ValueError, match="program/project folder"):
        export(patterned_document(), "drawing.png")
    assert not checkout.exists()


def test_symlink_into_the_project_is_rejected(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    alias = tmp_path / "external-link"
    try:
        alias.symlink_to(checkout, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable on this host.")
    with pytest.raises(ValueError, match="program/project folder"):
        export(patterned_document(), alias / "drawing.png")
    assert not (checkout / "drawing.png").exists()


@pytest.mark.parametrize("ansi", [False, True])
def test_direct_text_export_respects_external_folder_policy(tmp_path, ansi):
    name = "drawing.ansi" if ansi else "drawing.txt"
    result = export_text(patterned_document(), name, columns=17, ansi=ansi)
    assert result == tmp_path / "external" / "exports" / name
    assert result.read_text(encoding="utf-8").endswith("\n")


def test_native_project_can_still_be_explicitly_saved_with_editable_pixels(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    target = checkout / "editable.tart"
    doc = patterned_document()
    save_project(doc, target)
    restored = load_project(target)
    assert restored.size == doc.size
    assert restored.layer.image.tobytes() == doc.layer.image.tobytes()


@pytest.mark.parametrize("mode", ["nearest", "bilinear", "bicubic"])
@pytest.mark.asyncio
async def test_view_sampling_zoom_and_guides_do_not_enter_export(tmp_path, mode):
    from termatelier.app import Studio
    from termatelier.canvas import Canvas

    doc = patterned_document()
    original = doc.composite().tobytes()
    app = Studio(doc)
    async with app.run_test(size=(90, 30)):
        canvas = app.query_one(Canvas)
        canvas.set_resampling(mode)
        if hasattr(app, "config"):
            app.config.set("view.resampling", mode, persist=False)
        canvas.zoom = .73
        canvas.pan_x, canvas.pan_y = 2.4, -1.7
        canvas.grid = True
        doc.metadata.update(guides_x=[4], guides_y=[8])
        doc.select("rectangle", (1, 1), (8, 8))
        canvas.screen_image()
        result = export(doc, tmp_path / (mode + ".png"))
    with Image.open(result) as restored:
        assert restored.size == (31, 19)
        assert restored.tobytes() == original
    assert doc.layer.image.size == (31, 19)


@pytest.mark.asyncio
async def test_preview_zoom_limits_follow_runtime_preferences():
    from termatelier.app import Studio
    from termatelier.canvas import Canvas
    from termatelier.config import RuntimeConfig

    app = Studio(patterned_document())
    app.config = RuntimeConfig.load()
    app.config.set("view.zoom_min", .1, persist=False)
    app.config.set("view.zoom_max", 2, persist=False)
    async with app.run_test(size=(90, 30)):
        canvas = app.query_one(Canvas)
        canvas.zoom = 1
        canvas.zoom_at(100)
        assert canvas.zoom == 2
        canvas.zoom_at(.001)
        assert canvas.zoom == .1


def test_unsupported_view_sampling_does_not_change_the_view():
    from termatelier.canvas import Canvas

    canvas = Canvas()
    with pytest.raises(ValueError, match="View resampling"):
        canvas.set_resampling("invalid")
    assert canvas.resampling == "nearest"
