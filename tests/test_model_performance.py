"""Exact pixel checks for viewport-sized rendering and local gesture updates."""
from random import Random
from collections import deque
import math

import pytest
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageOps

from termatelier import model
from termatelier.model import BLENDS, Document, Layer


def random_image(mode, size, seed):
    random = Random(seed)
    bands = 4 if mode == "RGBA" else 1
    return Image.frombytes(mode, size, bytes(random.randrange(256) for _ in range(size[0] * size[1] * bands)))


def reference_rendered(layer):
    image = layer.image.copy()
    alpha = image.getchannel("A")
    if layer.mask is not None:
        alpha = ImageChops.multiply(alpha, layer.mask)
    image.putalpha(alpha.point(lambda value: round(value * layer.opacity)))
    return image


def reference_composite(doc):
    result = Image.new("RGBA", doc.size)
    for layer in doc.layers:
        if layer.visible:
            result = model.composite_layer(result, reference_rendered(layer), layer.blend)
    return result


def reference_paint(base, mask, color, opacity, erase, selection=None):
    if selection is not None:
        mask = ImageChops.multiply(mask, selection)
    mask = mask.point(lambda value: round(value * max(0, min(1, opacity))))
    if erase:
        result = base.copy()
        result.putalpha(ImageChops.multiply(base.getchannel("A"), ImageOps.invert(mask)))
        return result
    ink = Image.new("RGBA", base.size, color)
    ink.putalpha(ImageChops.multiply(ink.getchannel("A"), mask))
    return Image.alpha_composite(base, ink)


def varied_document(mode="normal"):
    doc = Document(29, 23)
    doc.layers = [Layer("Back", random_image("RGBA", doc.size, 10)),
                  Layer("Front", random_image("RGBA", doc.size, 20), opacity=.43, blend=mode,
                        mask=random_image("L", doc.size, 30)),
                  Layer("Hidden", random_image("RGBA", doc.size, 40), visible=False),
                  Layer("Top", random_image("RGBA", doc.size, 50), opacity=.8)]
    doc.active = 1
    return doc


@pytest.mark.parametrize("mode", BLENDS)
def test_region_composite_matches_full_pixels_for_all_blends(mode):
    doc = varied_document(mode)
    expected = reference_composite(doc)
    assert doc.composite().tobytes() == expected.tobytes()
    for box in ((3, 4, 18, 20), (0, 0, 1, 1), (-5, -4, 12, 9), (24, 19, 35, 30)):
        actual = doc.composite(box)
        assert actual.size == (box[2] - box[0], box[3] - box[1])
        assert actual.tobytes() == expected.crop(box).tobytes()


@pytest.mark.parametrize("mode", BLENDS)
def test_nearest_view_composite_matches_full_render_at_pan_fit_zoom_and_edges(mode):
    doc = varied_document(mode)
    expected = reference_composite(doc)
    for size, affine in (((17, 13), (1, 0, 7, 0, 1, 5)),
                         ((11, 9), (29 / 11, 0, 0, 0, 23 / 9, 0)),
                         ((37, 29), (.5, 0, -4.3, 0, .5, -3.1)),
                         ((21, 15), (.78, 0, 19.2, 0, 1.2, 15.8))):
        actual = doc.composite_view(size, affine)
        sampled = expected.transform(size, Image.Transform.AFFINE, affine, Image.Resampling.NEAREST)
        assert actual.tobytes() == sampled.tobytes()


@pytest.mark.parametrize("resampling", (Image.Resampling.BILINEAR, Image.Resampling.BICUBIC))
def test_smooth_view_sampling_keeps_full_composite_interpolation(resampling):
    doc = varied_document("overlay")
    size, affine = (18, 13), (1.4, 0, -3.2, 0, .9, 2.7)
    expected = reference_composite(doc).transform(size, Image.Transform.AFFINE, affine, resampling)
    assert doc.composite_view(size, affine, resampling).tobytes() == expected.tobytes()


def test_rendered_public_result_remains_independent_and_internal_borrow_is_safe():
    layer = Layer("Plain", random_image("RGBA", (8, 9), 7))
    expected = layer.image.tobytes()
    rendered = layer.rendered()
    rendered.putpixel((2, 3), (0, 0, 0, 0))
    assert layer.image.tobytes() == expected
    assert layer.rendered(copy=False) is layer.image
    assert layer.rendered((2, 3, 5, 7)).tobytes() == layer.image.crop((2, 3, 5, 7)).tobytes()


@pytest.mark.parametrize("opacity", (0, .37, 1, 2))
@pytest.mark.parametrize("erase", (False, True))
@pytest.mark.parametrize("selected", (False, True))
def test_cropped_full_mask_paint_preserves_rgba_selection_and_base_semantics(opacity, erase, selected):
    doc = Document(29, 23)
    base = random_image("RGBA", doc.size, 65)
    doc.layer.image = random_image("RGBA", doc.size, 88)
    if selected:
        doc.selection = random_image("L", doc.size, 12)
    mask = Image.new("L", doc.size)
    mask.paste(random_image("L", (8, 7), 24), (5, 9))
    expected = reference_paint(base, mask, "#e7933589", opacity, erase, doc.selection)
    before = base.tobytes()
    doc.paint_mask(mask, "#e7933589", opacity, erase, base)
    assert doc.layer.image.tobytes() == expected.tobytes()
    assert base.tobytes() == before


@pytest.mark.parametrize("erase", (False, True))
@pytest.mark.parametrize("selected", (False, True))
def test_canvas_wide_mask_fast_path_preserves_exact_pixels_and_immutable_base(erase, selected):
    doc = Document(29, 23)
    base = random_image("RGBA", doc.size, 95)
    doc.layer.image = random_image("RGBA", doc.size, 82)
    mask = random_image("L", doc.size, 34)
    mask.putpixel((0, 0), 255)
    mask.putpixel((28, 22), 255)
    if selected:
        doc.selection = random_image("L", doc.size, 46)
    before = base.tobytes()
    expected = reference_paint(base, mask, "#e7933589", .37, erase, doc.selection)
    doc.paint_mask(mask, "#e7933589", .37, erase, base)
    assert doc.layer.image.tobytes() == expected.tobytes()
    assert base.tobytes() == before


@pytest.mark.parametrize("erase", (False, True))
def test_local_mask_updates_only_region_without_accumulating_stroke_opacity(erase):
    doc = Document(31, 24)
    doc.layer.image = random_image("RGBA", doc.size, 44)
    base = doc.layer.image.copy()
    doc.selection = random_image("L", doc.size, 81)
    box = (9, 8, 18, 15)
    mask = random_image("L", (9, 7), 57)
    full_mask = Image.new("L", doc.size)
    full_mask.paste(mask, box[:2])
    expected = reference_paint(base, full_mask, "#37a4d98b", .42, erase, doc.selection)
    for _ in range(3):
        doc.paint_mask(mask, "#37a4d98b", .42, erase, base, box=box)
        assert doc.layer.image.tobytes() == expected.tobytes()


@pytest.mark.parametrize("erase", (False, True))
def test_local_region_handles_canvas_edges_and_padding(erase):
    doc = Document(21, 17)
    doc.layer.image = random_image("RGBA", doc.size, 88)
    base = doc.layer.image.copy()
    for box in ((-3, -2, 5, 4), (18, 14, 26, 20)):
        mask = random_image("L", (8, 6), 33)
        full_mask = Image.new("L", doc.size)
        full_mask.paste(mask, box[:2])
        expected = reference_paint(base, full_mask, "#ec9417", .6, erase)
        doc.layer.image = base.copy()
        doc.paint_mask(mask, "#ec9417", .6, erase, base, box=box)
        assert doc.layer.image.tobytes() == expected.tobytes()


def test_empty_full_mask_restores_base_and_invalid_mask_is_rejected():
    doc = Document(12, 10)
    base = random_image("RGBA", doc.size, 72)
    doc.paint_mask(Image.new("L", doc.size), "red", base=base)
    assert doc.layer.image.tobytes() == base.tobytes()
    with pytest.raises(ValueError):
        doc.paint_mask(Image.new("L", (2, 2)), "red")
    with pytest.raises(ValueError):
        doc.paint_mask(Image.new("RGB", doc.size), "red")
    with pytest.raises(ValueError):
        doc.paint_mask(Image.new("L", (2, 2)), "red", box=(0, 0, 3, 4))


def test_large_view_and_local_paint_process_only_requested_pixels(monkeypatch):
    doc = Document(2048, 2048)
    doc.add_layer("Blend", Image.new("RGBA", doc.size, (90, 130, 200, 180)))
    doc.layer.opacity, doc.layer.blend = .6, "multiply"
    doc.layer.mask = Image.new("L", doc.size, 200)
    seen = []
    original = model.composite_layer
    def measured_composite(back, front, blend="normal"):
        seen.append((back.size, front.size))
        return original(back, front, blend)
    monkeypatch.setattr(model, "composite_layer", measured_composite)
    view = doc.composite_view((160, 100), (12.8, 0, 0, 0, 20.48, 0))
    assert view.size == (160, 100)
    assert seen and all(back == front == (160, 100) for back, front in seen)

    base = doc.layer.image.copy()
    original_alpha = Image.alpha_composite
    painted_sizes = []
    def measured_paint(back, front):
        painted_sizes.append((back.size, front.size))
        return original_alpha(back, front)
    monkeypatch.setattr(Image, "alpha_composite", measured_paint)
    original_copy = Image.Image.copy
    def forbid_canvas_copy(image):
        assert image.size != doc.size, "Local painting copied the entire canvas"
        return original_copy(image)
    monkeypatch.setattr(Image.Image, "copy", forbid_canvas_copy)
    doc.paint_mask(Image.new("L", (9, 7), 255), "#e79335", .5, base=base,
                   box=(1024, 1024, 1033, 1031))
    assert painted_sizes == [((9, 7), (9, 7))]
    assert base.getpixel((1025, 1025)) == (90, 130, 200, 180)
    assert doc.layer.image.getpixel((0, 0)) == base.getpixel((0, 0))


def test_local_paint_transaction_undo_redo_and_cancel_restore_exact_document():
    doc = varied_document("screen")
    doc.selection = random_image("L", doc.size, 64)
    before = doc.snapshot()
    before_pixels = doc.composite().tobytes()
    with doc.edit("Local stroke"):
        base = doc.layer.image.copy()
        for box in ((5, 4, 14, 11), (9, 7, 18, 14)):
            doc.paint_mask(Image.new("L", (9, 7), 180), "#e79335", .63, base=base, box=box)
    after = doc.composite().tobytes()
    assert after != before_pixels and doc.undo()
    assert doc.composite().tobytes() == before_pixels
    assert doc.layer.mask.tobytes() == before[1][doc.active].mask.tobytes()
    assert doc.redo() and doc.composite().tobytes() == after
    doc.begin("Cancelled local stroke")
    doc.paint_mask(Image.new("L", (3, 3), 255), "blue", box=(10, 10, 13, 13))
    doc.cancel()
    assert doc.composite().tobytes() == after


def reference_region(doc, point, tolerance=0, sample_merged=False):
    mask = Image.new("L", doc.size)
    x, y = point
    if not (0 <= x < doc.width and 0 <= y < doc.height):
        return mask
    source = reference_composite(doc) if sample_merged else doc.layer.image
    pixels, out = source.load(), mask.load()
    target = pixels[x, y]
    seen = set()
    queue = deque([point])
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen:
            continue
        seen.add((x, y))
        if max(abs(a-b) for a, b in zip(pixels[x, y], target)) > tolerance:
            continue
        if doc.selection is not None and doc.selection.getpixel((x, y)) == 0:
            continue
        out[x, y] = 255
        queue.extend((a, b) for a, b in ((x-1, y), (x+1, y), (x, y-1), (x, y+1))
                     if 0 <= a < doc.width and 0 <= b < doc.height)
    return mask


@pytest.mark.parametrize("tolerance", (0, 7, 30.5, 255, -1))
@pytest.mark.parametrize("sample_merged", (False, True))
@pytest.mark.parametrize("selected", (False, True))
def test_span_fill_matches_four_connected_rgba_reference(tolerance, sample_merged, selected):
    random = Random(72)
    doc = Document(33, 27)
    colors = ((100, 80, 30, 0), (106, 81, 27, 6), (100, 80, 30, 255),
              (130, 92, 50, 18), (0, 0, 0, 0))
    pixels = bytes(value for _ in range(doc.width * doc.height)
                   for value in colors[random.randrange(len(colors))])
    doc.layer.image = Image.frombytes("RGBA", doc.size, pixels)
    ImageDraw.Draw(doc.layer.image).rectangle((7, 6, 24, 22), fill=colors[0])
    ImageDraw.Draw(doc.layer.image).rectangle((11, 9, 19, 19), outline=colors[2], width=1)
    ImageDraw.Draw(doc.layer.image).line((7, 2, 7, 23), fill=colors[1], width=1)
    if selected:
        doc.selection = Image.new("L", doc.size, 130)
        ImageDraw.Draw(doc.selection).line((0, 13, 32, 13), fill=0)
        ImageDraw.Draw(doc.selection).rectangle((15, 15, 17, 18), fill=0)
    for point in ((0, 0), (7, 6), (13, 13), (15, 16), (32, 26), (-1, 4), (33, 7)):
        expected = reference_region(doc, point, tolerance, sample_merged)
        assert doc.region_mask(point, tolerance, sample_merged).tobytes() == expected.tobytes()


def test_fill_never_crosses_diagonal_connections_or_selection_holes():
    doc = Document(9, 9)
    doc.layer.image = Image.new("RGBA", doc.size, "black")
    for i in range(9):
        doc.layer.image.putpixel((i, i), (255, 255, 255, 255))
    assert doc.region_mask((4, 4)).getbbox() == (4, 4, 5, 5)
    doc.selection = Image.new("L", doc.size)
    ImageDraw.Draw(doc.selection).rectangle((1, 1, 7, 7), fill=1)
    doc.selection.putpixel((4, 4), 0)
    assert doc.region_mask((4, 4), 255).getbbox() is None


def test_large_uniform_fill_queues_rows_instead_of_individual_pixels(monkeypatch):
    doc = Document(1024, 1024)
    appended = []
    class MeasuredQueue(deque):
        def append(self, value):
            appended.append(value)
            super().append(value)
    monkeypatch.setattr(model, "deque", MeasuredQueue)
    result = doc.region_mask((512, 512))
    assert result.size == doc.size and result.getextrema() == (255, 255)
    assert len(appended) < 2 * doc.height


def reference_gradient(doc, start, end, foreground, background, opacity=1, radial=False):
    a, b = ImageColor.getcolor(foreground, "RGBA"), ImageColor.getcolor(background, "RGBA")
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = max(1, dx*dx + dy*dy)
    ink = Image.new("RGBA", doc.size)
    pixels = ink.load()
    for y in range(doc.height):
        for x in range(doc.width):
            t = (math.hypot(x-start[0], y-start[1]) / math.sqrt(length) if radial
                 else ((x-start[0])*dx + (y-start[1])*dy) / length)
            t = max(0, min(1, t))
            pixels[x, y] = tuple(round(v*(1-t) + w*t) for v, w in zip(a, b))
    mask = Image.new("L", doc.size, round(255 * max(0, min(1, opacity))))
    if doc.selection is not None:
        mask = ImageChops.multiply(mask, doc.selection)
    ink.putalpha(ImageChops.multiply(ink.getchannel("A"), mask))
    return Image.alpha_composite(doc.layer.image, ink)


@pytest.mark.parametrize("start,end,radial", (((0, 0), (28, 0), False),
                       ((23, 12), (4, 12), False), ((5, -3), (5, 22), False),
                       ((7, 20), (7, 3), False), ((4, 9), (4, 9), False),
                       ((.5, 7.3), (20.8, 7.3), False), ((2, 4), (26, 20), False),
                       ((3, 5), (19, 17), True)))
@pytest.mark.parametrize("selected", (False, True))
def test_gradient_fast_rows_preserve_exact_original_interpolation(start, end, radial, selected):
    doc = Document(29, 23)
    doc.layer.image = random_image("RGBA", doc.size, 78)
    if selected:
        doc.selection = random_image("L", doc.size, 25)
    expected = reference_gradient(doc, start, end, "#e7933540", "#5a8deefa", .73, radial)
    doc.gradient(start, end, "#e7933540", "#5a8deefa", .73, radial)
    assert doc.layer.image.tobytes() == expected.tobytes()
