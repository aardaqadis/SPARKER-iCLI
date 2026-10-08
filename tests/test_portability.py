"""OS-specific services remain optional and report accurate runtime state."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from termatelier import diagnostics, file_explorer, preview
from termatelier.config import RuntimeConfig
from termatelier.model import Document


@pytest.mark.parametrize("os_name,platform,environment,available", [
    ("nt", "win32", {}, True),
    ("posix", "darwin", {}, True),
    ("posix", "linux", {}, False),
    ("posix", "freebsd14", {}, False),
    ("posix", "linux", {"WAYLAND_DISPLAY": "wayland-0"}, False),
    ("posix", "linux", {"DISPLAY": ":0"}, True),
    ("posix", "freebsd14", {"DISPLAY": "localhost:10.0"}, True),
])
def test_optional_desktop_availability(os_name, platform, environment, available, monkeypatch):
    monkeypatch.setattr(preview, "os", SimpleNamespace(name=os_name, environ=environment))
    monkeypatch.setattr(preview, "sys", SimpleNamespace(platform=platform))
    assert preview.desktop_available() is available


def test_no_desktop_does_not_allocate_preview_frames_or_spawn_a_helper(monkeypatch):
    monkeypatch.setattr(preview, "desktop_available", lambda: False)
    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("A headless session must not start a desktop process")
    monkeypatch.setattr(preview.subprocess, "Popen", unexpected_spawn)
    controller = preview.PreviewController(config=RuntimeConfig())
    assert not controller.start(Document(31, 19))
    assert controller.state_path is None
    assert "No desktop display" in controller.error
    controller.close()


@pytest.mark.parametrize("name", ["CON", "trail.", "art:layers", "a\\b"])
def test_explorer_applies_filename_rules_to_host_os(name, monkeypatch):
    monkeypatch.setattr(file_explorer, "os", SimpleNamespace(name="posix"))
    assert file_explorer.folder_name(name) == name
    monkeypatch.setattr(file_explorer, "os", SimpleNamespace(name="nt"))
    with pytest.raises(ValueError):
        file_explorer.folder_name(name)


def test_linux_memory_reports_current_resident_pages(monkeypatch):
    original_read = Path.read_text
    monkeypatch.setattr(diagnostics, "os", SimpleNamespace(name="posix", sysconf=lambda name: 4096))
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(Path, "read_text", lambda path, **kwargs:
                        "1000 120 30 40 0 90 0\n" if path.as_posix() == "/proc/self/statm"
                        else original_read(path, **kwargs))
    assert diagnostics._memory_measurement() == (120 * 4096, "resident", "Linux /proc/self/statm")


@pytest.mark.parametrize("platform,multiplier", [("darwin", 1), ("freebsd14", 1024)])
def test_fallback_memory_reports_peak_with_platform_units(platform, multiplier, monkeypatch):
    monkeypatch.setattr(diagnostics, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setitem(sys.modules, "resource", SimpleNamespace(
        RUSAGE_SELF=0, getrusage=lambda who: SimpleNamespace(ru_maxrss=8192)))
    assert diagnostics._memory_measurement() == (8192 * multiplier, "peak resident", "resource.ru_maxrss")


def test_external_active_virtual_environment_is_reported(tmp_path, monkeypatch):
    environment = tmp_path / "External Python"
    environment.mkdir()
    (environment / "pyvenv.cfg").write_text("version = 3.14\nhome = /usr/bin\nsecret = omitted\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(prefix=str(environment), base_prefix="/usr"))
    details = diagnostics._venv_details(tmp_path / "Source")
    assert not details["exists"]
    assert details["active"]
    assert details["active_path"] == str(environment.resolve())
    assert details["active_config"] == {"version": "3.14", "home": "/usr/bin"}


def test_diagnostics_count_compressed_history_and_show_memory_mode(tmp_path):
    config = RuntimeConfig({"memory.mode": "low"}, path=tmp_path / "settings.json")
    document = Document(64, 48)
    document.history_storage = "compressed"
    with document.edit("Pixel"):
        document.layer.image.putpixel((1, 1), (20, 40, 60, 255))
    data = diagnostics.collect_diagnostics(document, config=config)
    assert data["document"]["history_bytes"] == document.history_memory_bytes
    assert diagnostics._history_cost(document.undo_stack) == document.history_memory_bytes
    assert document.history_memory_bytes < 64 * 48 * 8
    assert data["program"]["memory_mode"] == "low"
    assert "mode=low" in diagnostics.format_diagnostics(data)
