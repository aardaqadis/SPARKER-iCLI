"""Exports preserve every full-resolution pixel independent of terminal zoom."""
import math

from PIL import Image, ImageDraw
import pytest

from termatelier.model import Document, Layer
from termatelier.commands import CommandSession
from termatelier.rendering import ExportView, reduction_factor, sample_rgba
from termatelier.storage import export, load_project, save_project


def artwork(size):
    """Fine outlines, gradients and transparency expose sampling differences."""
    image = Image.new("RGBA", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = ((x * 7 + y) % 256, (y * 11 + x) % 256,
                            (x + y * 5) % 256, (x * 3 + y * 13) % 256)
    draw = ImageDraw.Draw(image)
    draw.line((0, 2, size[0] - 1, size[1] - 3), fill=(255, 255, 255, 255), width=1)
    draw.rectangle((3, 4, size[0] - 5, size[1] - 6), outline=(14, 18, 27, 255), width=1)
    return image


def previous_canvas_raster(image, size, zoom, mode, pan=(0, 0)):
    factor = max(1, math.ceil(1 / zoom))
    reduced = image.reduce(factor)
    affine = tuple(value / factor for value in
                   (1 / zoom, 0, pan[0], 0, 1 / zoom, pan[1]))
    filters = {"nearest": Image.Resampling.NEAREST,
               "bilinear": Image.Resampling.BILINEAR,
               "bicubic": Image.Resampling.BICUBIC}
    return reduced.transform(size, Image.Transform.AFFINE, affine, filters[mode])


def rgba_colors(image):
    raw = image.tobytes()
    return {raw[index:index + 4] for index in range(0, len(raw), 4)}


@pytest.mark.parametrize("zoom,factor", [(0.13125, 8), (.5, 2), (.8, 2), (1, 1), (3, 1)])
def test_reduction_factor_retains_terminal_coverage(zoom, factor):
    assert reduction_factor(zoom) == factor


@pytest.mark.parametrize("mode", ["nearest", "bilinear", "bicubic"])
@pytest.mark.parametrize("pan", [(0, 0), (3.25, -2.75)])
def test_shared_sampler_matches_existing_canvas_affine(mode, pan):
    image, zoom, size = artwork((83, 61)), .28, (31, 24)
    factor = reduction_factor(zoom)
    expected = previous_canvas_raster(image, size, zoom, mode, pan)
    actual = sample_rgba(image.reduce(factor), size, zoom, factor, pan, mode)
    assert actual.mode == "RGBA" and actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("size", [(31, 19), (96, 64)])
def test_small_exports_preserve_every_pixel_including_invisible_rgb(tmp_path, size):
    image = artwork(size)
    image.putpixel((1, 1), (78, 19, 212, 0))
    doc = Document(*size)
    doc.layers = [Layer("Pixels", image)]
    doc.active = 0
    exported = export(doc, tmp_path / "small.png")
    with Image.open(exported) as restored:
        assert restored.size == size and restored.mode == "RGBA"
        assert restored.tobytes() == doc.composite().tobytes()


@pytest.mark.parametrize("size,zoom", [((960, 640), 126 / 960), ((131, 99), .31)])
@pytest.mark.parametrize("mode", ["nearest", "bilinear", "bicubic"])
def test_native_image_retains_fine_pixels_that_zoomed_out_terminal_combines(tmp_path, size, zoom, mode):
    image = artwork(size)
    before = image.tobytes()
    logical = tuple(math.ceil(dimension * zoom) for dimension in size)
    cells = previous_canvas_raster(image, logical, zoom, mode)
    doc = Document(*size)
    doc.layers = [Layer("Fine detail", image)]
    doc.active = 0
    doc.export_view = ExportView(zoom, mode)
    original = doc.composite()
    result = export(doc, tmp_path / "details.png")
    with Image.open(result) as exported:
        assert exported.size == size and exported.mode == "RGBA"
        assert exported.tobytes() == original.tobytes()
        # Looking at a 1:1 crop reveals all original pixels, including a
        # one-pixel white outline that a zoomed-out preview averages away.
        box = (1, 1, 28, 23)
        assert exported.crop(box).tobytes() == original.crop(box).tobytes()
        assert exported.tobytes() != cells.resize(size, Image.Resampling.NEAREST).tobytes()
    assert image.tobytes() == before


@pytest.mark.parametrize("suffix", ["png", "webp", "tiff", "jpg", "gif", "bmp"])
def test_program_exports_follow_current_canvas_size_after_resize_and_undo(tmp_path, suffix):
    session = CommandSession(Document(96, 64))
    session.execute("pencil 3,3 89,53 --color orange")
    session.document.export_view = ExportView(.073)
    for step, command, size in [("canvas", "canvas 257x173", (257, 173)),
                                ("resize", "resize 419x277", (419, 277)),
                                ("undo", "undo", (257, 173))]:
        session.execute(command)
        original = session.document.composite().tobytes()
        path = tmp_path / f"{step}.{suffix}"
        # Text column settings and screen sampling do not set image dimensions.
        session.execute(f'export "{path}" --columns 11 --allow-lossy')
        with Image.open(path) as saved:
            assert saved.size == session.document.size == size
        assert session.document.composite().tobytes() == original


def test_export_rejects_a_non_canvas_composite_before_writing(tmp_path, monkeypatch):
    doc = Document(131, 99)
    target = tmp_path / "painting.png"
    monkeypatch.setattr(doc, "composite", lambda: Image.new("RGBA", (262, 198)))
    with pytest.raises(ValueError, match="original canvas dimensions"):
        export(doc, target)
    assert not target.exists()


@pytest.mark.parametrize("size,zoom", [((803, 603), .055), ((100, 100), .263)])
@pytest.mark.parametrize("mode", ["nearest", "bilinear", "bicubic"])
@pytest.mark.parametrize("alpha", [128, 255])
def test_canvas_edges_and_transparency_are_pixel_exact_despite_partial_preview_cells(tmp_path, size, zoom, mode, alpha):
    image = artwork(size)
    image.putalpha(alpha)
    doc = Document(*size)
    doc.layers = [Layer("Pixels", image)]
    doc.active = 0
    doc.export_view = ExportView(zoom, mode)
    original = doc.composite()
    with Image.open(export(doc, tmp_path / "edges.png")) as actual:
        assert actual.size == size
        assert actual.tobytes() == original.tobytes()
        assert actual.getchannel("A").getextrema() == (alpha, alpha)


@pytest.mark.parametrize("suffix", [".png", ".webp", ".tiff"])
def test_standard_image_export_preserves_full_composite_rgba_and_editable_native_state(tmp_path, suffix):
    doc = Document(263, 197)
    doc.layers = [Layer("Original pixels", artwork(doc.size)),
                  Layer("Overlay", Image.new("RGBA", doc.size, (40, 170, 90, 100)),
                        opacity=.6, blend="screen")]
    doc.active = 1
    doc.layer.mask = Image.linear_gradient("L").resize(doc.size)
    doc.select("rectangle", (5, 7), (62, 57))
    doc.metadata.update(guides_x=[2, 19], guides_y=[3], title="Synthetic painting")
    doc.export_view = ExportView(.31)
    original_layers = [layer.image.tobytes() for layer in doc.layers]
    original_composite = doc.composite().tobytes()
    original_selection, original_mask = doc.selection.tobytes(), doc.layer.mask.tobytes()
    state = doc.revision, doc.active, len(doc.undo_stack), len(doc.redo_stack)
    expected = doc.composite()
    target = tmp_path / ("painting" + suffix)
    assert export(doc, target) == target
    with Image.open(target) as saved:
        assert saved.mode == "RGBA" and saved.size == doc.size
        assert saved.tobytes() == expected.tobytes()
    assert expected.tobytes() == original_composite
    assert [layer.image.tobytes() for layer in doc.layers] == original_layers
    assert doc.composite().tobytes() == original_composite
    assert doc.selection.tobytes() == original_selection and doc.layer.mask.tobytes() == original_mask
    assert (doc.revision, doc.active, len(doc.undo_stack), len(doc.redo_stack)) == state
    project = tmp_path / "editable.tart"
    save_project(doc, project)
    restored = load_project(project)
    assert [layer.image.tobytes() for layer in restored.layers] == original_layers
    assert restored.composite().tobytes() == original_composite
    assert restored.selection.tobytes() == original_selection
    assert restored.layer.mask.tobytes() == original_mask
    assert restored.metadata == doc.metadata


def test_headless_image_export_keeps_all_full_resolution_pixels(tmp_path):
    doc = Document(263, 197)
    doc.layers = [Layer("Paint", artwork(doc.size))]
    expected = doc.composite()
    target = export(doc, tmp_path / "headless.png")
    with Image.open(target) as saved:
        assert saved.size == doc.size and saved.tobytes() == expected.tobytes()


def test_text_exports_do_not_change_when_terminal_sampling_changes(tmp_path):
    doc = Document(131, 99)
    doc.layers = [Layer("Paint", artwork(doc.size))]
    before = export(doc, tmp_path / "first.txt", columns=43).read_text()
    doc.export_view = ExportView(.13, "bicubic")
    after = export(doc, tmp_path / "second.txt", columns=43).read_text()
    assert before == after


@pytest.mark.parametrize("zoom", [0, -1, float("nan"), float("inf"), True, "bad"])
def test_invalid_sampling_zoom_is_rejected(zoom):
    with pytest.raises(ValueError, match="finite positive"):
        ExportView(zoom)
