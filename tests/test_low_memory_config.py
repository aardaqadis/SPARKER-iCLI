import json
from pathlib import Path
from types import SimpleNamespace

from termatelier import config as preferences
from termatelier.commands import CommandSession
from termatelier.config import RuntimeConfig
from termatelier.model import Document


def test_low_profile_is_optional_preserves_preferences_and_caps_history():
    saved = RuntimeConfig.load()
    saved.set("history.max_mb", 64)
    saved.set("history.max_steps", 20)
    saved.set("preview.enabled", True)
    low = RuntimeConfig.load(overrides={"memory.mode": "low"})
    assert low.get("history.max_mb") == 16
    assert low.get("history.max_steps") == 8
    assert low.get("history.storage") == "compressed"
    assert not low.get("preview.enabled") and not low.get("debug.enabled")
    assert low.as_dict()["history.max_mb"] == 16
    assert RuntimeConfig.load().get("history.max_mb") == 64
    low.set("history.max_mb", 8, persist=False)
    assert low.get("history.max_mb") == 8
    low.set("memory.mode", "standard", persist=False)
    assert low.get("history.max_steps") == 20
    assert low.get("preview.enabled") is True


def test_live_cli_memory_switch_compresses_history_and_restores_settings():
    session = CommandSession(Document(64, 48))
    session.execute('pencil 2,3 --color red')
    before = session.document.composite().tobytes()
    session.execute("config set memory.mode low")
    doc = session.document
    assert doc.history_storage == "compressed"
    assert doc.history_bytes == 16 * 1024 * 1024 and doc.history_limit == 8
    assert doc.composite().tobytes() == before
    session.execute("undo")
    session.execute("redo")
    assert doc.composite().tobytes() == before
    assert RuntimeConfig.load().get("memory.mode") == "low"
    assert "stays off in low-memory mode" in session.execute("debug on").text
    result = session.execute("config set history.max_mb 32")
    assert "limited by low-memory mode" in result.text
    session.execute("config set memory.mode standard")
    assert doc.history_bytes == 32 * 1024 * 1024
    assert doc.history_storage == "raw"


def test_platform_settings_and_xdg_pictures_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("SPARKER_CONFIG_FILE")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(preferences, "os", SimpleNamespace(name="posix", environ={"XDG_CONFIG_HOME": str(tmp_path / "prefs")}))
    monkeypatch.setattr(preferences.sys, "platform", "linux")
    assert preferences.settings_path() == tmp_path / "prefs" / "SPARKER-iCLI" / "settings.json"
    (tmp_path / "prefs").mkdir()
    (tmp_path / "prefs" / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME/My Artwork"\n', encoding="utf-8")
    assert preferences.pictures_directory() == tmp_path / "My Artwork"
    assert preferences.default_export_directory() == tmp_path / "My Artwork" / "SPARKER-iCLI" / "Exports"
    monkeypatch.setattr(preferences.sys, "platform", "darwin")
    assert preferences.settings_path() == tmp_path / "Library" / "Application Support" / "SPARKER-iCLI" / "settings.json"
    legacy = tmp_path / ".config" / "SPARKER-iCLI" / "settings.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"debug.enabled": False}))
    assert preferences.settings_path() == legacy
