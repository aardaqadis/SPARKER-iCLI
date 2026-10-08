"""Lossless checkpoints and measured pixel-storage savings in low-memory mode."""
import copy
import random

import pytest
from PIL import Image, ImageDraw

from termatelier.model import Document
from termatelier.storage import export, load_project, save_project


def fingerprint(document):
    """Compare every editable field, including transparent pixels and masks."""
    return (document.size, document.active, document.revision,
            [(layer.name, layer.image.mode, layer.image.tobytes(), layer.visible,
              layer.locked, layer.opacity, layer.blend,
              layer.mask.tobytes() if layer.mask is not None else None)
             for layer in document.layers],
            document.selection.tobytes() if document.selection is not None else None,
            copy.deepcopy(document.metadata), copy.deepcopy(document.settings))


def varied_document(storage):
    document = Document(32, 24)
    document.history_storage = storage
    rng = random.Random(81)
    document.layers[0].image = Image.frombytes("RGBA", document.size, rng.randbytes(32 * 24 * 4))
    document.layer.image = Image.frombytes("RGBA", document.size, rng.randbytes(32 * 24 * 4))
    document.layer.mask = Image.frombytes("L", document.size, rng.randbytes(32 * 24))
    document.layer.opacity = .37
    document.layer.blend = "screen"
    document.selection = Image.frombytes("L", document.size, rng.randbytes(32 * 24))
    document.metadata.update(title="Café 火", guides_x=[2, 7], custom={"notes": ["one", "two"]})
    document.settings.update(custom={"values": [1, .25, None]}, palette=["#123456", "#abcdef"])
    return document


@pytest.mark.parametrize("storage", ["raw", "compressed"])
def test_history_restores_all_editable_state_and_exact_pixels(storage):
    document = varied_document(storage)
    before = fingerprint(document)
    with document.edit("Paint, mask and properties"):
        document.layer.image.putpixel((2, 3), (19, 33, 71, 0))
        document.layer.mask.putpixel((2, 3), 11)
        document.layer.name = "Changed"
        document.layer.visible = False
        document.layer.locked = True
        document.layer.opacity = .82
        document.layer.blend = "difference"
        document.selection.putpixel((7, 8), 22)
        document.active = 0
        document.metadata["custom"]["notes"].append("three")
        document.settings["custom"]["values"].append(3)
    after = fingerprint(document)
    for _ in range(3):
        assert document.undo() and fingerprint(document) == before
        assert document.redo() and fingerprint(document) == after


def test_compressed_history_keeps_public_snapshot_and_pending_stroke_raw():
    document = varied_document("compressed")
    snapshot = document.snapshot()
    assert isinstance(snapshot, tuple) and len(snapshot) == 7
    assert isinstance(snapshot[1][1].image, Image.Image)
    assert isinstance(snapshot[1][1].mask, Image.Image)
    assert isinstance(snapshot[3], Image.Image)
    before = fingerprint(document)
    document.begin("Brush")
    base = document.pending[1][1][document.active].image
    assert isinstance(base, Image.Image)
    document.paint_mask(Image.new("L", (3, 3), 255), "red", base=base, box=(4, 4, 7, 7))
    assert base.tobytes() == snapshot[1][1].image.tobytes()
    document.cancel()
    assert fingerprint(document) == before and not document.undo_stack
    document.layer.image.putpixel((1, 1), (0, 0, 0, 0))
    document.restore(snapshot)
    assert fingerprint(document) == before
    document.layer.image.putpixel((0, 0), (1, 2, 3, 4))
    assert snapshot[1][1].image.getpixel((0, 0)) != (1, 2, 3, 4)


def test_failed_compressed_transaction_rolls_back_without_history_changes():
    document = varied_document("compressed")
    with document.edit("Existing"):
        document.layer.name = "Existing"
    before = fingerprint(document)
    payload = document.history_memory_bytes
    with pytest.raises(ValueError, match="failed"):
        with document.edit("Failure"):
            document.resize(16, 12)
            document.add_layer("Temporary")
            document.settings["custom"]["values"].append(99)
            raise ValueError("failed")
    assert fingerprint(document) == before
    assert document.pending is None
    assert [label for label, _ in document.undo_stack] == ["Existing"]
    assert document.history_memory_bytes == payload
    assert document.undo()


def test_switching_storage_compacts_existing_undo_and_redo_without_losing_steps():
    document = Document(256, 192)
    states = [fingerprint(document)]
    for color in ("red", "green", "blue"):
        with document.edit(color):
            ImageDraw.Draw(document.layer.image).line((1, 1, 80, 90), fill=color, width=4)
        states.append(fingerprint(document))
    assert document.undo() and fingerprint(document) == states[-2]
    before_bytes = document.history_memory_bytes
    document.history_storage = "compressed"
    assert len(document.undo_stack) == 2 and len(document.redo_stack) == 1
    assert document.history_memory_bytes < before_bytes / 20
    assert document.redo() and fingerprint(document) == states[-1]
    document.history_storage = "raw"
    assert document.undo() and fingerprint(document) == states[-2]
    assert document.undo() and fingerprint(document) == states[-3]
    assert document.redo() and fingerprint(document) == states[-2]
    assert document.redo() and fingerprint(document) == states[-1]
    document.history_storage = "compressed"
    with document.edit("Mixed new checkpoint"):
        document.layer.name = "new"
    assert document.undo() and fingerprint(document) == states[-1]
    assert len(document.undo_stack) == 3


def test_compressed_redo_and_undo_share_byte_budget_after_canvas_size_changes():
    document = Document(32, 24)
    document.layers = document.layers[1:]
    document.active = 0
    document.history_storage = "compressed"
    original = fingerprint(document)
    with document.edit("Grow"):
        document.resize(128, 96)
    grown = fingerprint(document)
    with document.edit("Paint"):
        document.layer.image.paste("red", (0, 0, document.width, document.height))
    painted = fingerprint(document)
    assert document.undo() and fingerprint(document) == grown
    combined_bytes = document.history_memory_bytes
    document.history_bytes = combined_bytes
    assert document.undo() and fingerprint(document) == original
    assert document.history_memory_bytes <= combined_bytes
    document.history_limit = 1
    document._trim()
    assert len(document.undo_stack) + len(document.redo_stack) == 1
    assert document.redo() and fingerprint(document) == grown
    assert not document.redo()
    assert painted != grown
    document.history_bytes = 0
    document._trim()
    assert document.history_memory_bytes == 0
    assert not document.undo_stack and not document.redo_stack


def test_empty_selection_and_mask_remain_distinct_from_no_selection_and_mask():
    document = Document(4, 3)
    document.history_storage = "compressed"
    document.selection = Image.new("L", document.size)
    document.layer.mask = Image.new("L", document.size)
    with document.edit("Remove masks"):
        document.selection = None
        document.layer.mask = None
    assert document.undo()
    assert document.selection is not None and document.selection.getbbox() is None
    assert document.layer.mask is not None and document.layer.mask.getbbox() is None
    assert document.redo()
    assert document.selection is None and document.layer.mask is None


def test_incompressible_pixels_do_not_expand_and_restore_losslessly():
    document = varied_document("compressed")
    before = fingerprint(document)
    raw_bytes = Document._snapshot_bytes(document.snapshot())
    with document.edit("Noise"):
        document.layer.image.putpixel((0, 0), (4, 3, 2, 1))
    assert document.history_memory_bytes <= raw_bytes
    assert document.undo() and fingerprint(document) == before


def test_compressed_history_reduces_retained_pixel_bytes_for_real_strokes():
    documents = [Document(512, 512), Document(512, 512)]
    documents[1].history_storage = "compressed"
    for step in range(8):
        for document in documents:
            with document.edit(f"Stroke {step}"):
                document.paint_mask(document.stroke_mask([(10, 20 + step * 25), (480, 30 + step * 25)], 3),
                                    (step * 25, 101, 211, 255))
    raw, compressed = documents
    assert fingerprint(raw) == fingerprint(compressed)
    assert raw.history_memory_bytes == 16 * 1024 * 1024
    assert compressed.history_memory_bytes < raw.history_memory_bytes / 50
    for _ in range(8):
        assert raw.undo() and compressed.undo()
        assert fingerprint(raw) == fingerprint(compressed)
    for _ in range(8):
        assert raw.redo() and compressed.redo()
        assert fingerprint(raw) == fingerprint(compressed)


def test_compressed_undo_survives_project_save_and_exports_exact_native_pixels(tmp_path):
    document = varied_document("compressed")
    before = fingerprint(document)
    with document.edit("Pixels"):
        document.layer.image.putpixel((6, 7), (200, 100, 30, 83))
    expected = document.composite().tobytes()
    project = tmp_path / "painting.tart"
    save_project(document, project)
    restored = load_project(project)
    assert restored.size == document.size
    assert restored.composite().tobytes() == expected
    assert restored.layer.image.tobytes() == document.layer.image.tobytes()
    assert restored.layer.mask.tobytes() == document.layer.mask.tobytes()
    image_path = tmp_path / "painting.png"
    export(document, image_path)
    with Image.open(image_path) as image:
        assert image.size == document.size and image.tobytes() == expected
    assert document.undo() and fingerprint(document) == before and document.dirty
    assert document.redo() and document.composite().tobytes() == expected and not document.dirty


def test_history_storage_rejects_invalid_preferences_without_mutating_history():
    document = Document(4, 3)
    with document.edit("Name"):
        document.layer.name = "Named"
    before = document.history_memory_bytes
    with pytest.raises(ValueError, match="raw or compressed"):
        document.history_storage = "lossy"
    assert document.history_storage == "raw"
    assert document.history_memory_bytes == before
    assert document.undo() and document.layer.name == "Paint"


@pytest.mark.parametrize("mode", ["standard", "low"])
def test_atomic_command_script_preserves_history_and_clipboard_in_each_memory_mode(tmp_path, mode):
    from termatelier.commands import CommandSession, ScriptError
    from termatelier.config import RuntimeConfig

    config = RuntimeConfig.load()
    config.set("memory.mode", mode)
    session = CommandSession(base_dir=tmp_path, config=config)
    session.execute("new 32x24 --transparent")
    session.execute("pencil 1,1 --color red")
    session.execute("pencil 2,2 --color blue")
    session.execute("copy")
    session.execute("save original.tart")
    document = session.document
    before = fingerprint(document)
    labels = [label for label, _ in document.undo_stack]
    payload = document.history_memory_bytes
    clipboard = copy.deepcopy(document.clipboard)
    with pytest.raises(ScriptError, match=r"broken.sparker:5"):
        session.run_script("pencil 4,4 --color green\ncut\nnew 8x8 --transparent\nlayer add Temporary\ninvalid-command",
                           "broken.sparker")
    assert session.document is document
    assert fingerprint(document) == before
    assert [label for label, _ in document.undo_stack] == labels
    assert document.history_memory_bytes == payload
    assert document.history_storage == ("compressed" if mode == "low" else "raw")
    assert document.clipboard[0].tobytes() == clipboard[0].tobytes()
    assert document.clipboard[1] == clipboard[1]
    assert document.pending is None and not document.dirty
    assert session.project_path == (tmp_path / "original.tart").resolve()
    assert document.undo()
    assert document.layer.image.getpixel((2, 2)) == (0, 0, 0, 0)
    assert document.redo() and fingerprint(document) == before
