"""Integer export enlargement repeats every original pixel without editing it."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from termatelier.commands import CommandError, CommandSession
from termatelier.config import RuntimeConfig
from termatelier.model import Document, Layer
from termatelier import storage
from termatelier.__main__ import main


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setenv("SPARKER_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("SPARKER_PROJECT_ROOT", str(tmp_path / "checkout"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    monkeypatch.delenv("SPARKER_DEBUG_STATE", raising=False)
    return RuntimeConfig.load()


def pixel_source():
    image = Image.new("RGBA", (3, 2))
    image.putdata([(255, 0, 0, 255), (10, 20, 30, 128), (37, 43, 51, 0),
                   (0, 255, 0, 17), (0, 0, 255, 240), (181, 97, 52, 1)])
    return image


def raw_document(image):
    return SimpleNamespace(size=image.size, width=image.width, height=image.height,
                           composite=lambda: image.copy(),
                           export_view=SimpleNamespace(zoom=.01, resampling="bicubic"))


def rendered_state(doc):
    def snapshot(state):
        size, layers, active, selection, metadata, settings, revision = state
        return (size, [(layer.name, layer.image.tobytes(), layer.visible, layer.locked,
                        layer.opacity, layer.blend, layer.mask.tobytes() if layer.mask else None)
                       for layer in layers], active, selection.tobytes() if selection else None,
                copy.deepcopy(metadata), copy.deepcopy(settings), revision)

    return (snapshot(doc.snapshot()), doc.serial, doc.saved_revision,
            [(label, snapshot(state)) for label, state in doc.undo_stack],
            [(label, snapshot(state)) for label, state in doc.redo_stack],
            copy.deepcopy(doc.export_view),
            (doc.clipboard[0].tobytes(), doc.clipboard[1]) if doc.clipboard else None)


@pytest.mark.parametrize("extension", [".png", ".webp", ".tif", ".tiff"])
@pytest.mark.parametrize("scale", [1, 2, 5, 16])
def test_lossless_scaled_exports_repeat_exact_rgba_blocks(config, tmp_path, extension, scale):
    image = pixel_source()
    doc = raw_document(image)
    expected = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    path = storage.export_image(doc, tmp_path / ("pixel detail" + extension), scale=scale, config=config)
    with Image.open(path) as restored:
        result = restored.convert("RGBA")
        assert result.size == (3 * scale, 2 * scale)
        assert result.tobytes() == expected.tobytes()
        for y in range(image.height):
            for x in range(image.width):
                block = result.crop((x * scale, y * scale, (x + 1) * scale, (y + 1) * scale))
                assert block.tobytes() == bytes(image.getpixel((x, y))) * (scale * scale)
    assert doc.size == (3, 2)
    assert doc.composite().tobytes() == image.tobytes()


def test_scaled_export_keeps_canvas_layers_selection_history_clipboard_and_view(config, tmp_path):
    doc = Document(7, 5)
    doc.layers = [Layer("Base", Image.new("RGBA", doc.size, (30, 90, 160, 220)))]
    doc.active = 0
    doc.selection = Image.new("L", doc.size, 128)
    doc.layer.mask = Image.new("L", doc.size, 190)
    with doc.edit("Detail"):
        doc.add_layer("Fine pixels", Image.new("RGBA", doc.size))
        doc.layer.image.putpixel((3, 2), (220, 150, 20, 110))
        doc.layer.opacity = .65
        doc.layer.blend = "multiply"
    with doc.edit("Temporary change"):
        doc.layer.image.putpixel((4, 1), (10, 20, 30, 40))
    doc.undo()
    doc.metadata.update(guides_x=[2], guides_y=[1], grid_spacing=2)
    doc.export_view = {"zoom": .025, "pan": [3, -2], "resampling": "bicubic"}
    doc.clipboard = (pixel_source(), (1, 1))
    before = rendered_state(doc)
    expected = doc.composite().resize((28, 20), Image.Resampling.NEAREST)
    path = storage.export(doc, tmp_path / "full details.png", scale=4, config=config)
    with Image.open(path) as restored:
        assert restored.convert("RGBA").tobytes() == expected.tobytes()
    assert rendered_state(doc) == before
    assert doc.size == (7, 5)


def test_default_export_is_native_size_and_persistent_scale_can_be_overridden(config, tmp_path):
    image = pixel_source()
    doc = raw_document(image)
    path = storage.export(doc, tmp_path / "native.png", config=config)
    with Image.open(path) as restored:
        assert restored.size == image.size
        assert restored.convert("RGBA").tobytes() == image.tobytes()
    config.set("export.scale", 3)
    loaded = RuntimeConfig.load()
    assert loaded.get("export.scale") == 3
    path = storage.export(doc, tmp_path / "configured.png", config=loaded)
    with Image.open(path) as restored:
        assert restored.size == (9, 6)
    path = storage.export(doc, tmp_path / "explicit.png", scale=2, config=loaded)
    with Image.open(path) as restored:
        assert restored.size == (6, 4)
    assert loaded.get("export.scale") == 3


@pytest.mark.parametrize("scale", [True, False, 0, -1, 17, 1.5, "2", float("nan"), float("inf")])
def test_invalid_scale_rejected_before_pixels_resize_directories_or_atomic_write(config, tmp_path, monkeypatch, scale):
    target = tmp_path / "existing.png"
    target.write_bytes(b"previous file must survive")

    def forbidden(*args, **kwargs):
        raise AssertionError("Validation must happen before rendering or writing.")

    doc = SimpleNamespace(size=(3, 2), width=3, height=2, composite=forbidden)
    monkeypatch.setattr(Image.Image, "resize", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(storage, "atomic_write", forbidden)
    with pytest.raises(ValueError, match="[Ss]cale|integer|1.*16"):
        storage.export(doc, target, scale=scale, config=config)
    assert target.read_bytes() == b"previous file must survive"


@pytest.mark.parametrize("size,scale,extension", [
    ((2048, 2048), 3, ".png"),
    ((4096, 1), 5, ".tiff"),
    ((4096, 256), 4, ".webp"),
])
def test_oversized_output_rejected_before_rendering_or_creating_destination(config, tmp_path, monkeypatch, size, scale, extension):
    target = tmp_path / "not-created" / ("oversized" + extension)

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized output must be rejected before rendering or writing.")

    doc = SimpleNamespace(size=size, width=size[0], height=size[1], composite=forbidden)
    monkeypatch.setattr(Image.Image, "resize", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(storage, "atomic_write", forbidden)
    with pytest.raises(ValueError, match="pixels|1638|[Ll]imit|[Ll]arge|[Ss]ize|[Dd]imension"):
        storage.export_image(doc, target, scale=scale, config=config)
    assert not target.parent.exists()


def test_output_dimension_limits_are_format_aware_without_allocating_pixels(config):
    assert storage.export_dimensions((3, 2)) == (3, 2)
    assert storage.export_dimensions((2048, 2048), 2, extension=".png") == (4096, 4096)
    assert storage.export_dimensions((4096, 256), 4, extension=".png") == (16384, 1024)
    assert storage.export_dimensions((4096, 256), 4, extension=".tiff") == (16384, 1024)
    with pytest.raises(ValueError):
        storage.export_dimensions((4096, 256), 4, extension=".webp")


@pytest.mark.parametrize("size,scale,extension", [
    ((2048, 2048), 3, ".png"), ((4096, 1), 5, ".tiff"), ((4096, 256), 4, ".webp"),
])
def test_oversized_export_preserves_existing_file(config, tmp_path, monkeypatch, size, scale, extension):
    target = tmp_path / ("existing" + extension)
    target.write_bytes(b"existing artwork")

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized output must fail before pixel work or filesystem changes.")

    doc = SimpleNamespace(size=size, composite=forbidden)
    monkeypatch.setattr(Image.Image, "resize", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(storage, "atomic_write", forbidden)
    with pytest.raises(ValueError):
        storage.export(doc, target, scale=scale, config=config)
    assert target.read_bytes() == b"existing artwork"


def test_native_scale_does_not_resample_any_pixels(config, tmp_path, monkeypatch):
    image = pixel_source()

    def forbidden(*args, **kwargs):
        raise AssertionError("Native scale must use the original composite pixels directly.")

    monkeypatch.setattr(Image.Image, "resize", forbidden)
    path = storage.export_image(raw_document(image), tmp_path / "native.png", scale=1, config=config)
    with Image.open(path) as restored:
        assert restored.convert("RGBA").tobytes() == image.tobytes()


@pytest.mark.parametrize("extension", [".jpg", ".jpeg", ".bmp", ".gif"])
def test_scaled_lossy_opt_in_encoders_use_enlarged_dimensions(config, tmp_path, extension):
    path = storage.export_image(raw_document(pixel_source()), tmp_path / ("opt-in" + extension),
                                scale=4, allow_lossy=True, config=config)
    with Image.open(path) as restored:
        assert restored.size == (12, 8)
        restored.load()


@pytest.mark.parametrize("extension", [".txt", ".ansi"])
def test_explicit_text_export_enlargement_rejected_before_rendering_or_disk(config, tmp_path, monkeypatch, extension):
    target = tmp_path / ("text" + extension)
    target.write_bytes(b"previous text")

    def forbidden(*args, **kwargs):
        raise AssertionError("Image-only option must be rejected before rendering text or writing.")

    doc = SimpleNamespace(size=(3, 2), composite=forbidden)
    monkeypatch.setattr(storage, "atomic_write", forbidden)
    with pytest.raises(ValueError, match="[Ii]mage"):
        storage.export(doc, target, scale=2, config=config)
    assert target.read_bytes() == b"previous text"


@pytest.mark.parametrize("extension", [".txt", ".ansi"])
def test_text_exports_ignore_persistent_image_scale(config, tmp_path, extension):
    doc = raw_document(pixel_source())
    config.set("export.scale", 5)
    path = storage.export(doc, tmp_path / ("text" + extension), columns=3, config=config)
    baseline = storage.export(doc, tmp_path / ("baseline" + extension), columns=3, scale=1, config=config)
    assert path.read_bytes() == baseline.read_bytes()
    assert doc.size == (3, 2)


def test_command_export_scale_reports_output_dimensions_and_preserves_canvas(config, tmp_path):
    doc = Document(7, 5)
    doc.layers = [Layer("Pixels", Image.new("RGBA", doc.size, (20, 30, 40, 128)))]
    doc.active = 0
    session = CommandSession(doc, base_dir=tmp_path, config=config)
    before = rendered_state(doc)
    result = session.execute('export "cli enlarged.png" --scale 3')
    target = tmp_path / "exports" / "cli enlarged.png"
    assert str(target) in result.text
    assert "21×15" in result.text or "21x15" in result.text
    with Image.open(target) as exported:
        assert exported.size == (21, 15)
        assert exported.convert("RGBA").tobytes() == doc.composite().resize((21, 15), Image.Resampling.NEAREST).tobytes()
    assert rendered_state(doc) == before
    assert session.document.size == (7, 5)
    with pytest.raises(CommandError):
        session.execute("export invalid.png --scale 2.5")
    assert not (tmp_path / "exports" / "invalid.png").exists()


def test_batch_export_scale_enlarges_only_output_and_inspect_retains_canvas(config, tmp_path, capsys):
    target = tmp_path / "batch.png"
    code = main(["--new", "5x3", "-c", "new 5x3 --transparent",
                 "-c", "pencil 2,1 --color red --size 1", "--export", str(target),
                 "--export-scale", "4", "--inspect"])
    assert code == 0
    output = capsys.readouterr().out
    assert "20×12" in output or "20x12" in output
    inspect = json.loads(output[output.index("{\n"):])
    assert inspect["size"] == [5, 3]
    with Image.open(target) as exported:
        assert exported.size == (20, 12)
        result = exported.convert("RGBA")
        assert result.crop((8, 4, 12, 8)).tobytes() == bytes((255, 0, 0, 255)) * 16
        assert result.getpixel((0, 0)) == (0, 0, 0, 0)


def test_batch_invalid_export_scale_preserves_existing_file(config, tmp_path, capsys):
    target = tmp_path / "keep.png"
    target.write_bytes(b"original")
    assert main(["--new", "5x3", "--export", str(target), "--export-scale", "17"]) == 2
    assert "scale" in capsys.readouterr().err.lower()
    assert target.read_bytes() == b"original"
