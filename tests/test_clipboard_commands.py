"""Actual CLI clipboard workflows preserve source detail, alpha and history."""
import json

from PIL import Image
import pytest

from termatelier.commands import CommandError, CommandSession
from termatelier.config import RuntimeConfig
from termatelier.model import Document, Layer


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    monkeypatch.delenv("SPARKER_DEBUG_STATE", raising=False)
    monkeypatch.delenv("SPARKER_EXPORT_DIR", raising=False)
    config = RuntimeConfig.load()
    config.set("export.directory", str(tmp_path))
    doc = Document(12, 10)
    doc.layers = [Layer("Pixels", Image.new("RGBA", doc.size))]
    doc.active = 0
    return CommandSession(doc, base_dir=tmp_path, config=config)


def state(doc):
    return (doc.size, doc.active, [(layer.name, layer.image.tobytes(), layer.opacity, layer.locked)
                                 for layer in doc.layers],
            doc.selection.tobytes() if doc.selection is not None else None,
            doc.revision, len(doc.undo_stack), len(doc.redo_stack))


def colored_clipboard(doc, origin=(2, 3)):
    image = Image.new("RGBA", (2, 2))
    image.putdata([(255, 0, 0, 255), (0, 255, 0, 128),
                   (0, 0, 255, 255), (37, 43, 51, 0)])
    doc.clipboard = (image, origin)
    return image


def test_copy_honors_layer_mask_opacity_and_feathered_selection(session):
    doc = session.document
    doc.layer.image = Image.new("RGBA", doc.size, (11, 22, 33, 200))
    doc.layer.mask = Image.new("L", doc.size, 128)
    doc.layer.opacity = .5
    doc.selection = Image.new("L", doc.size)
    doc.selection.paste(128, (3, 2, 6, 4))
    before = state(doc)
    session.execute("copy")
    image, origin = doc.clipboard
    assert origin == (3, 2)
    assert image.size == (3, 2)
    assert image.getpixel((0, 0)) == (11, 22, 33, 25)
    assert state(doc) == before


def test_copy_merged_matches_visible_composite_and_half_open_bounds(session):
    doc = session.document
    doc.layer.image.paste((150, 80, 30, 180), (0, 0, *doc.size))
    doc.add_layer("Visible", Image.new("RGBA", doc.size, (70, 120, 150, 120)))
    doc.layer.blend = "multiply"
    doc.layer.mask = Image.new("L", doc.size, 210)
    expected = doc.composite().crop((1, 2, 4, 5))
    session.execute("copy --merged --box 1,2,4,5")
    assert doc.clipboard[1] == (1, 2)
    assert doc.clipboard[0].size == (3, 3)
    assert doc.clipboard[0].tobytes() == expected.tobytes()


def test_cut_box_intersects_selection_and_is_undoable(session):
    doc = session.document
    doc.layer.image.paste("red", (0, 0, *doc.size))
    doc.selection = Image.new("L", doc.size)
    doc.selection.paste(255, (1, 1, 7, 6))
    original = doc.layer.image.tobytes()
    session.execute("cut --box 3,2,5,4")
    assert doc.clipboard[0].size == (2, 2)
    assert doc.clipboard[1] == (3, 2)
    assert doc.layer.image.getpixel((3, 2))[3] == 0
    assert doc.layer.image.getpixel((2, 2))[3] == 255
    assert doc.layer.image.getpixel((5, 2))[3] == 255
    assert len(doc.undo_stack) == 1
    session.execute("undo")
    assert doc.layer.image.tobytes() == original
    assert doc.clipboard[0].getpixel((0, 0)) == (255, 0, 0, 255)


def test_paste_coordinates_preserve_every_clipboard_rgba_pixel_and_origin(session):
    doc = session.document
    image = colored_clipboard(doc)
    original_clip = image.tobytes()
    session.execute('paste 5 4 --name "Detailed copy" --opacity 50%')
    assert len(doc.layers) == 2
    assert doc.layer.name == "Detailed copy"
    assert doc.layer.opacity == .5
    assert doc.layer.image.crop((5, 4, 7, 6)).tobytes() == original_clip
    assert doc.clipboard[0].tobytes() == original_clip
    assert doc.clipboard[1] == (2, 3)
    session.execute("undo")
    assert len(doc.layers) == 1
    session.execute("paste --in-place")
    assert doc.layer.image.crop((2, 3, 4, 5)).tobytes() == original_clip


def test_paste_into_uses_source_alpha_opacity_selection_without_dark_edges(session):
    doc = session.document
    doc.clipboard = (Image.new("RGBA", (1, 1), (255, 0, 0, 128)), (0, 0))
    doc.selection = Image.new("L", doc.size)
    doc.selection.putpixel((4, 3), 128)
    session.execute("paste --x 4 --y 3 --into --opacity 50%")
    assert len(doc.layers) == 1
    assert doc.layer.image.getpixel((4, 3)) == (255, 0, 0, 32)
    assert doc.layer.image.getpixel((4, 4)) == (0, 0, 0, 0)
    session.execute("undo")
    assert doc.layer.image.getchannel("A").getbbox() is None


def test_paste_transforms_are_nearest_and_do_not_change_clipboard(session):
    doc = session.document
    original = colored_clipboard(doc)
    expected = original.resize((4, 6), Image.Resampling.NEAREST).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    expected = expected.transpose(Image.Transpose.FLIP_TOP_BOTTOM).rotate(90, resample=Image.Resampling.NEAREST, expand=True)
    session.execute("paste --center --scale 4x6 --flip-h --flip-v --rotate 90")
    x, y = (doc.width - expected.width) // 2, (doc.height - expected.height) // 2
    assert doc.layer.image.crop((x, y, x + expected.width, y + expected.height)).tobytes() == expected.tobytes()
    assert doc.clipboard[0].tobytes() == original.tobytes()
    assert doc.clipboard[0].size == (2, 2)


def test_grid_paste_creates_one_layer_and_one_undo_step(session):
    doc = session.document
    doc.clipboard = (Image.new("RGBA", (2, 1), (10, 20, 30, 255)), (0, 0))
    session.execute("paste 1 2 --grid 3x2 --gap 1,2")
    assert len(doc.layers) == 2
    assert len(doc.undo_stack) == 1
    for x in (1, 4, 7):
        for y in (2, 5):
            assert doc.layer.image.getpixel((x, y)) == (10, 20, 30, 255)
            assert doc.layer.image.getpixel((x + 2, y))[3] == 0
    session.execute("undo")
    assert len(doc.layers) == 1


@pytest.mark.parametrize("extension", ["png", "webp", "tif", "tiff"])
def test_clipboard_lossless_file_roundtrip_without_changing_artwork(session, tmp_path, extension):
    doc = session.document
    original = colored_clipboard(doc)
    before = state(doc)
    session.execute(f"clipboard save clipboard.{extension}")
    with Image.open(tmp_path / f"clipboard.{extension}") as saved:
        assert saved.size == original.size
        assert saved.convert("RGBA").tobytes() == original.tobytes()
    session.execute("clipboard clear")
    assert doc.clipboard is None
    session.execute(f"clipboard load clipboard.{extension}")
    assert doc.clipboard[1] == (0, 0)
    assert doc.clipboard[0].tobytes() == original.tobytes()
    assert state(doc) == before


def test_clipboard_info_reports_real_dimensions_alpha_and_pixel_bytes(session):
    image = colored_clipboard(session.document)
    data = json.loads(session.execute("clipboard info --json").text)
    assert data == {"empty": False, "width": 2, "height": 2, "mode": "RGBA",
                    "origin": [2, 3], "pixel_bytes": 16, "alpha_range": [0, 255]}
    session.execute("clipboard clear")
    assert json.loads(session.execute("clipboard --json").text) == {"empty": True}


@pytest.mark.parametrize("line", [
    "copy --box 2,2,1,4", "copy --box 100,100,101,101", "cut --merged",
    "paste 1", "paste 1 2 --x 3", "paste --center --in-place", "paste 1 2 --center",
    "paste --into --name new", "paste --scale 0x10", "paste --rotate nan",
    "paste --opacity 101%", "paste --grid 257x1", "paste --grid 17x17",
    "paste --gap 1,2", "paste --grid 2x2 --gap 1", "paste --flip-z",
    "clipboard save copy.jpg", "clipboard load nonexistent.png", "clipboard clear --json",
])
def test_failed_commands_leave_art_clipboard_and_history_intact(session, line):
    doc = session.document
    image = colored_clipboard(doc)
    before = state(doc)
    clipboard = doc.clipboard
    with pytest.raises(CommandError):
        session.execute(line)
    assert state(doc) == before
    assert doc.clipboard is clipboard
    assert doc.clipboard[0].tobytes() == image.tobytes()


@pytest.mark.parametrize("line", ["cut", "paste --into"])
def test_locked_layer_rejects_pixel_changes_without_consuming_clipboard(session, line):
    doc = session.document
    colored_clipboard(doc)
    doc.layer.locked = True
    before = state(doc)
    clipboard = doc.clipboard
    with pytest.raises(CommandError, match="locked"):
        session.execute(line)
    assert state(doc) == before
    assert doc.clipboard is clipboard


def test_copy_and_new_layer_paste_remain_usable_above_locked_layer(session):
    doc = session.document
    doc.layer.image.putpixel((2, 3), (20, 30, 40, 255))
    doc.layer.locked = True
    session.execute("copy --box 2,3,3,4")
    session.execute("paste 5 6")
    assert len(doc.layers) == 2
    assert not doc.layer.locked
    assert doc.layer.image.getpixel((5, 6)) == (20, 30, 40, 255)


def test_cut_failure_restores_previous_clipboard_as_well_as_document(session, monkeypatch):
    doc = session.document
    colored_clipboard(doc)
    previous = doc.clipboard
    before = state(doc)
    copy_selection = doc.copy_selection

    def failed_cut(*args, **kwargs):
        copy_selection(*args, **kwargs)
        raise RuntimeError("simulated failure after cut")

    monkeypatch.setattr(doc, "copy_selection", failed_cut)
    with pytest.raises(CommandError, match="simulated failure"):
        session.execute("cut")
    assert state(doc) == before
    assert doc.clipboard is previous


def test_clipboard_file_save_cannot_write_to_program_folder(session, monkeypatch, tmp_path):
    from termatelier import storage
    colored_clipboard(session.document)
    blocked = tmp_path / "protected-program"
    blocked.mkdir()
    monkeypatch.setattr(storage, "project_roots", lambda: (blocked.resolve(),))
    with pytest.raises(CommandError, match="program/project"):
        session.execute(f'clipboard save "{blocked / "pixels.png"}"')
    assert not (blocked / "pixels.png").exists()


def test_clipboard_survives_new_and_open_documents_in_same_session(session, tmp_path):
    image = colored_clipboard(session.document)
    expected = image.tobytes()
    session.execute("new 6x6 --transparent")
    assert session.document.clipboard[0].tobytes() == expected
    session.execute("paste 1 1")
    assert session.document.layer.image.crop((1, 1, 3, 3)).tobytes() == expected
    source = tmp_path / "open.png"
    Image.new("RGBA", (7, 5), "blue").save(source)
    session.execute("open open.png")
    assert session.document.clipboard[0].tobytes() == expected


def test_empty_selection_does_not_replace_clipboard(session):
    doc = session.document
    colored_clipboard(doc)
    previous = doc.clipboard
    doc.selection = Image.new("L", doc.size)
    with pytest.raises(CommandError, match="empty"):
        session.execute("copy")
    assert doc.clipboard is previous


def test_atomic_script_failure_restores_clipboard_and_document(session):
    from termatelier.commands import ScriptError
    doc = session.document
    expected = colored_clipboard(doc).tobytes()
    before = state(doc)
    with pytest.raises(ScriptError):
        session.run_script("clipboard clear\nnew 4x4 --transparent\ncopy\ninvalid-operation\n")
    assert session.document is doc
    assert state(doc) == before
    assert doc.clipboard[0].tobytes() == expected
    assert doc.clipboard[1] == (2, 3)


def test_paste_at_layer_limit_rolls_back_without_consuming_clipboard(session):
    doc = session.document
    colored_clipboard(doc)
    doc.layers = [Layer(str(index), Image.new("RGBA", doc.size)) for index in range(64)]
    before = state(doc)
    clipboard = doc.clipboard
    with pytest.raises(CommandError, match="64 layers"):
        session.execute("paste --center --scale 4x4")
    assert state(doc) == before
    assert doc.clipboard is clipboard


def test_grid_overlap_uses_source_over_alpha_and_clips_negative_positions(session):
    doc = session.document
    source = Image.new("RGBA", (2, 2), (255, 0, 0, 128))
    doc.clipboard = (source, (0, 0))
    session.execute("paste -1 -1 --grid 2x2 --gap -1,-1")
    assert doc.layer.image.getpixel((0, 0)) == (255, 0, 0, 240)
    assert doc.layer.image.getpixel((1, 1)) == (255, 0, 0, 128)
    assert doc.layer.image.getpixel((2, 2)) == (0, 0, 0, 0)


@pytest.mark.parametrize("line", ["paste", "clipboard save"])
def test_empty_clipboard_errors_leave_history_untouched(session, line):
    doc = session.document
    before = state(doc)
    with pytest.raises(CommandError, match="first"):
        session.execute(line)
    assert state(doc) == before
