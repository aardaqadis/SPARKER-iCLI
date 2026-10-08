"""Typography commands exercise real layout, alpha, clipping and undo behavior."""
import json

import pytest
from PIL import Image, ImageDraw, ImageFont

from termatelier.commands import CommandError, CommandSession
from termatelier.config import RuntimeConfig
from termatelier.model import Document
from termatelier.text_tools import (MAX_TEXT_LENGTH, TextStyle, discover_fonts,
                                    normalize_text, render_text)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    monkeypatch.delenv("SPARKER_DEBUG_STATE", raising=False)
    result = CommandSession(base_dir=tmp_path, config=RuntimeConfig.load())
    result.execute("new 512x320 --transparent")
    return result


def measure(session, text, options=""):
    return json.loads(session.execute(f'text measure "{text}" {options} --json').text)


def test_simple_text_keeps_existing_signature_and_raster_position():
    doc = Document(96, 64)
    doc.text((7, 5), "Hello", "red", 12)
    font = ImageFont.load_default(size=12)
    mask = Image.new("L", doc.size)
    ImageDraw.Draw(mask).text((7, 5), "Hello", fill=255, font=font)
    expected = Image.new("RGBA", doc.size, "red")
    expected.putalpha(mask)
    assert doc.layer.image.tobytes() == Image.alpha_composite(Image.new("RGBA", doc.size), expected).tobytes()


def test_measure_does_not_change_state_and_matches_rendered_bounds(session):
    before = session.document.snapshot()
    undo = list(session.document.undo_stack)
    data = measure(session, "A title", "--size 26 --stroke 2 --shadow -3,5 --rotate 12 --opacity 75%")
    assert session.document.layer.image.tobytes() == before[1][0].image.tobytes()
    assert session.document.undo_stack == undo
    session.execute('text 40 30 "A title" --size 26 --stroke 2 --shadow -3,5 --rotate 12 --opacity 75%')
    expected = tuple(value + (40 if index % 2 == 0 else 30) for index, value in enumerate(data["bounds"]))
    assert session.document.layer.image.getchannel("A").getbbox() == expected
    assert data["width"] == data["size"][0] and data["height"] == data["size"][1]


def test_real_newlines_and_escape_sequences_have_same_layout(session):
    real = json.loads(session.execute('text measure "First\nSecond" --size 20 --json').text)
    escaped = measure(session, r"First\nSecond", "--escapes --size 20")
    assert real == escaped
    literal = measure(session, r"First\nSecond", "--size 20")
    assert literal["line_count"] == 1
    assert escaped["line_count"] == 2
    assert normalize_text(r"First\\nSecond", escapes=True) == r"First\nSecond"


def test_utf8_file_is_text_only_and_relative_to_workspace(session, tmp_path):
    source = tmp_path / "words.txt"
    source.write_text("Café\nquit\n--color green", encoding="utf-8-sig")
    data = json.loads(session.execute('text measure --file "words.txt" --json').text)
    assert data["lines"] == ["Café", "quit", "--color green"]
    result = session.execute('text 8 10 --file "words.txt" --color blue --layer "File label"')
    assert result.changed and session.document.layer.name == "File label"
    assert session.document.layer.image.getchannel("A").getbbox() is not None


def test_wrapping_breaks_words_and_honors_explicit_blank_lines(session):
    data = measure(session, r"Longwordwithoutspaces\n\nA sentence wraps correctly", "--escapes --size 14 --wrap 65")
    assert data["line_count"] >= 7
    assert "" in data["lines"]
    for line in data["lines"]:
        assert ImageFont.load_default(size=14).getlength(line) <= 65
    assert data["width"] == 65


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment_repositions_short_lines_in_fixed_text_box(align):
    result = render_text("MMMM\nI", "white", TextStyle(size=24, wrap=160, align=align))
    first = result.image.getchannel("A").crop((0, 0, 160, 30)).getbbox()
    second = result.image.getchannel("A").crop((0, 32, 160, result.image.height)).getbbox()
    assert first is not None and second is not None
    if align == "left":
        assert abs(first[0] - second[0]) <= 3
    elif align == "right":
        assert abs(first[2] - second[2]) <= 3
    else:
        assert abs((first[0] + first[2]) - (second[0] + second[2])) <= 3


def test_line_and_letter_spacing_are_real_pixel_geometry(session):
    base = measure(session, r"First\nSecond", "--escapes --size 20")
    spaced = measure(session, r"First\nSecond", "--escapes --size 20 --spacing 12 --letter-spacing 3")
    assert spaced["height"] == base["height"] + 10
    assert spaced["width"] >= base["width"] + 15
    session.execute(r'text 0 0 "First\nSecond" --escapes --size 20 --spacing 12 --letter-spacing 3')
    assert session.document.layer.image.getchannel("A").getbbox() == tuple(spaced["bounds"])


def test_outline_shadow_and_opacity_alpha_composite(session):
    session.execute('text 20 20 "MMMM" --size 32 --color "#ff000080" --stroke 2 --stroke-color blue '
                    '--shadow 5,7 --shadow-color "#00ff0080" --opacity 50%')
    pixels = list(session.document.layer.image.get_flattened_data())
    assert max(pixel[3] for pixel in pixels) <= 160
    assert any(pixel[2] > 200 and pixel[3] >= 100 for pixel in pixels)
    assert any(pixel[1] > 200 and 0 < pixel[3] <= 64 for pixel in pixels)
    assert any(pixel[0] > 200 and 0 < pixel[3] <= 80 for pixel in pixels)


@pytest.mark.parametrize("anchor", ["top-left", "top-center", "top-right", "center-left", "center",
                                    "center-right", "bottom-left", "bottom-center", "bottom-right"])
def test_anchors_match_measurement_and_clipped_canvas(session, anchor):
    data = measure(session, "Anchor", f"--size 20 --anchor {anchor}")
    session.execute(f'text 200 120 "Anchor" --size 20 --anchor {anchor}')
    expected = tuple(value + (200 if index % 2 == 0 else 120) for index, value in enumerate(data["bounds"]))
    assert session.document.layer.image.getchannel("A").getbbox() == expected


def test_selection_and_new_text_layer_are_one_undo_checkpoint(session):
    session.execute("select rectangle 20 20 70 60")
    old_layers = len(session.document.layers)
    old_history = len(session.document.undo_stack)
    session.execute('text 0 0 "Selected text" --size 48 --stroke 2 --shadow 6,8 --layer "Text layer"')
    doc = session.document
    bounds = doc.layer.image.getchannel("A").getbbox()
    assert bounds is not None and bounds[0] >= 20 and bounds[1] >= 20 and bounds[2] <= 71 and bounds[3] <= 61
    assert len(doc.undo_stack) == old_history + 1
    assert len(doc.layers) == old_layers + 1
    pixels = doc.layer.image.tobytes()
    session.execute("undo")
    assert len(doc.layers) == old_layers
    session.execute("redo")
    assert doc.layer.image.tobytes() == pixels
    assert doc.layer.name == "Text layer"


def test_soft_selection_multiplies_text_alpha_without_resampling(session):
    session.document.selection = Image.new("L", session.document.size, 128)
    result = render_text("Soft selection", "#ffffff80", TextStyle(size=22, stroke=1, stroke_color="#ff000080"))
    session.execute('text 5 8 "Soft selection" --size 22 --color "#ffffff80" --stroke 1 --stroke-color "#ff000080"')
    alpha = result.image.getchannel("A").point(lambda value: value * 128 // 255)
    expected = Image.new("L", session.document.size)
    expected.paste(alpha, (5, 8))
    assert session.document.layer.image.getchannel("A").tobytes() == expected.tobytes()


def test_locked_active_layer_rejects_text_without_new_layer(session):
    session.execute("layer lock")
    before = session.document.layer.image.tobytes()
    history = len(session.document.undo_stack)
    with pytest.raises(CommandError, match="locked"):
        session.execute('text 5 5 "Locked"')
    assert session.document.layer.image.tobytes() == before
    assert len(session.document.undo_stack) == history and session.document.pending is None
    session.execute('text 5 5 "Allowed" --layer "New text"')
    assert not session.document.layer.locked


def test_script_failure_rolls_back_advanced_text_layer_and_pixels(session):
    before = session.document.snapshot()
    with pytest.raises(CommandError):
        session.run_script('text 5 5 "Title" --stroke 1 --layer Title\ntext 5 5 "bad" --font missing.ttf')
    assert len(session.document.layers) == len(before[1])
    assert session.document.layer.image.tobytes() == before[1][0].image.tobytes()
    assert session.document.pending is None


def test_font_listing_and_font_file_choice(session, monkeypatch, tmp_path):
    from termatelier import text_tools
    # Pillow's bundled default font can be exported for a platform-neutral file.
    font = ImageFont.load_default(size=12)
    path = tmp_path / "Aileron-Regular.ttf"
    path.write_bytes(font.font_bytes if hasattr(font, "font_bytes") else font.path.getvalue())
    monkeypatch.setattr(text_tools, "font_directories", lambda: [tmp_path])
    rows = json.loads(session.execute("fonts list aileron --json --limit 4").text)
    assert rows == [{"name": "Aileron-Regular", "path": str(path.resolve())}]
    data = json.loads(session.execute('fonts info "Aileron-Regular.ttf" --size 18 --json').text)
    assert data["family"] == "Aileron" and data["size"] == 18
    measured = measure(session, "File font", '--font "Aileron-Regular.ttf" --size 18')
    session.execute('text 8 8 "File font" --font "Aileron-Regular.ttf" --size 18')
    assert measured["font"]["path"] == str(path.resolve())
    assert session.document.layer.image.getchannel("A").getbbox() is not None


@pytest.mark.parametrize("command", [
    'text 0 0 "hi" --size 0', 'text 0 0 "hi" --wrap 0', 'text 0 0 "hi" --align diagonal',
    'text 0 0 "hi" --spacing nan', 'text 0 0 "hi" --letter-spacing -1',
    'text 0 0 "hi" --stroke 129', 'text 0 0 "hi" --shadow 1',
    'text 0 0 "hi" --shadow 5000,1', 'text 0 0 "hi" --anchor nowhere',
    'text 0 0 "hi" --font missing.ttf', 'text 0 0 "hi" --rotate inf',
    'text 0 0 "hi" --json', 'text measure "hi" --layer Title',
    'text 0 0 "hi" --file missing.txt', 'text 0 0 --file missing.txt',
    'text 0 0 "hi" --layer ""', 'fonts --limit 501', 'fonts info missing.ttf',
])
def test_invalid_text_options_leave_document_and_history_unchanged(session, command):
    before = session.document.snapshot()
    history = len(session.document.undo_stack)
    with pytest.raises(CommandError):
        session.execute(command)
    assert session.document.layer.image.tobytes() == before[1][0].image.tobytes()
    assert len(session.document.layers) == len(before[1])
    assert len(session.document.undo_stack) == history and session.document.pending is None


def test_text_and_layout_allocation_are_bounded(session, tmp_path):
    with pytest.raises(CommandError, match="16,384"):
        session.execute('text 0 0 "' + "A" * (MAX_TEXT_LENGTH + 1) + '"')
    with pytest.raises(CommandError, match="pixel budget"):
        session.execute('text 0 0 "' + "A" * 200 + '" --size 512')
    with pytest.raises(CommandError, match="pixel budget"):
        session.execute('text 0 0 "A" --wrap 4096 --shadow 4096,4096')
    with pytest.raises(CommandError, match="control"):
        session.execute('text 0 0 "unsafe\x1b text"')
    path = tmp_path / "too large.txt"
    path.write_bytes(b"x" * 65_537)
    with pytest.raises(CommandError, match="65,536"):
        session.execute('text 0 0 --file "too large.txt"')
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(CommandError, match="UTF-8"):
        session.execute('text 0 0 --file "too large.txt"')
    assert session.document.pending is None


def test_offcanvas_text_does_not_rescale_or_allocate_canvas_sized_buffers(session):
    session.execute('text -100000 -100000 "Outside" --size 20')
    assert session.document.layer.image.getchannel("A").getbbox() is None
    session.execute('text -15 -8 "Partly visible" --size 20')
    assert session.document.layer.image.getchannel("A").getbbox() is not None
