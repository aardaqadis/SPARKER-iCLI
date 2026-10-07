import pytest
from PIL import Image, ImageChops
from termatelier.model import BLENDS, Document, Layer, composite_layer
from termatelier.storage import export, import_document, load_project, save_project


@pytest.fixture(autouse=True)
def isolated_export_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("SPARKER_EXPORT_DIR", str(tmp_path / "exports"))


def blank(w=12, h=10):
    doc = Document(w, h)
    doc.layers = [Layer("Paint", Image.new("RGBA", doc.size))]
    doc.active = 0
    return doc


def test_stroke_selection_erase_and_one_step_undo():
    doc = blank()
    doc.select("rectangle", (2, 2), (5, 5))
    initial = doc.layer.image.tobytes()
    with doc.edit("Stroke"):
        doc.paint_mask(doc.stroke_mask([(0, 3), (10, 3)], 3), "red", .5)
    assert doc.layer.image.getpixel((3, 3)) == (255, 0, 0, 128)
    assert doc.layer.image.getpixel((1, 3))[3] == 0
    painted = doc.layer.image.tobytes()
    assert doc.undo() and doc.layer.image.tobytes() == initial
    assert doc.redo() and doc.layer.image.tobytes() == painted
    with doc.edit("Erase"):
        doc.paint_mask(Image.new("L", doc.size, 255), "black", .5, True)
    # Pillow's integer multiply floors 128 * 127 / 255.
    assert doc.layer.image.getpixel((3, 3))[3] == 63
    doc.undo()
    assert doc.layer.image.tobytes() == painted


def test_transaction_rollback_and_lock():
    doc = blank()
    with pytest.raises(ValueError):
        with doc.edit("Bad edit"):
            doc.add_layer()
            raise ValueError("failure")
    assert len(doc.layers) == 1 and not doc.undo_stack and not doc.dirty
    doc.layer.locked = True
    with pytest.raises(ValueError): doc.paint_mask(Image.new("L", doc.size, 255), "red")


@pytest.mark.parametrize("mode", BLENDS)
def test_blends_with_transparent_backdrop(mode):
    back = Image.new("RGBA", (1, 1))
    source = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
    assert composite_layer(back, source, mode).getpixel((0, 0)) == (200, 100, 50, 128)


def test_layer_opacity_mask_and_multiply():
    doc = blank(2, 2)
    doc.layer.image = Image.new("RGBA", doc.size, (100, 200, 255, 255))
    doc.add_layer(image=Image.new("RGBA", doc.size, (128, 128, 128, 255)))
    doc.layer.blend = "multiply"
    doc.layer.opacity = .5
    doc.layer.mask = Image.new("L", doc.size, 255)
    doc.layer.mask.putpixel((0, 0), 0)
    assert doc.composite().getpixel((0, 0)) == (100, 200, 255, 255)
    assert doc.composite().getpixel((1, 1))[:3] == (75, 150, 191)


def test_flood_region_and_tolerance():
    doc = blank()
    doc.paint_mask(doc.shape_mask("rectangle", (2, 2), (8, 8)), "red")
    inside = doc.region_mask((4, 4))
    assert inside.getpixel((4, 4)) == 255
    assert inside.getpixel((1, 1)) == 0
    doc.paint_mask(inside, "blue")
    assert doc.layer.image.getpixel((4, 4)) == (0, 0, 255, 255)
    assert doc.layer.image.getpixel((2, 2)) == (255, 0, 0, 255)


def test_gradients_text_shapes_and_filter_alpha():
    doc = blank(40, 20)
    doc.gradient((0, 0), (39, 0), "black", "white")
    assert doc.layer.image.getpixel((0, 0)) == (0, 0, 0, 255)
    assert doc.layer.image.getpixel((39, 0)) == (255, 255, 255, 255)
    doc.add_layer()
    doc.text((0, 0), "Hi", "red", 12)
    assert doc.layer.image.getchannel("A").getbbox() is not None
    alpha = doc.layer.image.getchannel("A").tobytes()
    doc.apply_filter("grayscale")
    assert doc.layer.image.getchannel("A").tobytes() == alpha


@pytest.mark.parametrize("name,amount", [("invert",1), ("sepia",1), ("autocontrast",1), ("blur",2),
    ("sharpen",1), ("edges",1), ("emboss",1), ("posterize",3), ("threshold",100),
    ("brightness",1.3), ("contrast",1.2), ("saturation",.5)])
def test_filters_respect_selection(name, amount):
    doc = blank()
    doc.layer.image = Image.new("RGBA", doc.size, (30, 80, 160, 123))
    doc.select("rectangle", (2, 2), (5, 5))
    doc.apply_filter(name, amount)
    assert doc.layer.image.getpixel((0, 0)) == (30, 80, 160, 123)
    assert doc.layer.image.getpixel((3, 3))[3] == 123


def test_cut_paste_move_transform_crop_resize():
    doc = blank(10, 10)
    doc.paint_mask(doc.shape_mask("rectangle", (2, 2), (4, 4), filled=True), "red")
    doc.select("rectangle", (2, 2), (4, 4))
    with doc.edit("Cut"): doc.copy_selection(True)
    assert doc.layer.image.getchannel("A").getbbox() is None
    with doc.edit("Paste"): doc.paste()
    assert doc.layer.image.getchannel("A").getbbox() == (2, 2, 5, 5)
    doc.layer.mask = Image.new("L", doc.size, 255)
    doc.move(2, 1)
    assert doc.layer.image.getchannel("A").getbbox() == (4, 3, 7, 6)
    assert doc.layer.mask.getpixel((0, 0)) == 0
    doc.transform_layer("flip_h")
    assert doc.layer.image.getchannel("A").getbbox() == (3, 3, 6, 6)
    doc.crop_selection()
    assert doc.size == (3, 3) and doc.selection is None
    doc.resize(6, 6)
    assert all(x.image.size == (6, 6) for x in doc.layers)
    assert doc.layer.mask.size == (6, 6)


def test_native_roundtrip_full_editable_state(tmp_path):
    doc = blank()
    doc.layer.image.putpixel((3, 4), (5, 10, 15, 77))
    doc.add_layer("Overlay", Image.new("RGBA", doc.size, "red"))
    doc.layer.opacity, doc.layer.blend, doc.layer.visible, doc.layer.locked = .3, "screen", False, True
    doc.layer.mask = Image.new("L", doc.size, 130)
    doc.select("ellipse", (2, 2), (6, 7))
    doc.metadata.update(title="Unicode café", guides_x=[3], guides_y=[4], author="Example")
    doc.settings.update(palette=["#123456"], foreground="#998877")
    path = tmp_path / "project.tart"
    save_project(doc, path)
    restored = load_project(path)
    assert restored.size == doc.size and restored.active == doc.active
    assert restored.metadata == doc.metadata and restored.settings == doc.settings
    assert restored.selection.tobytes() == doc.selection.tobytes()
    assert restored.composite().tobytes() == doc.composite().tobytes()
    for a, b in zip(restored.layers, doc.layers):
        assert (a.name,a.visible,a.locked,a.opacity,a.blend) == (b.name,b.visible,b.locked,b.opacity,b.blend)
        assert a.image.tobytes() == b.image.tobytes()
        if b.mask: assert a.mask.tobytes() == b.mask.tobytes()


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".webp", ".bmp", ".gif", ".tiff", ".txt", ".ansi"])
def test_exports_are_readable(tmp_path, suffix):
    doc = blank()
    doc.paint_mask(doc.shape_mask("ellipse", (2, 2), (7, 7), filled=True), "red", .5)
    path = tmp_path / ("export"+suffix)
    assert export(doc, path, 10, allow_lossy=True) == path
    if suffix in (".txt", ".ansi"):
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n") and len(text.splitlines()) == 4
        assert ("\x1b[" in text) == (suffix == ".ansi")
    else:
        with Image.open(path) as image: assert image.size == doc.size; image.load()
        if suffix in (".png", ".webp", ".tiff"):
            assert import_document(path).layer.image.tobytes() == doc.composite().tobytes()


def test_history_branching_and_saved_dirty(tmp_path):
    doc = blank()
    with doc.edit("A"): doc.layer.name = "A"
    save_project(doc, tmp_path/"a.tart")
    assert not doc.dirty
    doc.undo(); assert doc.dirty
    doc.redo(); assert not doc.dirty
    doc.undo()
    with doc.edit("B"): doc.layer.name = "B"
    assert doc.dirty and not doc.redo_stack
    doc.history_limit = 2
    for i in range(4):
        with doc.edit(str(i)): doc.layer.name = str(i)
    assert len(doc.undo_stack) == 2


def test_merge_matches_render():
    doc = Document(5, 5)
    doc.layers[0].opacity = .4
    doc.layer.image = Image.new("RGBA", doc.size, (45, 80, 160, 100))
    doc.layer.blend = "multiply"
    before = doc.composite().tobytes()
    doc.merge_down()
    assert doc.composite().tobytes() == before


def test_corrupt_project_and_size_rejected(tmp_path):
    import zipfile, json
    path = tmp_path/"bad.tart"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format":"wrong", "version":1}))
    with pytest.raises(ValueError): load_project(path)
    with pytest.raises(ValueError): Document(4096, 4096)


@pytest.mark.parametrize("manifest", [None, [], 3, "bad"])
def test_nonobject_manifest_rejected(tmp_path, manifest):
    import zipfile, json
    path = tmp_path / "broken.tart"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError): load_project(path)


def test_failed_atomic_write_preserves_previous_file(tmp_path):
    from termatelier.storage import atomic_write
    path = tmp_path / "original.txt"
    path.write_text("keep me")
    def failing(tmp):
        from pathlib import Path
        Path(tmp).write_text("partial write")
        raise OSError("disk error")
    with pytest.raises(OSError): atomic_write(path, failing)
    assert path.read_text() == "keep me"
    assert not list(tmp_path.glob(".termatelier-*"))


def test_wand_selection_combinations_and_empty_selection():
    doc = blank(8, 8)
    doc.select("rectangle", (1, 1), (3, 3))
    doc.select("rectangle", (5, 5), (6, 6), "add")
    assert doc.selection.getpixel((2, 2)) == 255 and doc.selection.getpixel((5, 5)) == 255
    doc.select("rectangle", (0, 0), (4, 4), "subtract")
    assert doc.selection.getbbox() == (5, 5, 7, 7)
    doc.select("rectangle", (0, 0), (2, 2), "intersect")
    assert doc.selection.getbbox() is None
    doc.paint_mask(Image.new("L", doc.size, 255), "red")
    assert doc.layer.image.getchannel("A").getbbox() is None


def test_rgba_brush_color_combines_color_tool_and_selection_alpha():
    doc = blank(3, 3)
    doc.selection = Image.new("L", doc.size, 128)
    doc.paint_mask(Image.new("L", doc.size, 255), "#ff000080", .5)
    assert doc.layer.image.getpixel((1, 1)) == (255, 0, 0, 32)
    doc.paint_mask(Image.new("L", doc.size, 255), "#00ff0000")
    assert doc.layer.image.getpixel((1, 1)) == (255, 0, 0, 32)


def test_rgba_gradient_preserves_interpolated_alpha_and_opacity():
    doc = blank(3, 1)
    doc.gradient((0, 0), (2, 0), "#ff000000", "#0000ffff", opacity=.5)
    assert doc.layer.image.getpixel((0, 0))[3] == 0
    assert doc.layer.image.getpixel((1, 0)) == (128, 0, 128, 64)
    assert doc.layer.image.getpixel((2, 0)) == (0, 0, 255, 128)
    doc.gradient((0, 0), (2, 0), "red", "blue", opacity=2)
    assert doc.layer.image.getpixel((0, 0)) == (255, 0, 0, 255)


def test_history_budget_includes_redo_after_canvas_size_changes():
    doc = blank(2, 2)
    doc.history_bytes = 80
    with doc.edit("Grow"):
        doc.resize(4, 4)
    with doc.edit("Paint"):
        doc.paint_mask(Image.new("L", doc.size, 255), "red")
    assert doc.undo()
    assert doc.undo()
    assert len(doc.redo_stack) == 1
    assert doc.redo()
    assert doc.size == (4, 4)
    assert doc.layer.image.getchannel("A").getbbox() is None


def test_undo_rejects_an_uncommitted_edit_without_losing_it():
    doc = blank()
    with doc.edit("First"):
        doc.layer.name = "First"
    doc.begin("Stroke")
    doc.layer.name = "Pending"
    with pytest.raises(RuntimeError):
        doc.undo()
    assert doc.pending is not None and doc.layer.name == "Pending"
    doc.cancel()
    assert doc.layer.name == "First" and doc.undo()


def test_add_layer_normalizes_rgb_import_without_changing_the_source():
    doc = blank()
    source = Image.new("RGB", doc.size, "red")
    doc.add_layer("RGB", source)
    assert source.mode == "RGB" and doc.layer.image.mode == "RGBA"
    assert doc.composite().getpixel((0, 0)) == (255, 0, 0, 255)


@pytest.mark.parametrize("width,height", [(True, 2), (2.5, 2), (2, "3")])
def test_canvas_dimensions_require_integer_pixels(width, height):
    with pytest.raises(ValueError):
        Document(width, height)


def test_invalid_selection_and_shape_do_not_mutate_document():
    doc = blank()
    with pytest.raises(ValueError):
        doc.shape_mask("unknown", (1, 1), (3, 3))
    with pytest.raises(ValueError):
        doc.set_selection(Image.new("L", doc.size), "unknown")
    with pytest.raises(ValueError):
        doc.set_selection(Image.new("L", (1, 1)))
    assert doc.selection is None


def test_invalid_save_preserves_existing_project_and_dirty_status(tmp_path):
    path = tmp_path / "project.tart"
    doc = blank()
    save_project(doc, path)
    before = path.read_bytes()
    with doc.edit("Invalid opacity"):
        doc.layer.opacity = float("nan")
    with pytest.raises(ValueError):
        save_project(doc, path)
    assert path.read_bytes() == before and doc.dirty
    assert load_project(path).layer.opacity == 1


@pytest.mark.parametrize("field,value", [("version", True), ("selection", False),
                                        ("selection", 0), ("active", True)])
def test_malformed_project_fields_are_rejected_before_decoding(tmp_path, field, value):
    import json
    import zipfile
    path = tmp_path / "project.tart"
    save_project(blank(), path)
    with zipfile.ZipFile(path) as archive:
        entries = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    manifest = json.loads(entries["manifest.json"])
    manifest[field] = value
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError):
        load_project(path)


def test_nonfinite_custom_metadata_is_rejected_on_save(tmp_path):
    doc = blank()
    doc.metadata["custom"] = float("nan")
    with pytest.raises(ValueError):
        save_project(doc, tmp_path / "nan.tart")
    assert not (tmp_path / "nan.tart").exists()
