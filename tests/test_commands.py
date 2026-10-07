"""Behavior tests for the shared command workspace and headless entry point."""
import json
from pathlib import Path

import pytest
from PIL import Image

from termatelier.commands import CommandError, CommandSession, ScriptError, tokenize
from termatelier.__main__ import main, repl
from termatelier.storage import load_project


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    """Commands must never edit the user's real preferences or Pictures folder."""
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    monkeypatch.delenv("SPARKER_DEBUG_STATE", raising=False)
    monkeypatch.delenv("SPARKER_EXPORT_DIR", raising=False)
    from termatelier.config import RuntimeConfig
    config = RuntimeConfig.load()
    config.set("export.directory", str(tmp_path))


def workspace(tmp_path, size="16x12"):
    session = CommandSession(base_dir=tmp_path)
    session.execute(f"new {size} --transparent")
    return session


def test_painting_selection_undo_and_real_exports(tmp_path):
    session = workspace(tmp_path)
    session.execute("brush --size 1 --hardness 1 --opacity 50%")
    session.execute("color red")
    session.execute("select rectangle 2 2 7 6")
    session.execute("stroke 0,3 12,3")
    doc = session.document
    assert doc.layer.image.getpixel((3, 3)) == (255, 0, 0, 128)
    assert doc.layer.image.getpixel((1, 3)) == (0, 0, 0, 0)
    painted = doc.layer.image.tobytes()
    session.execute("erase 3,3 --size 1 --hardness 1 --opacity 1")
    assert doc.layer.image.getpixel((3, 3))[3] == 0
    session.execute("undo")
    assert doc.layer.image.tobytes() == painted
    session.execute("redo")
    assert doc.layer.image.getpixel((3, 3))[3] == 0
    session.execute('export "paint image.png"')
    with Image.open(tmp_path / "paint image.png") as image:
        assert image.tobytes() == doc.composite().tobytes()
    session.execute("export art.txt --columns 8")
    assert len((tmp_path / "art.txt").read_text().splitlines()) == 3
    session.execute('save "editable art.tart"')
    restored = load_project(tmp_path / "editable art.tart")
    assert restored.composite().tobytes() == doc.composite().tobytes()
    assert restored.settings["foreground"] == "#ff0000"
    assert restored.selection.tobytes() == doc.selection.tobytes()
    assert not doc.dirty


def test_fill_shapes_gradient_text_and_color_pick(tmp_path):
    session = workspace(tmp_path, "48x24")
    session.execute("gradient 0 0 47 0 --from black --to white --opacity 1")
    doc = session.document
    assert doc.layer.image.getpixel((0, 0)) == (0, 0, 0, 255)
    assert doc.layer.image.getpixel((47, 0)) == (255, 255, 255, 255)
    session.execute("layer add Shapes")
    session.execute("rect 2 2 15 15 --color red --width 1")
    session.execute("fill 5 5 --color blue --tolerance 0")
    assert doc.layer.image.getpixel((5, 5)) == (0, 0, 255, 255)
    assert doc.layer.image.getpixel((2, 2)) == (255, 0, 0, 255)
    session.execute("ellipse 18 2 27 12 --filled --color green --width 1")
    assert doc.layer.image.getpixel((23, 7))[:3] == (0, 128, 0)
    session.execute("line 0 20 47 20 --color yellow --width 1")
    assert doc.layer.image.getpixel((25, 20))[:3] == (255, 255, 0)
    session.execute('text 1 2 "Hi SPARKER" --size 10 --color white')
    assert doc.layer.image.getchannel("A").getbbox() is not None
    result = session.execute("pick 25 20 --active")
    assert "#ffff00" in result.text
    assert doc.settings["foreground"] == "#ffff00"


def test_layers_masks_clipboard_order_and_flatten(tmp_path):
    session = workspace(tmp_path)
    session.execute("rectangle 1 1 4 4 --filled --color red")
    session.execute("select rectangle 1 1 2 2")
    session.execute("layer mask selection")
    assert session.document.composite().getpixel((3, 3))[3] == 0
    session.execute("layer mask apply")
    assert session.document.layer.mask is None
    assert session.document.layer.image.getpixel((3, 3))[3] == 0
    session.execute("copy")
    session.execute("cut")
    assert session.document.layer.image.getchannel("A").getbbox() is None
    session.execute("paste")
    assert len(session.document.layers) == 2
    session.execute('layer rename "Cutout copy"')
    session.execute("layer opacity 50%")
    session.execute("layer blend multiply")
    session.execute("layer lower")
    assert session.document.active == 0
    session.execute("layer raise")
    assert session.document.active == 1
    session.execute('layer select "Cutout copy"')
    before = session.document.composite().tobytes()
    session.execute("layer merge")
    assert len(session.document.layers) == 1
    assert session.document.composite().tobytes() == before
    session.execute("layer duplicate")
    session.execute("layer hide")
    before = session.document.composite().tobytes()
    session.execute("layer flatten")
    assert len(session.document.layers) == 1
    assert session.document.composite().tobytes() == before


def test_transforms_crop_filter_and_metadata(tmp_path):
    session = workspace(tmp_path, "10x10")
    session.execute("pencil 1,1 --color red")
    session.execute("layer mask white")
    session.execute("move 2 1")
    assert session.document.layer.image.getpixel((3, 2)) == (255, 0, 0, 255)
    assert session.document.layer.mask.getpixel((0, 0)) == 0
    session.execute("transform flip-h")
    assert session.document.layer.image.getpixel((6, 2)) == (255, 0, 0, 255)
    session.execute("select rectangle 5 1 7 3")
    session.execute("filter invert")
    assert session.document.layer.image.getpixel((6, 2)) == (0, 255, 255, 255)
    session.execute("crop")
    assert session.document.size == (3, 3)
    session.execute("resize 6x6")
    assert session.document.layer.mask.size == (6, 6)
    session.execute("canvas 7x8")
    assert session.document.size == (7, 8)
    session.execute("guides x 1 3")
    session.execute("guides y 2 6")
    session.execute("grid 4")
    session.execute('meta title "A careful study"')
    session.execute("palette set red blue white")
    session.execute("palette remove 2")
    session.execute("palette add orange")
    session.execute("save study.tart")
    restored = load_project(tmp_path / "study.tart")
    assert restored.metadata["guides_x"] == [1, 3]
    assert restored.metadata["guides_y"] == [2, 6]
    assert restored.metadata["grid_spacing"] == 4
    assert restored.metadata["title"] == "A careful study"
    assert restored.settings["palette"] == ["#ff0000", "#ffffff", "#ffa500"]


def test_import_new_open_and_project_tracking(tmp_path):
    Image.new("RGBA", (4, 3), "red").save(tmp_path / "small source.png")
    session = workspace(tmp_path)
    original = session.document
    session.execute('import "small source.png" --name Inset --x 2 --y 4')
    assert len(session.document.layers) == 2
    assert session.document.layer.image.getpixel((2, 4)) == (255, 0, 0, 255)
    session.execute("save imported.tart")
    session.execute("new 2x2 --color blue")
    assert session.project_path is None
    assert session.document is not original
    result = session.execute("open imported.tart")
    assert result.document_replaced
    assert session.project_path == (tmp_path / "imported.tart").resolve()
    assert session.document.size == (16, 12)
    session.execute("save")
    session.execute('open "small source.png"')
    assert session.project_path is None
    assert session.document.size == (4, 3)


def test_atomic_script_restores_history_clipboard_settings_and_document_identity(tmp_path):
    session = workspace(tmp_path)
    session.execute("pencil 1,1 --color red")
    session.execute("copy")
    original = session.document
    before = original.snapshot()
    undo_labels = [entry[0] for entry in original.undo_stack]
    session.execute("save original.tart")
    with pytest.raises(ScriptError, match=r"failure.sparker:6"):
        session.run_script('color blue\npencil 2,2\ncut\nnew 3x3 --transparent\nlayer add Changed\nnot-a-command', "failure.sparker")
    assert session.document is original
    assert original.layer.image.tobytes() == before[1][0].image.tobytes()
    assert original.settings == before[5]
    assert [entry[0] for entry in original.undo_stack] == undo_labels
    assert original.clipboard is not None
    assert original.pending is None
    assert session.project_path == (tmp_path / "original.tart").resolve()
    assert not original.dirty


def test_script_output_is_explicitly_outside_rollback_and_nonatomic_keeps_edits(tmp_path):
    session = workspace(tmp_path)
    before = session.document.layer.image.tobytes()
    with pytest.raises(ScriptError, match="files already written remain"):
        session.run_script('pencil 2,2 --color red\nexport earlier.png\nunknown')
    assert session.document.layer.image.tobytes() == before
    with Image.open(tmp_path / "earlier.png") as image:
        assert image.getpixel((2, 2)) == (255, 0, 0, 255)
    with pytest.raises(ScriptError, match="Earlier commands remain applied"):
        session.run_script('pencil 2,2 --color green\nunknown\npencil 3,3 --color blue', atomic=False)
    assert session.document.layer.image.getpixel((2, 2)) == (0, 128, 0, 255)
    assert session.document.layer.image.getpixel((3, 3))[3] == 0


def test_script_relative_paths_nesting_and_comments(tmp_path):
    folder = tmp_path / "job folder"
    folder.mkdir()
    (folder / "inner.sparker").write_text("# A comment\n// Another comment\ncolor red\npencil 2,2\nexport child.png", encoding="utf-8")
    (folder / "outer.sparker").write_text("script inner.sparker\nsave child.tart", encoding="utf-8")
    session = workspace(tmp_path)
    session.execute('script "job folder/outer.sparker"')
    assert (tmp_path / "child.png").is_file()
    assert (folder / "child.tart").is_file()
    assert session.base_dir == tmp_path
    (folder / "loop.sparker").write_text("script loop.sparker", encoding="utf-8")
    with pytest.raises(ScriptError, match="at most 8"):
        session.execute('script "job folder/loop.sparker"')
    assert session.script_depth == 0


@pytest.mark.parametrize("command", [
    "stroke", "pencil 1,2,3", "fill -1 0", "pick 20 20", "brush --size 0", "brush --hardness nan",
    "brush --opacity 101%", "gradient 0 0 3 3 --unknown yes", "text 0 0 hi --size 10000",
    "layer opacity 2", "layer blend unknown", "layer mask apply", "layer select 100", "layer add --wat",
    "select rectangle 0 0 4 4 --mode nope", "transform nope", "filter unknown", "filter blur -1",
    "palette set", "grid 0", "guides x 200", "meta grid_spacing nope", "new 4096x4096",
    "color not-a-color", "save bad.png", "export bad.exe", 'text 0 0 "unclosed', "!echo unsafe",
])
def test_errors_do_not_change_pixels_or_history(tmp_path, command):
    session = workspace(tmp_path)
    original = session.document
    before = original.layer.image.tobytes(), original.revision, len(original.undo_stack)
    with pytest.raises(CommandError):
        session.execute(command)
    assert session.document is original
    assert (original.layer.image.tobytes(), original.revision, len(original.undo_stack)) == before
    assert original.pending is None


def test_locked_layer_cannot_be_painted_filtered_or_masked(tmp_path):
    session = workspace(tmp_path)
    session.execute("layer lock")
    for command in ("pencil 1,1", "clear", "filter invert", "layer mask white", "transform rotate 45", "layer flatten"):
        with pytest.raises(CommandError, match="[Ll]ock|[Uu]nlock"):
            session.execute(command)
    session.execute("layer unlock")
    session.execute("pencil 1,1")
    assert session.document.layer.image.getpixel((1, 1))[3] == 255


def test_multi_undo_requires_full_count_before_changing_anything(tmp_path):
    session = workspace(tmp_path)
    session.execute("pencil 1,1")
    before = session.document.layer.image.tobytes()
    with pytest.raises(CommandError, match="nothing changed"):
        session.execute("undo 2")
    assert session.document.layer.image.tobytes() == before
    session.execute("undo")
    session.execute("redo")
    assert session.document.layer.image.tobytes() == before


def test_windows_path_tokenization_preserves_backslashes():
    assert tokenize(r'open "C:\Users\rich\My Art\new.png"') == ["open", r"C:\Users\rich\My Art\new.png"]
    assert tokenize(r"open C:\images\new.png") == ["open", r"C:\images\new.png"]
    assert tokenize("color #e89332") == ["color", "#e89332"]
    assert tokenize('text 0 0 "A title with spaces"')[-1] == "A title with spaces"


def test_headless_ordered_script_command_and_legacy_exports(tmp_path, capsys):
    script = tmp_path / "job.sparker"
    script.write_text("color red\npencil 1,1 --size 1 --opacity 1", encoding="utf-8")
    result = main(["--new", "8x8", "-c", "layer add CLI", "--script", str(script),
                   "-c", "pencil 2,2 --color blue --size 1 --opacity 1", "--export", str(tmp_path / "job.png"),
                   "--save-project", str(tmp_path / "job.tart"), "--inspect"])
    assert result == 0
    with Image.open(tmp_path / "job.png") as image:
        assert image.getpixel((1, 1)) == (255, 0, 0, 255)
        assert image.getpixel((2, 2)) == (0, 0, 255, 255)
    restored = load_project(tmp_path / "job.tart")
    assert restored.layer.name == "CLI"
    assert '"metadata"' in capsys.readouterr().out


def test_headless_failure_returns_two_and_skips_later_export(tmp_path, capsys):
    result = main(["--new", "4x4", "-c", "pencil 1,1 --color red", "-c", "nonsense",
                   "--export", str(tmp_path / "not-created.png")])
    assert result == 2
    assert not (tmp_path / "not-created.png").exists()
    assert "Unknown command" in capsys.readouterr().err


def test_repl_uses_shared_session_and_recovers_from_bad_command(tmp_path, monkeypatch, capsys):
    session = workspace(tmp_path)
    lines = iter(["nonsense", "pencil 1,1 --color red", "info --json", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    assert repl(session) == 0
    assert session.document.layer.image.getpixel((1, 1)) == (255, 0, 0, 255)
    output = capsys.readouterr()
    assert "Unknown command" in output.err
    assert '"application": "SPARKER iCLI"' in output.out


def test_describe_reports_editable_state_without_mutating(tmp_path):
    session = workspace(tmp_path)
    result = session.execute("info --json")
    data = json.loads(result.text)
    assert data["size"] == [16, 12]
    assert data["active"] == 1
    assert data["layers"][0]["index"] == 1
    assert not result.changed


def test_selection_combinations_lasso_wand_feather_and_masks(tmp_path):
    session = workspace(tmp_path)
    doc = session.document
    session.execute("select lasso 1,1 5,1 3,5")
    assert doc.selection.getpixel((3, 2)) == 255
    session.execute("select ellipse 8 2 12 6 --mode add")
    assert doc.selection.getpixel((10, 4)) == 255
    session.execute("select rectangle 0 0 6 6 --mode subtract")
    assert doc.selection.getpixel((3, 2)) == 0
    session.execute("select feather 1")
    assert 0 < doc.selection.getpixel((7, 4)) < 255
    session.execute("select none")
    session.execute("rectangle 2 2 5 5 --filled --color red")
    session.execute("select wand 3 3 --tolerance 0")
    assert doc.selection.getbbox() == (2, 2, 6, 6)
    session.execute("select invert")
    assert doc.selection.getpixel((3, 3)) == 0
    session.execute("select all")
    session.execute("layer mask black")
    assert doc.composite().getchannel("A").getbbox() is None
    session.execute("layer mask invert")
    assert doc.composite().getchannel("A").getbbox() == (2, 2, 6, 6)
    session.execute("layer mask remove")
    assert doc.layer.mask is None


def test_arbitrary_rotation_scaling_and_mask_transforms(tmp_path):
    session = workspace(tmp_path, "12x12")
    session.execute("rectangle 3 3 8 8 --filled --color red")
    session.execute("select rectangle 3 3 8 8")
    session.execute("layer mask selection")
    session.execute("select none")
    session.execute("transform rotate 45")
    doc = session.document
    assert doc.layer.image.size == doc.size
    assert doc.layer.mask.size == doc.size
    assert doc.layer.image.getchannel("A").getbbox() is not None
    session.execute("transform scale 6x6")
    assert doc.layer.mask.getbbox() is not None
    assert doc.layer.image.size == doc.size
    session.execute("transform rotate-cw")
    session.execute("transform rotate-ccw")
    session.execute("transform flip-v")
    assert doc.layer.image.getchannel("A").getbbox() is not None


def test_rgba_color_alpha_combines_with_tool_opacity(tmp_path):
    session = workspace(tmp_path, "6x4")
    session.execute("pencil 1,1 --color #ff000080 --opacity 50%")
    assert session.document.layer.image.getpixel((1, 1)) == (255, 0, 0, 64)
    session.execute("layer add Gradient")
    session.execute("gradient 0 0 5 0 --from #00000080 --to #ffffff80 --opacity 50%")
    assert session.document.layer.image.getpixel((0, 0)) == (0, 0, 0, 64)
    assert session.document.layer.image.getpixel((5, 0)) == (255, 255, 255, 64)


def test_duplicate_options_rejected_before_mutation(tmp_path):
    session = workspace(tmp_path)
    with pytest.raises(CommandError, match="Duplicate"):
        session.execute("pencil 1,1 --color red --color blue")
    assert not session.document.undo_stack


def test_quit_in_script_cannot_silently_skip_later_lines(tmp_path):
    session = workspace(tmp_path)
    with pytest.raises(ScriptError, match=r"<script>:2: quit"):
        session.run_script("pencil 1,1 --color red\nquit\npencil 2,2 --color blue")
    assert not session.document.undo_stack
    assert session.document.layer.image.getchannel("A").getbbox() is None


def test_config_commands_validate_persist_and_apply_live_brush_history(tmp_path):
    from termatelier.config import RuntimeConfig
    session = workspace(tmp_path)
    session.execute("brush --size 11")
    result = session.execute("config set brush.size 2")
    assert result.changed
    assert session.setting("brush_size") == 2
    assert RuntimeConfig.load().get("brush.size") == 2
    session.execute("config set history.max_steps 2")
    session.execute("pencil 1,1 --color red")
    session.execute("pencil 2,2 --color blue")
    session.execute("pencil 3,3 --color green")
    assert len(session.document.undo_stack) == 2
    session.execute("new 9x7")
    assert session.document.history_limit == 2
    assert session.setting("brush_size") == 2
    settings = json.loads(session.execute("config list brush. --json").text)
    assert settings["brush.size"] == 2
    assert "brush.size = 2" in session.execute("config get brush.size").text
    assert session.execute("config path").text == str(RuntimeConfig.load().path)
    session.execute("config reset brush.size")
    assert session.setting("brush_size") == 3
    with pytest.raises(CommandError, match="requires an integer"):
        session.execute("config set brush.size 3.5")
    with pytest.raises(CommandError, match="Unknown setting"):
        session.execute("config set arbitrary.eval unsafe")


def test_debug_commands_publish_live_preferences_and_external_diagnostics(tmp_path, monkeypatch):
    from termatelier.config import RuntimeConfig
    state = tmp_path / "overlay-state.json"
    monkeypatch.setenv("SPARKER_DEBUG_STATE", str(state))
    session = workspace(tmp_path, "19x11")
    session.execute("debug off")
    assert json.loads(state.read_text())["diagnostics"]["settings"]["debug.enabled"] is False
    session.execute("debug toggle")
    session.execute("debug set font_size 14")
    assert RuntimeConfig.load().get("debug.enabled") is True
    assert RuntimeConfig.load().get("debug.font_size") == 14
    assert json.loads(session.execute("debug settings --json").text)["debug.font_size"] == 14
    data = json.loads(session.execute("debug info --json").text)
    assert data["document"]["width"] == 19
    assert data["document"]["height"] == 11
    assert data["venv"]["path"].endswith(".venv")
    assert ".venv" in session.execute("debug").text
    session.execute("debug export diagnostics.json")
    assert json.loads((tmp_path / "diagnostics.json").read_text())["document"]["height"] == 11
    with pytest.raises(CommandError, match=".json"):
        session.execute("debug export diagnostics.png")


def test_export_without_path_uses_external_folder_and_preserves_exact_canvas(tmp_path):
    session = workspace(tmp_path, "37x23")
    result = session.execute("export")
    assert str(tmp_path) in result.text
    with Image.open(tmp_path / "Untitled.png") as image:
        assert image.size == (37, 23)
        assert image.convert("RGBA").tobytes() == session.document.composite().tobytes()


def test_command_export_requires_explicit_lossy_permission(tmp_path):
    session = workspace(tmp_path, "27x15")
    with pytest.raises(CommandError, match="lossy"):
        session.execute("export art.jpg")
    assert not (tmp_path / "art.jpg").exists()
    result = session.execute("export art.jpg --allow-lossy")
    assert "lossy format" in result.text
    with Image.open(tmp_path / "art.jpg") as image:
        assert image.size == (27, 15)


def test_headless_settings_overrides_are_ephemeral_and_default_export_is_safe(tmp_path, capsys):
    from termatelier.config import RuntimeConfig
    original = RuntimeConfig.load().get("brush.size")
    assert main(["--new", "29x17", "--set", "brush.size=1", "--no-debug",
                 "-c", "pencil 2,2 --color red", "-c", "debug info --json", "--export"]) == 0
    with Image.open(tmp_path / "Untitled.png") as image:
        assert image.size == (29, 17)
        assert image.getpixel((2, 2)) == (255, 0, 0, 255)
        assert image.getpixel((3, 2)) == (255, 255, 255, 255)
    output = capsys.readouterr().out
    assert '"debug.enabled": false' in output
    assert RuntimeConfig.load().get("brush.size") == original
    assert RuntimeConfig.load().get("debug.enabled") is True


def test_headless_invalid_variable_stops_before_export(tmp_path, capsys):
    assert main(["--set", "brush.size=0", "--export", "bad.png"]) == 2
    assert not (tmp_path / "bad.png").exists()
    assert "brush.size must be" in capsys.readouterr().err


def test_headless_lossy_requires_permission_and_keeps_dimensions(tmp_path, capsys):
    assert main(["--new", "31x19", "--export", "art.jpg"]) == 2
    assert "lossy" in capsys.readouterr().err
    assert main(["--new", "31x19", "--export", "art.jpg", "--allow-lossy"]) == 0
    with Image.open(tmp_path / "art.jpg") as image:
        assert image.size == (31, 19)


def test_config_changes_in_script_remain_explicitly_persisted_after_document_rollback(tmp_path):
    from termatelier.config import RuntimeConfig
    session = workspace(tmp_path)
    before = session.document.layer.image.tobytes()
    with pytest.raises(ScriptError, match="Saved preferences also remain"):
        session.run_script("config set debug.enabled false\npencil 1,1 --color red\nunknown")
    assert session.document.layer.image.tobytes() == before
    assert RuntimeConfig.load().get("debug.enabled") is False
