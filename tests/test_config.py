"""Real persisted preferences, live debug snapshots and safe export locations."""
import importlib.util
import json
from pathlib import Path

import pytest

from termatelier.config import ConfigError, DEFAULTS, RuntimeConfig, parse_overrides, settings_path, validate
from termatelier.diagnostics import collect_diagnostics, format_diagnostics, publish_diagnostics
from termatelier.model import Document


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "preferences" / "settings.json"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    monkeypatch.delenv("SPARKER_DEBUG_STATE", raising=False)
    monkeypatch.delenv("SPARKER_EXPORT_DIR", raising=False)


def test_preferences_roundtrip_and_changes_from_multiple_instances(tmp_path):
    first, second = RuntimeConfig.load(), RuntimeConfig.load()
    assert not first.path.exists()
    assert first.get("startup.minimum_seconds") == 2.5
    assert first.get("debug.enabled") is True
    assert first.get("view.resampling") == "nearest"
    first.set("brush.size", "19")
    second.set("debug.enabled", "off")
    restored = RuntimeConfig.load()
    assert restored.get("brush.size") == 19
    assert restored.get("debug.enabled") is False
    assert restored.path == settings_path()
    stored = json.loads(restored.path.read_text())
    assert stored["format"] == "sparker-settings"
    assert stored["settings"]["brush.size"] == 19
    restored.reset("brush.size")
    assert RuntimeConfig.load().get("brush.size") == 3
    restored.reset()
    assert RuntimeConfig.load().as_dict() == DEFAULTS


@pytest.mark.parametrize("name,value", [
    ("debug.enabled", "sometimes"), ("debug.refresh_ms", 99), ("debug.font_size", 0),
    ("debug.opacity", "nan"), ("debug.color", "white"), ("debug.detail", "secret"),
    ("startup.minimum_seconds", "inf"), ("history.max_steps", 0), ("brush.size", 1.5),
    ("brush.size", True), ("brush.hardness", -1), ("fill.tolerance", 256),
    ("view.resampling", "unknown"), ("export.directory", "relative-folder"), ("python.eval", "print(1)"),
])
def test_invalid_variables_never_write_settings(name, value):
    config = RuntimeConfig.load()
    with pytest.raises(ConfigError):
        config.set(name, value)
    assert not config.path.exists()


def test_export_directory_rejects_project_and_wrapper_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    project = workspace / "Termatelier"
    project.mkdir(parents=True)
    (workspace / "run.cmd").write_text("wrapper")
    (workspace / "run.ps1").write_text("wrapper")
    monkeypatch.setenv("SPARKER_PROJECT_ROOT", str(project))
    for directory in (project, project / "exports", workspace / "artwork"):
        with pytest.raises(ConfigError, match="outside"):
            validate("export.directory", str(directory))
    assert validate("export.directory", str(tmp_path / "Pictures")) == str(tmp_path / "Pictures")


def test_ephemeral_flags_do_not_replace_saved_preferences(monkeypatch):
    cfg = RuntimeConfig.load()
    cfg.set("brush.size", 10)
    cfg.set("debug.enabled", True)
    flags = parse_overrides(["brush.size=4", "debug.enabled=off", "startup.minimum_seconds=0.5"])
    session = RuntimeConfig.load(overrides=flags)
    assert session.get("brush.size") == 4
    assert session.get("debug.enabled") is False
    assert RuntimeConfig.load().get("brush.size") == 10
    assert RuntimeConfig.load().get("debug.enabled") is True
    session.set("debug.enabled", True)
    assert session.get("debug.enabled") is True
    monkeypatch.setenv("SPARKER_CONFIG_OVERRIDES", '{"brush.size": 17}')
    assert RuntimeConfig.load().get("brush.size") == 17
    assert RuntimeConfig.load(overrides={"brush.size": 5}).get("brush.size") == 5


def test_corrupt_settings_use_defaults_without_silently_overwriting_evidence():
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    config = RuntimeConfig.load()
    assert config.get("brush.size") == 3
    assert config.warnings
    assert path.read_text() == "not json"
    path.write_text(json.dumps({"brush.size": 8, "history.max_steps": -1, "arbitrary.code": "unsafe"}))
    config = RuntimeConfig.load()
    assert config.get("brush.size") == 8
    assert config.get("history.max_steps") == 40
    assert len(config.warnings) == 2


def test_settings_module_has_no_editor_dependencies_when_loaded_directly():
    source = Path(__file__).parents[1] / "src" / "termatelier" / "config.py"
    spec = importlib.util.spec_from_file_location("sparker_bootstrap_config", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RuntimeConfig.load().get("debug.refresh_ms") == 500


def test_diagnostics_include_venv_program_document_and_no_environment_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SECRET_TOKEN", "do-not-publish-this")
    root = tmp_path / "app"
    (root / ".venv").mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = C:/Python\nversion = 3.13.2\nunsafe-secret = withheld\n")
    doc = Document(21, 13)
    with doc.edit("Test brush"):
        doc.paint_mask(doc.stroke_mask([(3, 3)], 1, 1), "red")
    data = collect_diagnostics(doc, program_root=root)
    assert data["program"]["name"] == "SPARKER iCLI"
    assert data["venv"]["config"]["version"] == "3.13.2"
    assert data["python"]["executable"]
    assert data["dependencies"]["Pillow"] != "not installed"
    assert data["document"]["width"] == 21
    assert data["document"]["height"] == 13
    assert data["document"]["undo_steps"] == 1
    assert data["document"]["history_bytes"] > 0
    assert "do-not-publish-this" not in json.dumps(data)
    assert "unsafe-secret" not in json.dumps(data)
    assert ".venv" in format_diagnostics(data)


def test_published_diagnostics_preserve_launcher_fields_and_update_live_preferences(tmp_path, monkeypatch):
    destination = tmp_path / "debug.json"
    destination.write_text(json.dumps({"startup": {"stage": "installing"}, "launcher_pid": 123}))
    monkeypatch.setenv("SPARKER_DEBUG_STATE", str(destination))
    cfg = RuntimeConfig.load()
    publish_diagnostics(Document(7, 9), cfg, stage="ready")
    snapshot = json.loads(destination.read_text())
    assert snapshot["startup"]["stage"] == "installing"
    assert snapshot["launcher_pid"] == 123
    assert snapshot["stage"] == "ready"
    assert snapshot["diagnostics"]["document"]["width"] == 7
    cfg.set("debug.enabled", False)
    cfg.set("debug.font_size", 14)
    publish_diagnostics(Document(), cfg)
    snapshot = json.loads(destination.read_text())
    assert snapshot["diagnostics"]["settings"]["debug.enabled"] is False
    assert snapshot["diagnostics"]["settings"]["debug.font_size"] == 14


def test_failed_diagnostic_file_write_does_not_break_editing(tmp_path, monkeypatch):
    destination = tmp_path / "directory"
    destination.mkdir()
    monkeypatch.setenv("SPARKER_DEBUG_STATE", str(destination))
    assert publish_diagnostics(Document())["program"]["name"] == "SPARKER iCLI"
