"""Real capture behavior and coverage-preserving terminal brush previews."""
from types import SimpleNamespace

from PIL import Image
import pytest
from textual import events
from textual.geometry import Size

from termatelier.app import Studio
from termatelier.canvas import Canvas
from termatelier.model import BLENDS, Document, Layer


def mouse(canvas, event_type, x, y, button=1):
    return event_type(canvas, x, y, 0, 0, button, False, False, False,
                      screen_x=canvas.content_region.x+x, screen_y=canvas.content_region.y+y)


@pytest.mark.asyncio
async def test_repeated_down_coordinates_extend_one_live_captured_stroke():
    app = Studio(Document(256, 192))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.brush_size, app.hardness = 3, 1
        canvas = app.canvas
        canvas.on_mouse_down(mouse(canvas, events.MouseDown, 4, 4))
        canvas.on_mouse_down(mouse(canvas, events.MouseDown, 30, 10))
        canvas.on_mouse_down(mouse(canvas, events.MouseDown, 52, 3))
        # Coordinate reports must paint while held, retaining the curved path.
        assert app.doc.layer.image.getpixel((30, 20))[3] == 255
        assert canvas.points == [(4, 8), (30, 20), (52, 6)]
        assert app.doc.pending is not None and not app.doc.undo_stack
        canvas.on_mouse_up(mouse(canvas, events.MouseUp, 52, 3))
        assert len(app.doc.undo_stack) == 1
        assert not canvas.dragging and app.mouse_captured is None
        painted = app.doc.layer.image.tobytes()
        assert app.doc.undo()
        assert app.doc.layer.image.getchannel("A").getbbox() is None
        assert app.doc.redo() and app.doc.layer.image.tobytes() == painted


@pytest.mark.asyncio
async def test_unrelated_button_release_does_not_end_left_brush_drag():
    app = Studio(Document(256, 192))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        canvas = app.canvas
        canvas.on_mouse_down(mouse(canvas, events.MouseDown, 4, 4))
        canvas.on_mouse_up(mouse(canvas, events.MouseUp, 4, 4, button=3))
        assert canvas.dragging and app.doc.pending is not None
        canvas.on_mouse_move(mouse(canvas, events.MouseMove, 31, 10))
        canvas.on_mouse_up(mouse(canvas, events.MouseUp, 31, 10))
        assert len(app.doc.undo_stack) == 1 and not canvas.dragging


class PreviewCanvas(Canvas):
    @property
    def app(self): return self._test_app

    @property
    def size(self): return Size(80, 30)


def draw_preview(width=801, height=603, *, size=3, zoom=.055, resampling="nearest"):
    doc = Document(width, height)
    canvas = PreviewCanvas()
    canvas._test_app = SimpleNamespace(doc=doc, config=None, tool="brush", brush_size=size,
        hardness=.8, opacity=1, foreground="#e79335", update_status=lambda *args: None)
    canvas.zoom, canvas.resampling = zoom, resampling
    canvas.pan_y = max(0, 123-20/zoom)
    canvas.base = doc.layer.image.copy()
    canvas.origin = (20, 123)
    canvas.points = [canvas.origin, (780, 123)]
    canvas.update_gesture(canvas.points[-1])
    return canvas, doc


@pytest.mark.parametrize("size", [1, 3])
@pytest.mark.parametrize("zoom", [.025, .055, .125, .5])
def test_thin_native_strokes_remain_continuous_when_zoomed_out(size, zoom):
    canvas, doc = draw_preview(size=size, zoom=zoom)
    native = doc.layer.image.tobytes()
    image = canvas.screen_image()
    left = max(1, round(20*zoom)+1)
    right = min(image.width-1, round(780*zoom)-1)
    y = round((123-canvas.pan_y)*zoom)
    painted_columns = [x for x in range(left, right) if any(
        image.getpixel((x, row))[:3] != (255, 255, 255)
        for row in range(max(0, y-3), min(image.height, y+4)))]
    assert len(painted_columns) >= right-left-1
    assert doc.layer.image.tobytes() == native


@pytest.mark.parametrize("resampling", ["nearest", "bilinear", "bicubic"])
@pytest.mark.parametrize("transparent", [False, True])
def test_minified_dirty_tiles_match_full_alpha_aware_reduction_and_pan(monkeypatch, resampling, transparent):
    canvas, doc = draw_preview(resampling=resampling)
    doc.layer.image = Image.new("RGBA", doc.size, (89, 12, 145, 37))
    doc.layers[0].visible = not transparent
    canvas.base = doc.layer.image.copy()
    doc.selection = Image.linear_gradient("L").resize(doc.size)
    for index, blend in enumerate(BLENDS[1:]):
        layer = Layer(blend, Image.new("RGBA", doc.size, (36+index*17, 179, 94, 31)),
                      opacity=.49, blend=blend)
        layer.mask = Image.linear_gradient("L").resize(doc.size)
        doc.layers.append(layer)
    original = doc.composite
    calls = []
    def spy(box=None):
        calls.append(box)
        return original(box)
    monkeypatch.setattr(doc, "composite", spy)
    canvas.screen_image()
    reference = PreviewCanvas()
    reference._test_app = canvas.app
    reference.resampling = resampling
    for point, zoom, pan_x, pan_y in [((699, 309), .055, -3.1, 2.7),
            ((797, 599), .125, 344.9, 258.2), ((22, 16), .5, 663.3, 567.8),
            ((74, 393), .025, -7.2, -15.8), ((210, 59), .055, 15.75, 9.25)]:
        canvas.points.append(point)
        canvas.update_gesture(point)
        canvas.zoom, canvas.pan_x, canvas.pan_y = zoom, pan_x, pan_y
        reference.zoom, reference.pan_x, reference.pan_y = zoom, pan_x, pan_y
        reference.preview = original()
        assert canvas.screen_image().tobytes() == reference.screen_image().tobytes()
        assert canvas._minified.tobytes() == reference.preview.reduce(canvas._minified_factor).tobytes()
    # Only the cold frame may composite the full odd-sized native canvas.
    assert calls.count(None) == 1
    assert all(box is not None for box in calls[1:])


@pytest.mark.parametrize("tool", ["select_rect", "select_ellipse", "lasso", "gradient"])
@pytest.mark.parametrize("resampling", ["nearest", "bilinear", "bicubic"])
def test_zoomed_out_contours_match_full_native_preview_reduction(tool, resampling):
    from PIL import ImageDraw
    canvas, doc = draw_preview(resampling=resampling)
    canvas._test_app.tool = tool
    canvas.points = [(20, 23), (399, 437), (745, 315)]
    canvas.origin = canvas.points[0]
    canvas.zoom, canvas.pan_x, canvas.pan_y = .055, 11.7, -3.2
    canvas.update_gesture(canvas.points[-1])
    before = doc.layer.image.tobytes()
    actual = canvas.screen_image()
    source = doc.composite()
    draw = ImageDraw.Draw(source)
    if tool == "lasso": draw.line(canvas.points, fill="#f4e285", width=1)
    elif tool == "gradient": draw.line((canvas.origin, canvas.points[-1]), fill="#f4e285", width=1)
    else:
        fn = draw.ellipse if tool == "select_ellipse" else draw.rectangle
        fn((*canvas.origin, *canvas.points[-1]), outline="#f4e285")
    canvas.preview = source
    assert actual.tobytes() == canvas.screen_image().tobytes()
    assert doc.layer.image.tobytes() == before


def test_area_preview_keeps_native_stroke_and_png_export_exact(tmp_path):
    from termatelier.storage import export
    canvas, doc = draw_preview()
    expected = Document(*doc.size)
    expected.paint_mask(expected.stroke_mask(canvas.points, 3, .8), "#e79335")
    native = doc.layer.image.tobytes()
    for zoom in (.025, .055, .125, .5, 1, 4):
        canvas.zoom = zoom
        canvas.screen_image()
        assert doc.layer.image.tobytes() == native == expected.layer.image.tobytes()
    path = tmp_path / "external-export" / "native.png"
    export(doc, path)
    with Image.open(path) as reopened:
        assert reopened.size == doc.size
        assert reopened.convert("RGBA").tobytes() == expected.composite().tobytes()
