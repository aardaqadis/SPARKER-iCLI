"""Catalog integrity and pixel-level behavior, rather than identifier counts alone."""
import hashlib
import random

from PIL import Image, ImageDraw
import pytest

from termatelier.model import Document
from termatelier.tool_library import (
    TOOLS, CATEGORY_DESCRIPTIONS, apply_tool, build_mask, catalog_counts, get_tool,
    list_tools, render_tip,
)


def fixture_image(size=(47, 35)):
    rng = random.Random(21)
    image = Image.new("RGBA", size)
    values = []
    for y in range(size[1]):
        for x in range(size[0]):
            if x > size[0] // 2:
                base = (65, 140, 195) if (x // 4 + y // 4) % 2 else (170, 90, 40)
                if (x + 3 * y) % 17 == 0:
                    base = (210, 25, 110)
            else:
                base = ((x * 7 + y * 3) % 210 + 20, (x * 3 + y * 9) % 210 + 20,
                        (x * 11 + y * 2 + rng.randrange(13)) % 210 + 20)
            values.append((*base, (x * 11 + y * 17) % 256))
    image.putdata(values)
    return image


def test_catalog_is_structural_and_contains_over_a_thousand_executable_tools():
    assert len(TOOLS) >= 1001
    counts = catalog_counts()
    assert counts["total"] == 1248
    assert counts["kinds"] == {"stamp": 600, "brush": 200, "pattern": 400, "effect": 48}
    assert set(counts["categories"]) == set(CATEGORY_DESCRIPTIONS)
    assert len({tool.signature for tool in TOOLS.values()}) == len(TOOLS)
    assert len({tool.name for tool in TOOLS.values()}) == len(TOOLS)
    assert all(tool.id and tool.name and tool.description for tool in TOOLS.values())


def test_every_drawable_recipe_has_a_unique_nonempty_rendered_footprint():
    fingerprints = {}
    for tool in list_tools():
        if tool.kind == "effect":
            continue
        tip = render_tip(tool, 72)
        assert tip.mode == "L" and tip.size == (72, 72)
        assert tip.getbbox(), tool.id
        fingerprint = hashlib.sha256(tip.tobytes()).hexdigest()
        assert fingerprint not in fingerprints, (tool.id, fingerprints.get(fingerprint))
        fingerprints[fingerprint] = tool.id
    assert len(fingerprints) == 1200


def test_every_drawable_tool_executes_through_document_transactions():
    doc = Document(75, 73)
    for tool in list_tools():
        if tool.kind == "effect":
            continue
        assert not doc.layer.image.getbbox()
        result = apply_tool(doc, tool.id, points=[(37, 36)], size=64)
        assert result == tool.name
        assert doc.layer.image.getbbox(), tool.id
        assert len(doc.undo_stack) == 1
        assert doc.undo()
        assert not doc.layer.image.getbbox()


def test_search_categories_kinds_and_canonical_identifiers():
    found = list_tools("oak wreath", category="botanical", kind="stamp")
    assert len(found) == 4
    assert all("oak.wreath" in spec.id for spec in found)
    assert get_tool(found[0].id.upper()) == found[0]
    assert get_tool(found[0]) is found[0]
    assert list_tools("no-such-art-recipe") == []
    with pytest.raises(ValueError, match="Unknown library tool"):
        get_tool("missing")


@pytest.mark.parametrize("identifier", [spec.id for spec in list_tools(kind="effect")])
def test_every_effect_changes_pixels_preserving_dimensions_and_alpha(identifier):
    source = fixture_image()
    doc = Document(*source.size)
    doc.layer.image = source.copy()
    apply_tool(doc, identifier)
    assert doc.layer.image.size == source.size
    assert doc.layer.image.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert doc.layer.image.tobytes() != source.tobytes(), identifier
    assert len(doc.undo_stack) == 1
    assert doc.undo()
    assert doc.layer.image.tobytes() == source.tobytes()


def test_effect_recipes_produce_distinct_outputs():
    fingerprints = {}
    for spec in list_tools(kind="effect"):
        source = fixture_image()
        doc = Document(*source.size)
        doc.layer.image = source
        apply_tool(doc, spec.id)
        digest = hashlib.sha256(doc.layer.image.tobytes()).hexdigest()
        assert digest not in fingerprints, (spec.id, fingerprints.get(digest))
        fingerprints[digest] = spec.id
    assert len(fingerprints) == 48


def test_stamp_selection_opacity_and_shared_undo_redo():
    doc = Document(61, 43)
    selection = Image.new("L", doc.size)
    ImageDraw.Draw(selection).rectangle((0, 0, 30, 42), fill=255)
    doc.selection = selection
    before = doc.layer.image.tobytes()
    apply_tool(doc, "rosettes.heart.spiral.star", points=[(30, 20)], size=40, color="#ff0080", opacity=.5)
    assert doc.layer.image.crop((31, 0, 61, 43)).getbbox() is None
    assert 0 < doc.layer.image.getchannel("A").getextrema()[1] <= 128
    edited = doc.layer.image.tobytes()
    assert edited != before
    assert doc.undo() and doc.layer.image.tobytes() == before
    assert doc.redo() and doc.layer.image.tobytes() == edited


def test_pattern_box_is_inclusive_and_does_not_paint_outside_it():
    doc = Document(47, 35)
    apply_tool(doc, "tessellation.maze.pinwheel.solid", box=(7, 6, 25, 24), size=17)
    assert doc.layer.image.crop((0, 0, 7, 35)).getbbox() is None
    assert doc.layer.image.crop((26, 0, 47, 35)).getbbox() is None
    assert doc.layer.image.crop((0, 0, 47, 6)).getbbox() is None
    assert doc.layer.image.crop((0, 25, 47, 35)).getbbox() is None
    assert doc.layer.image.crop((7, 6, 26, 25)).getbbox()


def test_brush_interpolates_continuous_dabs_in_one_transaction():
    doc = Document(96, 40)
    apply_tool(doc, "natural-media.sponge.centered.coarse", points=[(12, 20), (82, 20)], size=20)
    mask = doc.layer.image.getchannel("A")
    assert mask.crop((42, 10, 52, 30)).getbbox()
    assert len(doc.undo_stack) == 1


def test_build_mask_is_pure_and_supports_live_stroke_cancel():
    doc = Document(51, 37)
    before = doc.layer.image.tobytes()
    doc.begin("Library mouse stroke")
    mask = build_mask("botanical.maple.single.net", doc.size, points=[(25, 18)], size=28)
    assert mask.mode == "L" and mask.size == doc.size
    assert doc.layer.image.tobytes() == before
    doc.paint_mask(mask, "#e79335")
    assert doc.layer.image.tobytes() != before
    doc.cancel()
    assert doc.layer.image.tobytes() == before and not doc.undo_stack


def test_apply_joins_an_existing_transaction_without_double_history():
    doc = Document(51, 37)
    doc.begin("A whole gesture")
    apply_tool(doc, "geometric.arrow.radial.cutouts", points=[(20, 17)], size=20)
    apply_tool(doc, "geometric.arrow.radial.cutouts", points=[(30, 17)], size=20)
    assert not doc.undo_stack
    doc.commit()
    assert len(doc.undo_stack) == 1


def test_effect_clips_to_box_and_selection_even_at_intensified_amount():
    source = fixture_image()
    doc = Document(*source.size)
    doc.layer.image = source.copy()
    doc.selection = Image.new("L", doc.size)
    ImageDraw.Draw(doc.selection).rectangle((0, 0, 23, 34), fill=255)
    apply_tool(doc, "effects.invert", box=(10, 10, 35, 25), amount=2.5)
    assert doc.layer.image.crop((24, 0, 47, 35)).tobytes() == source.crop((24, 0, 47, 35)).tobytes()
    assert doc.layer.image.crop((0, 0, 47, 10)).tobytes() == source.crop((0, 0, 47, 10)).tobytes()
    assert doc.layer.image.getchannel("A").tobytes() == source.getchannel("A").tobytes()


def test_seed_rotation_density_are_deterministic_and_change_brush_structure():
    spec = get_tool("natural-media.grass.slash.striated")
    first = render_tip(spec, 64, seed=14, angle=20, density=2)
    assert first.tobytes() == render_tip(spec, 64, seed=14, angle=20, density=2).tobytes()
    assert first.tobytes() != render_tip(spec, 64, seed=15, angle=20, density=2).tobytes()
    assert first.tobytes() != render_tip(spec, 64, seed=14, angle=0, density=2).tobytes()
    assert first.tobytes() != render_tip(spec, 64, seed=14, angle=20, density=.5).tobytes()


def test_off_canvas_large_boxes_clip_without_large_allocations():
    mask = build_mask("tessellation.brick.stepped.outline", (41, 31), box=(-999999,-999999,999999,999999), size=16)
    assert mask.size == (41, 31) and mask.getbbox()
    outside = build_mask("botanical.oval.single.midrib", (41, 31), box=(100,100,110,110))
    assert outside.getbbox() is None


def test_one_pixel_pattern_repeats_across_a_large_canvas():
    mask = build_mask("tessellation.brick.rows.solid", (1024, 1024), size=1)
    minimum, maximum = mask.getextrema()
    assert minimum == maximum and minimum > 0
    assert mask.getbbox() == (0, 0, 1024, 1024)


@pytest.mark.parametrize("kwargs", [{"size": 0}, {"size": 1025}, {"opacity": float("nan")},
    {"density": 0}, {"seed": float("inf")}, {"angle": float("nan")}, {"amount": -1},
    {"points": []}, {"points": [(1, 2, 3)]}, {"box": (1, 2, 3)}, {"color": "invalid-color"}])
def test_invalid_inputs_are_atomic(kwargs):
    doc = Document(31, 19)
    before = doc.layer.image.tobytes()
    with pytest.raises((ValueError, TypeError)):
        apply_tool(doc, "botanical.holly.whorl.dotted", **kwargs)
    assert doc.layer.image.tobytes() == before
    assert doc.pending is None and not doc.undo_stack


def test_locked_layer_rejects_all_tool_kinds_without_history():
    doc = Document()
    doc.layer.locked = True
    for kind in ("stamp", "brush", "pattern", "effect"):
        with pytest.raises(ValueError, match="locked"):
            apply_tool(doc, list_tools(kind=kind)[0].id)
    assert not doc.undo_stack


def test_effects_cannot_be_used_as_footprints():
    with pytest.raises(ValueError, match="do not have"):
        render_tip("effects.invert", 32)
    with pytest.raises(ValueError, match="do not have"):
        build_mask("effects.invert", (31, 19))
