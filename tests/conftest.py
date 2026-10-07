"""Keep test preferences and exports out of the user's profile and project."""
import pytest


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    from termatelier.config import DEFAULTS
    monkeypatch.setitem(DEFAULTS, "export.directory", str(tmp_path / "exports"))
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    monkeypatch.delenv("SPARKER_DEBUG_STATE", raising=False)
    monkeypatch.delenv("SPARKER_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("SPARKER_EXPORT_DIR", raising=False)
