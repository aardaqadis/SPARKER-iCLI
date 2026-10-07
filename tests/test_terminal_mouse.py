"""Real terminal mouse reports pass through the parser, driver and painter."""
import math

import pytest
from PIL import Image, ImageChops
from textual._xterm_parser import XTermParser

from termatelier.app import Studio
from termatelier.model import Document


async def report(app, pilot, parser, code, cell, state="M"):
    # SGR coordinates are one-based screen cells, unlike Pilot's local offsets.
    region = app.canvas.region
    wire = f"\x1b[<{code};{region.x + cell[0] + 1};{region.y + cell[1] + 1}{state}"
    for event in parser.feed(wire):
        app._driver.process_message(event)
    await pilot.pause()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["brush", "pencil", "eraser"])
@pytest.mark.parametrize("large", [False, True])
async def test_sgr_drag_connects_sparse_samples_and_hover_never_paints(tool, large):
    doc = Document(1024, 768) if large else Document(96, 64)
    if tool == "eraser":
        doc.layer.image = Image.new("RGBA", doc.size, (231, 147, 53, 180))
    original = doc.layer.image.copy()
    app = Studio(doc)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_tool(tool)
        if not large:
            app.action_actual()
        app.brush_size, app.hardness, app.opacity = 3, .8, .43
        parser = XTermParser()
        cells = [(5, 3), (25, 4), (12, 14), (42, 15)]
        expected_points = [(math.floor(app.canvas.pan_x + x / app.canvas.zoom),
                            math.floor(app.canvas.pan_y + y * 2 / app.canvas.zoom))
                           for x, y in cells]
        await report(app, pilot, parser, 35, (2, 2))  # genuine no-button hover
        assert doc.layer.image.tobytes() == original.tobytes()
        await report(app, pilot, parser, 0, cells[0])
        for cell in cells[1:]:
            await report(app, pilot, parser, 32, cell)  # left button held
            assert app.canvas.dragging and doc.pending is not None
        assert app.canvas.points == expected_points
        await report(app, pilot, parser, 0, cells[-1], "m")
        assert not app.canvas.dragging and doc.pending is None
        assert len(doc.undo_stack) == 1 and app.mouse_captured is None
        mask = doc.stroke_mask(expected_points, 1 if tool == "pencil" else 3,
                               1 if tool == "pencil" else .8)
        mask = mask.point(lambda value: round(value * .43))
        if tool == "eraser":
            expected = original.copy()
            expected.putalpha(ImageChops.multiply(original.getchannel("A"), ImageChops.invert(mask)))
        else:
            ink = Image.new("RGBA", doc.size, app.foreground)
            ink.putalpha(mask)
            expected = Image.alpha_composite(original, ink)
        assert doc.layer.image.tobytes() == expected.tobytes()
        painted = doc.layer.image.tobytes()
        await report(app, pilot, parser, 35, (44, 16))
        assert doc.layer.image.tobytes() == painted and len(doc.undo_stack) == 1
        assert doc.undo() and doc.layer.image.tobytes() == original.tobytes()
        assert doc.redo() and doc.layer.image.tobytes() == painted


@pytest.mark.asyncio
async def test_sgr_right_drag_pans_without_painting():
    app = Studio(Document(1024, 768))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        initial = app.doc.layer.image.tobytes()
        zoom = app.canvas.zoom
        parser = XTermParser()
        await report(app, pilot, parser, 2, (12, 8))
        await report(app, pilot, parser, 34, (24, 13))
        await report(app, pilot, parser, 2, (24, 13), "m")
        assert app.canvas.pan_x == pytest.approx(-12 / zoom)
        assert app.canvas.pan_y == pytest.approx(-10 / zoom)
        assert not app.canvas.dragging and app.mouse_captured is None
        assert app.doc.layer.image.tobytes() == initial and not app.doc.undo_stack


@pytest.mark.asyncio
async def test_sgr_repeated_press_coordinates_keep_live_path_and_single_undo():
    app = Studio(Document(256, 192))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.brush_size, app.hardness = 3, 1
        parser = XTermParser()
        cells = [(5, 3), (16, 12), (33, 5)]
        for cell in cells:
            await report(app, pilot, parser, 0, cell)
            assert app.canvas.dragging and app.doc.pending is not None
        points = [(x, y * 2) for x, y in cells]
        assert app.canvas.points == points
        assert app.doc.layer.image.getpixel(points[1])[3] == 255
        await report(app, pilot, parser, 0, cells[-1], "m")
        painted = app.doc.layer.image.tobytes()
        # Textual may emit a duplicate synthetic release on the next hover.
        await report(app, pilot, parser, 35, (38, 14))
        assert app.doc.layer.image.tobytes() == painted
        assert len(app.doc.undo_stack) == 1 and app.doc.pending is None
        assert not app.canvas.dragging and app.mouse_captured is None


@pytest.mark.asyncio
async def test_sgr_unrelated_button_release_keeps_left_stroke_captured():
    app = Studio(Document(256, 192))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        parser = XTermParser()
        await report(app, pilot, parser, 0, (5, 3))
        await report(app, pilot, parser, 2, (5, 3))
        await report(app, pilot, parser, 2, (5, 3), "m")
        assert app.canvas.dragging and app.doc.pending is not None
        await report(app, pilot, parser, 32, (25, 12))
        await report(app, pilot, parser, 0, (25, 12), "m")
        assert app.doc.layer.image.getpixel((25, 24))[3] > 0
        assert len(app.doc.undo_stack) == 1 and app.mouse_captured is None
