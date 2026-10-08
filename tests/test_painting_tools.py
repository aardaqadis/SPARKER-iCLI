"""Pixel-level behavior checks for painting and assisted selection families."""
import math
import time

import numpy as np
from PIL import Image, ImageDraw
import pytest

from termatelier.commands import CommandSession, tokenize
from termatelier.model import Document, Layer
from termatelier.painting_commands import HELP, execute_painting
from termatelier.painting_tools import (MOUSE_TOOLS, NATURAL_PRESETS, apply_stroke,
    color_selection, foreground_selection, perspective_coefficients, scissors_selection)


def workspace(size=(48, 32), color=(0, 0, 0, 0)):
    doc = Document(*size)
    doc.layers = [Layer("Paint", Image.new("RGBA", size, color))]
    doc.active = 0
    return CommandSession(doc)


def run(session, line):
    tokens = tokenize(line)
    return execute_painting(session, tokens[0], tokens[1:])


def saved(doc):
    return (doc.layer.image.tobytes(), doc.selection.tobytes() if doc.selection is not None else None,
            doc.revision, len(doc.undo_stack), len(doc.redo_stack))


def pattern(doc):
    ys, xs = np.mgrid[:doc.height, :doc.width]
    pixels = np.empty((doc.height, doc.width, 4), dtype=np.uint8)
    pixels[..., 0], pixels[..., 1], pixels[..., 2], pixels[..., 3] = xs * 5, ys * 7, (xs + ys) * 3, 255
    doc.layer.image = Image.fromarray(pixels, "RGBA")


def test_family_registry_and_unknown_adapter():
    assert len(HELP) == 11 and len(MOUSE_TOOLS) == 11
    assert execute_painting(workspace(), "unrelated", []) is None
    result = run(workspace(), "natural list")
    assert not result.changed and all(name in result.text for name in NATURAL_PRESETS)


def test_clone_frozen_aligned_pixels_continuity_opacity_mask_sampling_and_undo():
    session = workspace()
    doc = session.document
    pattern(doc)
    original = doc.layer.image.copy()
    result = run(session, "clone 25,12 38,12 --source 5,5 --size 3 --hardness 1")
    assert result.changed
    for x in range(25, 39):
        assert doc.layer.image.getpixel((x, 12)) == original.getpixel((x - 20, 5))
    assert doc.undo() and doc.layer.image.tobytes() == original.tobytes()
    doc.layer.mask = Image.new("L", doc.size, 128)
    doc.layer.opacity = .5
    run(session, "clone 25,12 --source 5,5 --size 1 --opacity 50%")
    sampled = original.getpixel((5, 5))[:3] + (32,)
    expected = Image.alpha_composite(original.crop((25, 12, 26, 13)), Image.new("RGBA", (1, 1), sampled))
    assert doc.layer.image.getpixel((25, 12)) == expected.getpixel((0, 0))
    assert doc.layer.mask.getpixel((25, 12)) == 128


def test_clone_mouse_full_path_grows_without_resampling_previous_target():
    doc = workspace().document
    pattern(doc)
    original = doc.layer.image.copy()
    state = {}
    with doc.edit("Clone gesture"):
        apply_stroke(doc, "clone", [(25, 12)], source=(5, 5), size=1, state=state)
        apply_stroke(doc, "clone", [(25, 12), (38, 12)], source=(5, 5), size=1, state=state)
        unchanged = doc.layer.image.tobytes()
        assert apply_stroke(doc, "clone", [(25, 12), (38, 12)], source=(5, 5), size=1, state=state) is None
        assert doc.layer.image.tobytes() == unchanged
    assert len(doc.undo_stack) == 1
    assert doc.layer.image.getpixel((38, 12)) == original.getpixel((18, 5))


def test_clone_sample_merged_and_source_setting():
    session = workspace(color="blue")
    doc = session.document
    doc.layers.insert(0, Layer("Below", Image.new("RGBA", doc.size, "red")))
    doc.active = 1
    doc.layer.image.putpixel((5, 5), (0, 0, 255, 0))
    doc.settings["clone_source"] = (5, 5)
    with doc.edit("Clone"):
        apply_stroke(doc, "clone", [(20, 12)], size=1, merged=True)
    assert doc.layer.image.getpixel((20, 12)) == (255, 0, 0, 255)


def test_heal_carries_texture_adapts_destination_color_and_preserves_alpha():
    session = workspace(color=(20, 50, 150, 170))
    doc = session.document
    for y in range(4, 13):
        for x in range(4, 13):
            variation = 20 if (x + y) % 2 else -20
            doc.layer.image.putpixel((x, y), (170 + variation, 70 + variation, 50 + variation, 255))
    original = doc.layer.image.copy()
    run(session, "heal 28,16 --source 8,8 --size 9 --radius 2 --hardness 1")
    sample = doc.layer.image.getpixel((28, 16))
    assert sample[3] == 170
    assert sample[2] > sample[0] and abs(sample[0] - 20) <= 25
    assert doc.layer.image.getpixel((28, 16)) != doc.layer.image.getpixel((29, 16))
    assert doc.layer.image.getpixel((0, 0)) == original.getpixel((0, 0))
    assert doc.undo() and doc.layer.image.tobytes() == original.tobytes()


def test_perspective_clone_real_projective_mapping_and_selection():
    session = workspace()
    doc = session.document
    pattern(doc)
    source = doc.layer.image.copy()
    doc.select("rectangle", (23, 11), (29, 17))
    run(session, 'perspective-clone 22,12 30,12 --source-quad "2,2;10,2;10,10;2,10" --target-quad "22,12;30,12;30,20;22,20" --size 1 --hardness 1')
    assert doc.layer.image.getpixel((26, 12)) == source.getpixel((6, 2))
    assert doc.layer.image.getpixel((22, 12)) == source.getpixel((22, 12))
    coeff = perspective_coefficients([(0,0),(8,0),(8,8),(0,8)], [(20,8),(32,10),(29,23),(22,20)])
    assert any(abs(value) > .001 for value in coeff[-2:])


def test_smudge_moves_real_pigment_across_drag_and_preserves_zero_coverage():
    session = workspace(color=(0, 0, 255, 0))
    doc = session.document
    ImageDraw.Draw(doc.layer.image).rectangle((2, 10, 7, 16), fill="red")
    original = doc.layer.image.copy()
    run(session, "smudge 5,13 25,13 --size 5 --hardness 1 --strength 1")
    pixel = doc.layer.image.getpixel((15, 13))
    assert pixel[0] == 255 and pixel[2] == 0 and pixel[3] > 0
    assert doc.layer.image.getpixel((40, 20)) == (0, 0, 255, 0)
    assert doc.undo() and doc.layer.image.tobytes() == original.tobytes()


def test_smudge_mouse_segments_equivalent_to_one_continuous_stroke():
    one = workspace(color="white").document
    split = workspace(color="white").document
    ImageDraw.Draw(one.layer.image).rectangle((1, 10, 8, 18), fill="red")
    split.layer.image = one.layer.image.copy()
    # Exact equivalence for step-aligned straight segments.
    with one.edit("Smudge"):
        apply_stroke(one, "smudge", [(5,14),(10,14),(15,14)], size=5, hardness=1, strength=.8)
    state = {}
    with split.edit("Smudge"):
        apply_stroke(split, "smudge", [(5,14),(10,14)], size=5, hardness=1, strength=.8, state=state)
        apply_stroke(split, "smudge", [(5,14),(10,14),(15,14)], size=5, hardness=1, strength=.8, state=state)
    assert np.max(np.abs(np.asarray(one.layer.image, dtype=int) - np.asarray(split.layer.image, dtype=int))) <= 5


@pytest.mark.parametrize("kind", ["blur", "sharpen", "dodge", "burn"])
def test_retouch_local_changes_alpha_selection_lock_and_undo(kind):
    session = workspace(color=(70, 70, 70, 128))
    doc = session.document
    doc.layer.image.putpixel((24, 16), (200, 200, 200, 128))
    doc.select("rectangle", (22,14), (25,18))
    original = doc.layer.image.copy()
    run(session, f"retouch {kind} 24,16 --size 9 --hardness 1 --strength 1 --radius 1 --range all --exposure 1")
    assert doc.layer.image.getpixel((24,16)) != original.getpixel((24,16))
    assert doc.layer.image.getchannel("A").tobytes() == original.getchannel("A").tobytes()
    assert doc.layer.image.getpixel((27,16)) == original.getpixel((27,16))
    assert doc.undo() and doc.layer.image.tobytes() == original.tobytes()
    doc.layer.locked = True
    before = saved(doc)
    with pytest.raises(ValueError):
        run(session, f"retouch {kind} 24,16")
    assert saved(doc) == before


def test_tonal_ranges_act_on_different_luminance():
    a, b = workspace(color=(50,50,50,255)), workspace(color=(50,50,50,255))
    run(a, "retouch dodge 24,16 --size 7 --strength 1 --range shadows --exposure 1")
    run(b, "retouch dodge 24,16 --size 7 --strength 1 --range highlights --exposure 1")
    assert a.document.layer.image.getpixel((24,16))[0] > 50
    assert b.document.layer.image.getpixel((24,16))[0] == 50


def test_airbrush_duration_flow_and_single_two_pixel_tip():
    a, b = workspace(), workspace()
    run(a, "airbrush 20,12 --size 2 --duration .05 --rate 1 --color red --hardness 1")
    run(b, "airbrush 20,12 --size 2 --duration 2 --rate 1 --color red --hardness 1")
    assert 0 < a.document.layer.image.getpixel((20,12))[3] < b.document.layer.image.getpixel((20,12))[3] < 255
    assert b.document.layer.image.getpixel((20,12))[:3] == (255,0,0)


def test_calligraphy_nib_angle_aspect_pressure_and_continuous_coverage():
    a, b = workspace(), workspace()
    run(a, "ink 15,12 --size 15 --aspect .2 --angle 0 --color black --hardness 1")
    run(b, "ink 15,12 --size 15 --aspect .2 --angle 90 --color black --hardness 1")
    box_a, box_b = a.document.layer.image.getbbox(), b.document.layer.image.getbbox()
    assert box_a[2] - box_a[0] > box_a[3] - box_a[1]
    assert box_b[3] - box_b[1] > box_b[2] - box_b[0]
    c = workspace()
    run(c, "ink 5,12 40,12 --size 7 --aspect .4 --angle 0 --pressure .5 --color blue")
    assert all(c.document.layer.image.getpixel((x,12))[3] for x in range(5,41))


@pytest.mark.parametrize("preset", list(NATURAL_PRESETS))
def test_natural_native_brushes_are_real_textured_repeatable_masks(preset):
    a, b = workspace(), workspace()
    command = f"natural {preset} 8,10 35,20 --size 12 --seed 7 --color green --density 2"
    run(a, command)
    run(b, command)
    assert a.document.layer.image.getbbox() is not None
    assert a.document.layer.image.tobytes() == b.document.layer.image.tobytes()
    assert len(np.unique(np.asarray(a.document.layer.image.getchannel("A")))) > 2


def test_select_color_disconnected_rgba_feather_modes_and_history():
    session = workspace(color="blue")
    doc = session.document
    for point in ((4,4),(40,25)):
        doc.layer.image.putpixel(point, (255,0,0,255))
    doc.layer.image.putpixel((41,25), (255,0,0,128))
    run(session, "select-color 4 4 --tolerance 0")
    assert doc.selection.getpixel((4,4)) == doc.selection.getpixel((40,25)) == 255
    assert doc.selection.getpixel((41,25)) == doc.selection.getpixel((20,12)) == 0
    original = doc.selection.tobytes()
    run(session, "select-color --color blue --mode add --feather 1")
    assert doc.selection.getpixel((20,12)) == 255
    assert doc.undo() and doc.selection.tobytes() == original
    doc.layer.locked = True
    run(session, "select-color --color blue --mode subtract")
    assert doc.selection.getpixel((4,4)) == 255


def test_scissors_follows_visible_indented_edge_not_straight_polygon():
    doc = workspace((64,64), color="white").document
    ImageDraw.Draw(doc.layer.image).polygon([(12,12),(50,12),(50,25),(38,25),(38,40),(50,40),(50,52),(12,52)], fill="black")
    points = [(12,12),(50,12),(50,52),(12,52)]
    mask = scissors_selection(doc, points, resolution=256)
    assert mask.getpixel((25,30)) == 255
    assert mask.getpixel((46,31)) == 0
    assert mask.getpixel((3,3)) == 0
    session = CommandSession(doc)
    run(session, "scissors 12,12 50,12 50,52 12,52 --feather 1")
    assert doc.selection.getpixel((25,30)) == 255
    assert doc.undo() and doc.selection is None


def test_foreground_seed_classifies_subject_inside_outline_and_preserves_history():
    session = workspace((64,48), color="blue")
    doc = session.document
    ImageDraw.Draw(doc.layer.image).ellipse((18,8,45,38), fill="red")
    original = doc.layer.image.tobytes()
    run(session, 'foreground 10,4 52,4 52,43 10,43 --marks "30,22;30,12" --tolerance 30')
    assert doc.selection.getpixel((30,22)) == 255
    assert doc.selection.getpixel((12,22)) == doc.selection.getpixel((2,2)) == 0
    assert doc.layer.image.tobytes() == original
    assert doc.undo() and doc.selection is None


@pytest.mark.parametrize("command", [
    "clone 10,10", "clone 10,10 --source 90,90", "smudge 10,10",
    "retouch invalid 10,10", "retouch blur 10,10 --radius nan",
    "airbrush 10,10 --duration 0", "ink 10,10 --aspect 0",
    "natural nonexistent 10,10", "select-color 90 90",
    "scissors 1,1 2,2 90,90", "foreground 1,1 30,1 30,30 --marks 40,20",
    'perspective-clone 10,10 --source-quad "1,1;2,2;3,3;4,4" --target-quad "1,1;20,1;20,20;1,20"',
])
def test_invalid_operations_leave_complete_state_unchanged(command):
    session = workspace(color="white")
    before = saved(session.document)
    with pytest.raises(ValueError):
        run(session, command)
    assert saved(session.document) == before


def test_injected_midstroke_failure_rolls_back_pixels_and_history(monkeypatch):
    session = workspace(color="white")
    before = saved(session.document)
    original = session.document.layer.image.crop
    calls = 0
    def fail(box):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("Interrupted raster operation")
        return original(box)
    monkeypatch.setattr(session.document.layer.image, "crop", fail)
    with pytest.raises(RuntimeError):
        run(session, "smudge 5,5 40,5 --size 3")
    assert saved(session.document) == before


def test_large_canvas_local_retouch_uses_small_regions_not_canvas_sized_float_buffers(monkeypatch):
    doc = workspace((2048,2048), color=(100,100,100,170)).document
    original_crop = doc.layer.image.crop
    boxes = []
    def bounded(box):
        boxes.append(box)
        assert (box[2] - box[0]) * (box[3] - box[1]) < 20000
        return original_crop(box)
    monkeypatch.setattr(doc.layer.image, "crop", bounded)
    start = time.monotonic()
    apply_stroke(doc, "blur", [(1000,1000),(1020,1000)], size=15, radius=2)
    assert boxes and time.monotonic() - start < 2


def test_extreme_outside_long_stroke_rejected_without_partial_paint():
    doc = workspace().document
    before = doc.layer.image.tobytes()
    with pytest.raises(ValueError):
        apply_stroke(doc, "airbrush", [(-32768,-32768),(32768,32768)], size=512)
    assert doc.layer.image.tobytes() == before
