"""Full-resolution equivalence and bounded-work checks for interactive painting."""
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest
from textual.geometry import Size

from termatelier.canvas import Canvas
from termatelier.model import BLENDS, Document, Layer
from termatelier.tool_library import build_mask, get_tool


class UnitCanvas(Canvas):
    @property
    def app(self): return self._test_app

    @property
    def size(self): return getattr(self, "_test_size", Size(50, 20))


def canvas_for(doc, tool="brush", **settings):
    canvas = UnitCanvas()
    options = dict(doc=doc, tool=tool, config=None, brush_size=30, hardness=.5,
                   opacity=.43, foreground="#e793357b", filled=False,
                   update_status=lambda *args: None,
                   library_tool="natural-media.chalk.centered.coarse",
                   library_size=32, library_seed=7, library_angle=23, library_density=1.3)
    options.update(settings)
    canvas._test_app = SimpleNamespace(**options)
    canvas.base = doc.layer.image.copy()
    canvas.origin = (4, 8)
    return canvas


def painted_document(size=(129, 97)):
    doc = Document(*size)
    doc.layer.image = Image.new("RGBA", size, (30, 77, 99, 120))
    draw = ImageDraw.Draw(doc.layer.image)
    draw.rectangle((20, 11, 67, 48), fill=(70, 220, 14, 240))
    doc.selection = Image.linear_gradient("L").resize(size)
    return doc


@pytest.mark.parametrize("tool", ["brush", "pencil", "eraser"])
@pytest.mark.parametrize("size,hardness", [(1, 1), (7, 1), (7, .2), (30, .5), (43, .77), (64, 0)])
def test_incremental_stroke_matches_full_mask_at_every_point(tool, size, hardness):
    doc = painted_document()
    canvas = canvas_for(doc, tool, brush_size=size, hardness=hardness)
    expected = painted_document()
    base = expected.layer.image.copy()
    # Reversals, joins, overlaps, clipping and repeated points exercise stroke
    # opacity and the blur boundary at every corner of the actual canvas.
    points = [(4, 8), (32, 22), (67, 36), (40, 68), (7, 9), (-13, 16),
              (60, -8), (129, 3), (143, 86), (48, 105), (4, 8), (4, 8)]
    for point in points:
        canvas.points.append(point)
        canvas.update_gesture(point)
        mask = expected.stroke_mask(canvas.points, 1 if tool == "pencil" else size,
                                     1 if tool == "pencil" else hardness)
        expected.paint_mask(mask, canvas.app.foreground, canvas.app.opacity, tool == "eraser", base)
        assert doc.layer.image.tobytes() == expected.layer.image.tobytes()


@pytest.mark.parametrize("tool", ["line", "rectangle", "ellipse"])
@pytest.mark.parametrize("filled", [False, True])
def test_local_shape_preview_matches_full_canvas_when_shrunk_or_clipped(tool, filled):
    doc = painted_document()
    canvas = canvas_for(doc, tool, filled=filled, brush_size=11)
    expected = painted_document()
    base = expected.layer.image.copy()
    for point in [(120, 90), (31, 40), (-10, 20), (10, -25), (129, 98), (6, 9)]:
        canvas.points.append(point)
        canvas.update_gesture(point)
        expected.paint_mask(expected.shape_mask(tool, canvas.origin, point, 11, filled),
                            canvas.app.foreground, canvas.app.opacity, base=base)
        assert doc.layer.image.tobytes() == expected.layer.image.tobytes()


@pytest.mark.parametrize("tool_id", ["natural-media.chalk.centered.coarse",
    "natural-media.cloud.ring.fine", "botanical.heart.single.net",
    "geometric.star.radial.hatching", "tessellation.wave.diagonal.dotted",
    "textile.chevron.offset.knotted"])
def test_incremental_library_masks_preserve_recipes_seed_phase_and_opacity(tool_id):
    doc = painted_document()
    canvas = canvas_for(doc, "library", library_tool=tool_id)
    expected = painted_document()
    base = expected.layer.image.copy()
    spec = get_tool(tool_id)
    for point in [(4, 8), (55, 43), (119, 84), (18, 22), (-21, 33), (30, -21)]:
        canvas.points.append(point)
        canvas.update_gesture(point)
        box = (*canvas.origin, *point) if spec.kind == "pattern" else None
        mask = build_mask(spec, expected.size, points=None if box else canvas.points, box=box,
                          size=32, seed=7, angle=23, density=1.3)
        expected.paint_mask(mask, canvas.app.foreground, canvas.app.opacity, base=base)
        assert doc.layer.image.tobytes() == expected.layer.image.tobytes()


def test_large_brush_updates_only_local_patches_and_still_undoes(monkeypatch):
    doc = Document(2048, 2048)
    initial = doc.layer.image.tobytes()
    doc.begin("Large-canvas stroke")
    canvas = canvas_for(doc)
    canvas.base = doc.pending[1][1][doc.active].image
    boxes = []
    paint = doc.paint_mask
    def spy(mask, *args, **kwargs):
        box = kwargs.get("box")
        assert box is not None
        assert mask.size == (box[2]-box[0], box[3]-box[1])
        boxes.append(box)
        return paint(mask, *args, **kwargs)
    monkeypatch.setattr(doc, "paint_mask", spy)
    for point in [(600, 700), (607, 714), (621, 726), (637, 739)]:
        canvas.points.append(point)
        canvas.update_gesture(point)
    assert all((x1-x0)*(y1-y0) < 20_000 for x0, y0, x1, y1 in boxes)
    doc.commit()
    painted = doc.layer.image.tobytes()
    assert painted != initial
    assert len(doc.undo_stack) == 1
    assert doc.undo() and doc.layer.image.tobytes() == initial
    assert doc.redo() and doc.layer.image.tobytes() == painted


def test_fit_view_composites_once_then_reuses_minification_and_rendered_rows(monkeypatch):
    doc = Document(2048, 2048)
    doc.layers += [Layer(str(index), Image.new("RGBA", doc.size, (43, 77, 140, 55)),
                        opacity=.6, blend=BLENDS[index]) for index in range(2, 5)]
    canvas = canvas_for(doc)
    canvas.zoom = 40/2048
    # Coverage-preserving minification prepares the exact composite once.
    canvas.screen_image()
    def forbidden(*args, **kwargs):
        raise AssertionError("Warm viewport must not rebuild a full-canvas composite")
    monkeypatch.setattr(doc, "composite", forbidden)
    image = canvas.screen_image()
    assert image.size == (50, 40)
    canvas.cache = image
    row = canvas.render_line(10)
    assert canvas.render_line(10) is row
    canvas.invalidate(artwork=False)
    assert canvas.render_line(10) is not row


@pytest.mark.parametrize("tool", ["select_rect", "select_ellipse", "lasso", "gradient"])
@pytest.mark.parametrize("resampling", ["nearest", "bilinear", "bicubic"])
def test_contour_previews_match_previous_raster_sampling(tool, resampling):
    doc = painted_document()
    canvas = canvas_for(doc, tool)
    canvas.zoom, canvas.pan_x, canvas.pan_y = .8, 3.25, -2.75
    canvas.resampling = resampling
    canvas.points = [canvas.origin, (50, 62), (93, 31)]
    canvas.update_gesture(canvas.points[-1])
    actual = canvas.screen_image()
    source = doc.composite()
    draw = ImageDraw.Draw(source)
    end = canvas.points[-1]
    if tool == "lasso": draw.line(canvas.points, fill="#f4e285", width=1)
    elif tool == "gradient": draw.line((canvas.origin, end), fill="#f4e285", width=1)
    else:
        fn = draw.ellipse if tool == "select_ellipse" else draw.rectangle
        fn((*canvas.origin, *end), outline="#f4e285")
    # The compatibility RGBA preview branch shares checkerboard/guide/selection
    # overlays with the viewport renderer, isolating contour sampling behavior.
    canvas.preview = source
    expected = canvas.screen_image()
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("tool", ["brush", "eraser", "line", "rectangle", "ellipse", "library"])
def test_changed_tool_settings_rebuild_stroke_without_stale_edges(tool):
    doc = painted_document()
    canvas = canvas_for(doc, tool, brush_size=43)
    expected = painted_document()
    base = expected.layer.image.copy()
    canvas.points = [(4, 8), (48, 53), (90, 37)]
    for count in range(1, 4):
        canvas.points = [(4, 8), (48, 53), (90, 37)][:count]
        canvas.update_gesture(canvas.points[-1])
    # Shrink a wide soft stroke and change its ink without moving the pointer.
    canvas.app.brush_size = 7
    canvas.app.hardness = 1
    canvas.app.foreground = "#73ba9b5a"
    canvas.app.opacity = .8
    canvas.app.library_size = 15
    canvas.app.library_seed = 19
    canvas.update_gesture(canvas.points[-1])
    if tool in ("brush", "eraser"):
        mask = expected.stroke_mask(canvas.points, 7, 1)
    elif tool == "library":
        mask = build_mask(get_tool(canvas.app.library_tool), expected.size, points=canvas.points,
                          size=15, seed=19, angle=23, density=1.3)
    else:
        mask = expected.shape_mask(tool, canvas.origin, canvas.points[-1], 7, False)
    expected.paint_mask(mask, canvas.app.foreground, .8, tool == "eraser", base)
    assert doc.layer.image.tobytes() == expected.layer.image.tobytes()


@pytest.mark.parametrize("resampling", ["bilinear", "bicubic"])
def test_smooth_panning_and_zoom_reuse_exact_composite(monkeypatch, resampling):
    doc = painted_document((1024, 768))
    canvas = canvas_for(doc)
    canvas.resampling = resampling
    original = doc.composite
    source = original()
    calls = []
    def spy(box=None):
        calls.append(box)
        return original(box)
    monkeypatch.setattr(doc, "composite", spy)
    reference = canvas_for(doc)
    reference.preview = source
    reference.resampling = resampling
    for zoom, pan_x, pan_y in [(1, 0, 0), (.04, -17.4, -23.6), (2.4, 932.8, 739.4), (.23, 467.7, 99.25)]:
        canvas.zoom = reference.zoom = zoom
        canvas.pan_x = reference.pan_x = pan_x
        canvas.pan_y = reference.pan_y = pan_y
        canvas.invalidate(artwork=False)
        assert canvas.screen_image().tobytes() == reference.screen_image().tobytes()
    assert calls == [None]
    # Default invalidation safely handles external in-place pixel changes.
    doc.layer.image.putpixel((468, 100), (123, 22, 34, 234))
    canvas.invalidate()
    canvas.screen_image()
    assert calls == [None, None]


@pytest.mark.parametrize("tool", ["brush", "eraser", "line", "rectangle", "ellipse", "library"])
@pytest.mark.parametrize("resampling", ["bilinear", "bicubic"])
def test_smooth_cache_local_patch_matches_full_layer_composite(monkeypatch, tool, resampling):
    doc = painted_document((512, 384))
    for index, blend in enumerate(BLENDS[1:]):
        layer = Layer(blend, Image.new("RGBA", doc.size, (25+index*23, 177, 104, 37)),
                      opacity=.63, blend=blend)
        layer.mask = Image.linear_gradient("L").resize(doc.size)
        doc.layers.append(layer)
    canvas = canvas_for(doc, tool)
    canvas.resampling = resampling
    canvas.zoom, canvas.pan_x, canvas.pan_y = .6, -3.4, 8.9
    original = doc.composite
    calls = []
    def spy(box=None):
        calls.append(box)
        return original(box)
    monkeypatch.setattr(doc, "composite", spy)
    canvas.screen_image()
    reference = canvas_for(doc)
    reference.resampling = resampling
    reference.zoom, reference.pan_x, reference.pan_y = canvas.zoom, canvas.pan_x, canvas.pan_y
    for point in [(4, 8), (44, 59), (77, 35), (13, 17)]:
        canvas.points.append(point)
        canvas.update_gesture(point)
        reference.preview = original()
        assert canvas.screen_image().tobytes() == reference.screen_image().tobytes()
        assert canvas._smooth_composite.tobytes() == reference.preview.tobytes()
    assert calls[0] is None
    assert all(box is not None for box in calls[1:])
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_smooth_mouse_commit_retains_cache_and_detects_external_pixel_edits(monkeypatch):
    from termatelier.app import Studio
    from termatelier.config import RuntimeConfig
    config = RuntimeConfig()
    config.set("view.resampling", "bicubic", persist=False)
    app = Studio(Document(512, 384), config=config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        canvas, doc = app.canvas, app.doc
        canvas.screen_image()
        original = doc.composite
        calls = []
        def spy(box=None):
            calls.append(box)
            return original(box)
        monkeypatch.setattr(doc, "composite", spy)
        await pilot.mouse_down("#canvas", offset=(5, 5))
        await pilot.hover("#canvas", offset=(15, 8))
        await pilot.mouse_up("#canvas", offset=(15, 8))
        await pilot.pause()
        assert len(doc.undo_stack) == 1
        assert None not in calls
        assert canvas._smooth_source().tobytes() == original().tobytes()
        # Revision tracking catches edits that retain the same Image object.
        with doc.edit("External pixel operation"):
            doc.layer.image.putpixel((10, 10), (120, 220, 77, 240))
        canvas.invalidate(artwork=False)
        assert canvas._smooth_source().tobytes() == original().tobytes()
        assert calls.count(None) == 1


@pytest.mark.asyncio
async def test_pointer_processing_error_cancels_gesture_and_releases_capture(monkeypatch):
    from termatelier.app import Studio
    app = Studio(Document(512, 384))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        initial = app.doc.layer.image.tobytes()
        await pilot.mouse_down("#canvas", offset=(5, 5))
        assert app.canvas.dragging and app.doc.pending is not None
        def fail(*args): raise ValueError("Stroke exceeds its dab limit")
        monkeypatch.setattr(app.canvas, "update_gesture", fail)
        await pilot.hover("#canvas", offset=(8, 6))
        await pilot.pause()
        assert not app.canvas.dragging
        assert app.doc.pending is None and not app.doc.undo_stack
        assert app.doc.layer.image.tobytes() == initial
        assert app.mouse_captured is None
        await pilot.mouse_up("#canvas", offset=(8, 6))
