"""Behavior checks for transactional editing command extensions."""
import json

from PIL import Image, ImageFilter, ImageOps
import pytest

from termatelier.commands import CommandError, CommandSession, ScriptError
from termatelier.editing_commands import HELP, _morphology, execute_extra
from termatelier.model import Document, Layer


def workspace(size=(21, 17)):
    doc = Document(*size)
    doc.layers = [Layer("Paint", Image.new("RGBA", size))]
    doc.active = 0
    return CommandSession(doc)


def run(session, command):
    return session.execute(command)


def state(doc):
    return ([layer.image.tobytes() for layer in doc.layers],
            doc.selection.tobytes() if doc.selection is not None else None,
            doc.revision, len(doc.undo_stack), len(doc.redo_stack))


def test_unknown_command_is_safe_for_other_adapters():
    assert execute_extra(workspace(), "another-command", []) is None
    assert set(HELP) == {"pixel", "polygon", "polyline", "bezier", "align", "selection", "adjust", "colors"}


def test_pixel_replacement_inspection_feathered_selection_and_history():
    session = workspace()
    doc = session.document
    original = doc.layer.image.tobytes()
    result = run(session, "pixel 4 5 #ff003380 --json")
    assert result.changed and json.loads(result.text)["rgba"] == [255, 0, 51, 128]
    painted = doc.layer.image.tobytes()
    assert doc.undo() and doc.layer.image.tobytes() == original
    assert doc.redo() and doc.layer.image.tobytes() == painted
    doc.selection = Image.new("L", doc.size)
    doc.selection.putpixel((4, 5), 128)
    run(session, "pixel 4 5 #00000000")
    assert doc.layer.image.getpixel((4, 5)) == (127, 0, 25, 64)
    before = state(doc)
    assert not run(session, "pixel 0 0 red").changed
    assert state(doc) == before


def test_pixel_inspection_uses_merged_mask_and_opacity_without_history_or_full_canvas(monkeypatch):
    session = workspace()
    doc = session.document
    doc.layer.image.putpixel((2, 3), (255, 0, 0, 255))
    doc.layers.append(Layer("Overlay", Image.new("RGBA", doc.size, (0, 0, 255, 128))))
    doc.active = 1
    expected = doc.composite().getpixel((2, 3))
    before = state(doc)
    composite = doc.composite
    boxes = []
    def bounded(box=None):
        assert box is not None
        boxes.append(box)
        return composite(box)
    monkeypatch.setattr(doc, "composite", bounded)
    result = json.loads(run(session, "pixel 2 3 --json").text)
    assert tuple(result["rgba"]) == expected and boxes == [(2, 3, 3, 4)]
    assert json.loads(run(session, "pixel 2 3 --active --json").text)["rgba"] == [0, 0, 255, 128]
    assert state(doc) == before


def test_polygon_and_polyline_draw_real_geometry_with_selection_opacity_and_undo():
    session = workspace()
    doc = session.document
    doc.settings["brush_size"] = 1
    doc.select("rectangle", (3, 3), (14, 12))
    original = doc.layer.image.tobytes()
    run(session, "polygon 1,1 17,1 17,14 1,14 --filled --color red --opacity 50%")
    assert doc.layer.image.getpixel((5, 5)) == (255, 0, 0, 128)
    assert doc.layer.image.getpixel((1, 1))[3] == 0
    assert doc.undo() and doc.layer.image.tobytes() == original
    doc.selection = None
    run(session, "polyline 2,2 10,2 10,10 --color blue --width 1")
    assert doc.layer.image.getpixel((6, 2)) == (0, 0, 255, 255)
    assert doc.layer.image.getpixel((10, 6)) == (0, 0, 255, 255)
    assert doc.layer.image.getpixel((6, 6))[3] == 0
    run(session, "polygon 1,1 18,1 18,14 1,14 --color green --width 1")
    assert doc.layer.image.getpixel((1, 7)) == (0, 128, 0, 255)
    assert doc.layer.image.getpixel((5, 5))[3] == 0


def test_bezier_reaches_endpoints_and_curve_interior_without_control_point_marks():
    session = workspace((41, 31))
    doc = session.document
    run(session, "bezier 2,20 2,0 38,0 38,20 --color orange --width 1 --steps 128")
    assert doc.layer.image.getpixel((2, 20)) == (255, 165, 0, 255)
    assert doc.layer.image.getpixel((38, 20)) == (255, 165, 0, 255)
    assert doc.layer.image.getpixel((20, 5))[3] == 255
    assert doc.layer.image.getpixel((2, 0))[3] == 0
    assert doc.layer.image.getpixel((20, 20))[3] == 0
    painted = doc.layer.image.tobytes()
    assert doc.undo() and doc.layer.image.getchannel("A").getbbox() is None
    assert doc.redo() and doc.layer.image.tobytes() == painted


@pytest.mark.parametrize("mode,expected", [("left", (0, 4, 3, 7)), ("right", (18, 4, 21, 7)),
    ("center-x", (9, 4, 12, 7)), ("top", (2, 0, 5, 3)), ("bottom", (2, 14, 5, 17)),
    ("center-y", (2, 7, 5, 10)), ("center", (9, 7, 12, 10))])
def test_align_active_alpha_bounds_and_layer_mask(mode, expected):
    session = workspace()
    doc = session.document
    doc.layer.image.paste((255, 0, 0, 255), (2, 4, 5, 7))
    doc.layer.mask = Image.new("L", doc.size)
    doc.layer.mask.paste(200, (2, 4, 5, 7))
    before = doc.layer.image.tobytes(), doc.layer.mask.tobytes()
    run(session, "align " + mode)
    assert doc.layer.image.getchannel("A").getbbox() == expected
    assert doc.layer.mask.getbbox() == expected
    assert doc.undo() and (doc.layer.image.tobytes(), doc.layer.mask.tobytes()) == before


def test_align_with_selection_bounds_preserves_selection():
    session = workspace()
    doc = session.document
    doc.layer.image.paste((255, 0, 0, 255), (1, 1, 4, 4))
    doc.select("rectangle", (7, 5), (15, 13))
    selected = doc.selection.tobytes()
    run(session, "align center --selection")
    assert doc.layer.image.getchannel("A").getbbox() == (10, 8, 13, 11)
    assert doc.selection.tobytes() == selected


def test_selection_bounds_growth_shrinkage_border_threshold_and_history():
    session = workspace()
    doc = session.document
    assert json.loads(run(session, "selection bounds --json").text)["bounds"] is None
    doc.select("rectangle", (5, 5), (9, 9))
    original = doc.selection.tobytes()
    bounds = json.loads(run(session, "selection bounds --json").text)
    assert bounds == {"active": True, "bounds": [5, 5, 10, 10], "width": 5, "height": 5}
    run(session, "selection grow 2")
    assert doc.selection.getbbox() == (3, 3, 12, 12)
    assert doc.undo() and doc.selection.tobytes() == original
    run(session, "selection shrink 1")
    assert doc.selection.getbbox() == (6, 6, 9, 9)
    assert doc.undo()
    run(session, "selection border 1")
    assert doc.selection.getpixel((5, 7)) == 255 and doc.selection.getpixel((7, 7)) == 0
    assert doc.undo()
    doc.selection.putpixel((7, 7), 127)
    doc.selection.putpixel((8, 7), 128)
    run(session, "selection threshold 128")
    assert doc.selection.getpixel((7, 7)) == 0 and doc.selection.getpixel((8, 7)) == 255


def test_full_canvas_selection_shrinks_at_canvas_edges_and_empty_selection_stays_empty():
    session = workspace((9, 7))
    doc = session.document
    doc.selection = Image.new("L", doc.size, 255)
    run(session, "selection shrink 2")
    assert doc.selection.getbbox() == (2, 2, 7, 5)
    doc.selection = Image.new("L", doc.size)
    run(session, "selection grow 3")
    assert doc.selection.getbbox() is None


@pytest.mark.parametrize("command,expected", [("adjust gamma 2", (128, 181, 221, 128)),
    ("adjust levels 64 192", (0, 128, 255, 128)),
    ("adjust temperature 0.5", (96, 128, 160, 128)),
    ("adjust alpha 50%", (64, 128, 192, 64)), ("adjust alpha 4", (64, 128, 192, 255))])
def test_adjustments_change_selected_pixels_preserve_other_pixels_alpha_and_undo(command, expected):
    session = workspace()
    doc = session.document
    doc.layer.image = Image.new("RGBA", doc.size, (64, 128, 192, 128))
    doc.layer.mask = Image.new("L", doc.size, 97)
    mask_before = doc.layer.mask.tobytes()
    doc.select("rectangle", (2, 2), (7, 8))
    before = doc.layer.image.tobytes()
    run(session, command)
    assert doc.layer.image.getpixel((4, 5)) == expected
    assert doc.layer.image.getpixel((0, 0)) == (64, 128, 192, 128)
    assert doc.layer.mask.tobytes() == mask_before
    after = doc.layer.image.tobytes()
    assert doc.undo() and doc.layer.image.tobytes() == before
    assert doc.redo() and doc.layer.image.tobytes() == after


def test_rgb_adjustments_preserve_invisible_rgb_and_feathered_selection():
    session = workspace()
    doc = session.document
    doc.layer.image = Image.new("RGBA", doc.size, (64, 64, 64, 128))
    doc.layer.image.putpixel((1, 1), (70, 90, 120, 0))
    doc.selection = Image.new("L", doc.size, 128)
    run(session, "adjust gamma 2")
    assert doc.layer.image.getpixel((1, 1)) == (70, 90, 120, 0)
    assert doc.layer.image.getpixel((4, 5)) == (96, 96, 96, 128)


def test_colors_reports_actual_visible_palette_and_bounded_readonly_sampling(monkeypatch):
    session = workspace((2048, 1024))
    doc = session.document
    doc.layer.image.paste((255, 0, 0, 255), (0, 0, 1536, 1024))
    doc.layer.image.paste((0, 0, 255, 255), (1536, 0, 2048, 1024))
    before = state(doc)
    def forbidden(*args, **kwargs):
        raise AssertionError("Palette extraction must not composite a full large canvas")
    monkeypatch.setattr(doc, "composite", forbidden)
    data = json.loads(run(session, "colors --limit 2 --json").text)
    assert data["sample_size"] == [256, 128]
    assert data["visible_samples"] == 32768
    assert data["colors"] == [{"color": "#ff0000", "samples": 24576}, {"color": "#0000ff", "samples": 8192}]
    assert state(doc) == before
    doc.layers.append(Layer("Transparent", Image.new("RGBA", doc.size, (0, 255, 0, 0))))
    doc.active = 1
    assert json.loads(run(session, "colors --active --json").text)["colors"] == []


@pytest.mark.parametrize("command", ["pixel -1 0 red", "pixel 2 3 nonsense", "pixel 2 3 --active --merged",
    "polygon 1,1 2,2", "polyline 1,1 99999999,2", "bezier 1,1 2,2 3,3 4,4 --steps 99999",
    "align diagonal", "align center --selection", "selection grow 9999", "selection shrink 1",
    "adjust gamma nan", "adjust levels 200 100", "adjust temperature inf", "adjust alpha -1",
    "colors --limit 0", "colors --limit 1000000", "polygon 1,1 2,2 3,3 --color red --opacity 2"])
def test_invalid_commands_preserve_pixels_selection_and_history(command):
    session = workspace()
    before = state(session.document)
    with pytest.raises(CommandError):
        run(session, command)
    assert state(session.document) == before


@pytest.mark.parametrize("command", ["pixel 2 3 red", "polygon 1,1 5,1 3,5 --filled", "polyline 1,1 5,5",
    "bezier 1,1 1,5 5,1 5,5", "align right", "adjust gamma 2"])
def test_locked_layers_reject_mutations_without_partial_changes(command):
    session = workspace()
    doc = session.document
    doc.layer.image.putpixel((2, 3), (255, 0, 0, 255))
    doc.layer.locked = True
    before = state(doc)
    with pytest.raises(CommandError, match="locked"):
        run(session, command)
    assert state(doc) == before


def test_path_vertex_budget_prevents_unbounded_work():
    session = workspace()
    before = state(session.document)
    with pytest.raises(CommandError):
        run(session, "polyline " + " ".join(["1,1"] * 2049))
    assert state(session.document) == before


def test_mid_operation_failure_rolls_back_pixel_selection_mask_and_history(monkeypatch):
    session = workspace()
    doc = session.document
    doc.selection = Image.new("L", doc.size, 128)
    doc.layer.mask = Image.new("L", doc.size, 97)
    mask_before, before = doc.layer.mask.tobytes(), state(doc)
    def fail(*args, **kwargs):
        doc.layer.image.putpixel((2, 3), (255, 0, 0, 255))
        doc.selection.putpixel((2, 3), 255)
        doc.layer.mask.putpixel((2, 3), 0)
        raise ValueError("Synthetic drawing failure")
    monkeypatch.setattr(doc, "paint_mask", fail)
    with pytest.raises(CommandError, match="Synthetic drawing failure"):
        run(session, "polygon 1,1 9,1 5,8 --filled")
    assert state(doc) == before and doc.layer.mask.tobytes() == mask_before
    assert doc.pending is None


def test_atomic_script_rolls_back_new_commands_and_history_when_later_input_is_invalid():
    session = workspace()
    before = state(session.document)
    with pytest.raises(ScriptError):
        session.run_script("pixel 2 3 red\npolygon 1,1 9,1 5,8 --filled\nadjust levels 200 100")
    assert state(session.document) == before


def test_new_command_help_and_search_are_registered_on_shared_session():
    session = workspace()
    assert "cubic" in run(session, "help bezier").text
    found = json.loads(run(session, "commands selection --json").text)
    assert "selection" in found and "align" in found


@pytest.mark.parametrize("size,radius", [((1, 1), 1), ((5, 3), 1), ((7, 9), 2),
    ((17, 11), 3), ((32, 23), 7), ((5, 3), 15), ((13, 9), 0)])
@pytest.mark.parametrize("grow", [True, False])
@pytest.mark.parametrize("feathered", [True, False])
def test_fast_square_morphology_matches_pillow_for_hard_and_feathered_edges(size, radius, grow, feathered):
    raw = bytes((x * 37 + y * 13 + 9) % 256 for y in range(size[1]) for x in range(size[0]))
    mask = Image.frombytes("L", size, raw)
    if not feathered:
        mask = mask.point([0 if value < 128 else 255 for value in range(256)])
    original = mask.tobytes()
    padded = ImageOps.expand(mask, border=radius, fill=0)
    operation = ImageFilter.MaxFilter if grow else ImageFilter.MinFilter
    # Pillow's rank-filter implementation is undefined for a 1×1 kernel;
    # radius zero is an exact identity and does not need a reference filter.
    reference = (padded.filter(operation(2 * radius + 1)).crop(
        (radius, radius, radius + size[0], radius + size[1])) if radius else mask.copy())
    actual = _morphology(mask, radius, grow)
    assert actual.size == size and actual.mode == "L"
    assert actual.tobytes() == reference.tobytes()
    assert mask.tobytes() == original


def test_maximum_radius_large_selection_avoids_rank_filter_work_and_preserves_edges(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Large selections must not scan a 129×129 rank-filter kernel")
    monkeypatch.setattr(Image.Image, "filter", forbidden)
    mask = Image.new("L", (1024, 768), 255)
    assert _morphology(mask, 64, True).getextrema() == (255, 255)
    shrink = _morphology(mask, 64, False)
    assert shrink.getbbox() == (64, 64, 960, 704)
    assert shrink.getpixel((63, 64)) == 0 and shrink.getpixel((64, 64)) == 255
