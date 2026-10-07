import json
import pytest
from PIL import Image

from termatelier.commands import CommandError, CommandSession, ScriptError
from termatelier.model import Document
from termatelier.storage import load_project
from termatelier.tool_library import TOOLS


def test_library_count_search_pagination_and_full_descriptions():
    session = CommandSession(Document(80, 64))
    data = json.loads(session.execute("tools count --json").text)
    assert data["total"] >= 1001 and data["total"] == len(TOOLS)
    found = json.loads(session.execute('tools search "oak wreath" --json').text)
    assert found["total"] == 4
    first = found["tools"][0]
    assert first["kind"] == "stamp" and "oak.wreath" in first["id"]
    assert first["description"] in session.execute("tools info " + first["id"]).text
    one = json.loads(session.execute("tools list --category rosettes --page 1 --limit 13 --json").text)
    two = json.loads(session.execute("tools list --category rosettes --page 2 --limit 13 --json").text)
    assert len(one["tools"]) == len(two["tools"]) == 13
    assert not ({s["id"] for s in one["tools"]} & {s["id"] for s in two["tools"]})
    assert not session.document.undo_stack


def test_catalog_use_settings_apply_selection_undo_and_native_round_trip(tmp_path):
    session = CommandSession(Document(80, 64))
    identifier = "botanical.oak.wreath.net"
    session.execute("tool " + identifier)
    assert session.setting("tool") == "library"
    session.execute("tool-options --size 40 --angle 20 --seed 17 --density 1.5 --amount 0.7")
    assert session.setting("library_size") == 40
    session.execute("select rectangle 0 0 39 63")
    session.execute(f"apply {identifier} 40,32 --color red --opacity 50%")
    edited = session.document.layer.image.tobytes()
    assert session.document.layer.image.getchannel("A").getbbox()
    assert session.document.layer.image.crop((40, 0, 80, 64)).getbbox() is None
    assert session.document.layer.image.getchannel("A").getextrema()[1] <= 128
    session.execute("undo")
    assert session.document.layer.image.getbbox() is None
    session.execute("redo")
    assert session.document.layer.image.tobytes() == edited
    path = tmp_path / "library.tart"
    session.execute(f'save "{path}"')
    loaded = load_project(path)
    assert loaded.settings["library_tool"] == identifier
    assert loaded.settings["library_size"] == 40
    assert loaded.layer.image.tobytes() == edited
    session.execute("tool pencil")
    assert session.setting("tool") == "pencil" and session.document.settings["library_tool"] is None


def test_library_pattern_effect_and_export_keep_original_dimensions(tmp_path):
    session = CommandSession(Document(67, 43))
    session.execute("apply textile.braid.woven.beaded --box 5,4,61,38 --size 18 --color orange")
    original = session.document.layer.image.tobytes()
    alpha = session.document.layer.image.getchannel("A").tobytes()
    session.execute("tools apply effects.invert --box 12,10,50,31 --amount 0.8")
    assert session.document.layer.image.tobytes() != original
    assert session.document.layer.image.getchannel("A").tobytes() == alpha
    session.config.set("export.directory", str(tmp_path / "outside"))
    session.execute("export library.png")
    with Image.open(tmp_path / "outside" / "library.png") as image:
        assert image.size == (67, 43)
        assert image.tobytes() == session.document.composite().tobytes()


@pytest.mark.parametrize("command", ["tools search", "tools info missing", "tool missing",
    "apply missing", "tools list --category missing", "tools list --kind missing",
    "tools count --page 2", "tools info effects.invert --kind effect",
    "apply effects.invert --box 1,2,3", "tool-options --density 0", "tool-options --size 513",
    "apply botanical.oak.wreath.net 10,10 --box 0,0,20,20", "apply effects.invert --seed -1"])
def test_malformed_library_commands_are_atomic(command):
    session = CommandSession(Document(40, 30))
    before = session.document.snapshot()
    with pytest.raises(CommandError):
        session.execute(command)
    assert session.document.layer.image.tobytes() == before[1][1].image.tobytes()
    assert session.document.pending is None and not session.document.undo_stack


def test_failed_library_script_rolls_back_pixels_and_tool_state():
    session = CommandSession(Document(80, 64))
    before = session.document.snapshot()
    with pytest.raises(ScriptError):
        session.run_script("tool rosettes.heart.spiral.star\napply rosettes.heart.spiral.star 40,32\napply unknown-tool\n")
    assert session.document.layer.image.tobytes() == before[1][1].image.tobytes()
    assert session.setting("tool") == "brush"
    assert not session.document.undo_stack and session.document.pending is None
