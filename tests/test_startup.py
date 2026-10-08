import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from termatelier import startup


def test_first_frame_signal_preserves_launcher_metadata(tmp_path, monkeypatch):
    state = tmp_path / "startup.json"
    state.write_text(json.dumps({"phase": "opening", "parent_pid": 123, "status": "Opening editor"}))
    monkeypatch.setenv(startup.STATE_ENV, str(state))
    assert startup.signal_ready()
    result = startup.read_state(state)
    assert result == {"phase": "ready", "status": "Ready", "parent_pid": 123}
    assert list(tmp_path.iterdir()) == [state]


def test_direct_launch_and_missing_state_are_safe(tmp_path, monkeypatch):
    monkeypatch.delenv(startup.STATE_ENV, raising=False)
    assert not startup.signal_ready()
    monkeypatch.setenv(startup.STATE_ENV, str(tmp_path / "absent.json"))
    assert not startup.signal_ready()
    assert not (tmp_path / "absent.json").exists()


def test_signal_read_only_state_does_not_block_editor(tmp_path, monkeypatch):
    state = tmp_path / "startup.json"
    state.write_text('{"phase":"opening"}')
    monkeypatch.setenv(startup.STATE_ENV, str(state))

    def denied(*_args):
        raise PermissionError("No write access")

    monkeypatch.setattr(startup, "write_state", denied)
    assert not startup.signal_ready()


@pytest.mark.parametrize("phase", ["starting", "environment", "dependencies", "opening", "unknown"])
def test_loading_persists_until_ready(phase):
    assert not startup.should_close({"phase": phase}, True)


@pytest.mark.parametrize("phase", ["ready", "failed", "closed"])
def test_ready_and_failure_close_window(phase):
    assert startup.should_close({"phase": phase}, True)


def test_launcher_exit_closes_even_with_missing_or_malformed_state():
    assert startup.should_close({}, False)
    assert startup.should_close({"phase": "dependencies"}, False)
    assert not startup.should_close({}, True)


def test_partial_and_bom_states_are_tolerated(tmp_path):
    state = tmp_path / "startup.json"
    assert startup.read_state(state) == {}
    state.write_text('{"phase":', encoding="utf-8")
    assert startup.read_state(state) == {}
    state.write_text('{"phase":"opening"}', encoding="utf-8-sig")
    assert startup.read_state(state) == {"phase": "opening"}


def test_parent_lifecycle():
    assert startup.parent_alive(os.getpid())
    assert not startup.parent_alive(0)
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    child.wait(timeout=10)
    assert not startup.parent_alive(child.pid)


def test_helper_runs_before_package_install_without_opening_ui(tmp_path):
    state = tmp_path / "state with spaces.json"
    startup.write_state(state, "dependencies", "Installing editor dependencies")
    args = startup.helper_arguments(state, os.getpid())
    assert args[0] == str(Path(startup.__file__).resolve())
    assert args[1:5] == ["--state", str(state), "--parent-pid", str(os.getpid())]
    result = subprocess.run([sys.executable, "-S", *args, "--check"], check=True,
                            capture_output=True, text=True, timeout=10)
    checked = json.loads(result.stdout)
    assert checked["logo_exists"]
    assert checked["parent_alive"]
    assert not checked["should_close"]
    assert checked["state"]["status"] == "Installing editor dependencies"


def test_minimum_logo_time_delays_ready_but_not_failure_or_parent_exit():
    assert not startup.should_close({"phase": "ready"}, True, elapsed=2.4, minimum_seconds=2.5)
    assert startup.should_close({"phase": "ready"}, True, elapsed=2.5, minimum_seconds=2.5)
    assert not startup.should_close({"phase": "dependencies"}, True, elapsed=50, minimum_seconds=2.5)
    assert startup.should_close({"phase": "failed"}, True, elapsed=0, minimum_seconds=30)
    assert startup.should_close({"phase": "closed"}, True, elapsed=0, minimum_seconds=30)
    assert startup.should_close({"phase": "starting"}, False, elapsed=0, minimum_seconds=30)


def test_debug_helper_persists_after_logo_readiness():
    assert not startup.helper_should_exit({"phase": "ready"}, True)
    assert not startup.helper_should_exit({"phase": "opening"}, True)
    assert startup.helper_should_exit({"phase": "failed"}, True)
    assert startup.helper_should_exit({"phase": "closed"}, True)
    assert startup.helper_should_exit({"phase": "ready"}, False)


def test_debug_snapshot_separate_from_startup_handshake(tmp_path, monkeypatch):
    state = tmp_path / "startup.json"
    debug = tmp_path / "runtime.json"
    startup.write_state(state, "opening", "Opening editor")
    debug.write_text('{"launcher":{"pid":42},"diagnostics":{"old":true}}')
    monkeypatch.setenv(startup.STATE_ENV, str(state))
    monkeypatch.setenv(startup.DEBUG_STATE_ENV, str(debug))
    assert startup.write_debug_snapshot({"diagnostics": {"document": {"size": [8192, 8192]}}})
    assert startup.signal_ready()
    assert startup.read_state(debug) == {"launcher": {"pid": 42},
                                         "diagnostics": {"document": {"size": [8192, 8192]}}}
    assert startup.read_state(state)["phase"] == "ready"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["runtime.json", "startup.json"]


def test_missing_debug_channel_does_not_prevent_direct_launch(tmp_path, monkeypatch):
    monkeypatch.delenv(startup.DEBUG_STATE_ENV, raising=False)
    assert not startup.write_debug_snapshot({"document": "test"})
    monkeypatch.setenv(startup.DEBUG_STATE_ENV, str(tmp_path / "absent" / "debug.json"))
    assert not startup.write_debug_snapshot({"document": "test"})


def test_curated_debug_text_contains_runtime_venv_and_native_canvas():
    text = startup.format_debug_text({"phase": "ready", "parent_pid": 101}, {
        "python": {"executable": r"C:\Painting\.venv\Scripts\python.exe", "version": "3.14.0"},
        "venv": {"active": True, "path": r"C:\Painting\.venv"},
        "program": {"name": "SPARKER iCLI", "version": "0.2.0"},
        "document": {"width": 6000, "height": 4000, "layers": 3},
    }, elapsed=3.5)
    assert "SPARKER iCLI" in text and ".venv" in text
    assert "Python: 3.14.0" in text and "Canvas: 6000×4000" in text
    assert "launcher PID 101" in text


def test_launcher_settings_validate_before_environment_install(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    result = subprocess.run([sys.executable, "-S", str(Path(startup.__file__).resolve()),
                             "--configure", "--set", "startup.minimum_seconds=4.5",
                             "--set", "debug.enabled=false"],
                            check=True, capture_output=True, text=True, timeout=10)
    checked = json.loads(result.stdout)
    assert checked == {"minimum_seconds": 4.5, "debug_enabled": False,
                        "memory_mode": "standard",
                        "overrides": {"startup.minimum_seconds": 4.5, "debug.enabled": False}}
    assert not (tmp_path / "settings.json").exists()


def test_launcher_rejects_invalid_minimum_and_unknown_setting():
    for value in ["startup.minimum_seconds=-1", "startup.minimum_seconds=31", "debug.unknown=true"]:
        result = subprocess.run([sys.executable, "-S", str(Path(startup.__file__).resolve()),
                                 "--configure", "--set", value],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert "error" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Native color-key transparency requires Windows")
def test_native_overlay_is_borderless_transparent_at_screen_origin():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    window = tk.Toplevel(root)
    window.withdraw()
    try:
        assert startup.configure_overlay(window, opacity=.75)
        label = tk.Label(window, text="SPARKER iCLI DEBUG", bg=startup.TRANSPARENT_COLOR,
                         fg="white", borderwidth=0, padx=0, pady=0)
        label.pack()
        window.geometry("+0+0")
        window.deiconify()
        root.update()
        assert window.overrideredirect()
        assert str(window.attributes("-transparentcolor")) == startup.TRANSPARENT_COLOR
        assert window.attributes("-alpha") == pytest.approx(.75)
        assert window.winfo_x() == 0 and window.winfo_y() == 0
        assert str(label.cget("background")) == startup.TRANSPARENT_COLOR
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetParent.argtypes = (wintypes.HWND,)
        user.GetParent.restype = wintypes.HWND
        handle = user.GetParent(window.winfo_id()) or window.winfo_id()
        get_style = getattr(user, "GetWindowLongPtrW", user.GetWindowLongW)
        get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        style = get_style(handle, -20)
        assert style & 0x20  # Mouse passes through to the terminal.
        assert style & 0x08000000  # The overlay cannot take keyboard focus.
        assert style & 0x80000  # Native layered-window transparency.
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Native helper lifecycle requires Windows")
@pytest.mark.parametrize("initially_hidden", [False, True])
def test_native_helper_logo_closes_but_debug_persists_and_toggles(tmp_path, monkeypatch, initially_hidden):
    import ctypes
    from ctypes import wintypes
    from termatelier.config import RuntimeConfig
    from termatelier.diagnostics import collect_diagnostics

    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("SPARKER_CONFIG_OVERRIDES", raising=False)
    state = tmp_path / "startup.json"
    debug = tmp_path / "runtime.json"
    startup.write_state(state, "opening", "Opening editor")
    config = RuntimeConfig.load(overrides={"startup.minimum_seconds": .6, "debug.detail": "compact",
                                          "debug.enabled": not initially_hidden})
    debug.write_text(json.dumps({"diagnostics": collect_diagnostics(config=config)}))
    # Venv redirectors spawn another process on Windows; use the base interpreter
    # so the native-window ownership assertion observes our exact helper PID.
    helper_args = [sys._base_executable, "-S", *startup.helper_arguments(state, os.getpid()),
                   "--debug-state", str(debug), "--set", "startup.minimum_seconds=.6"]
    if initially_hidden:
        helper_args.append("--no-debug")
    process = subprocess.Popen(helper_args,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)

    def windows():
        found = {}

        @callback_type
        def inspect(handle, _):
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == process.pid:
                title = ctypes.create_unicode_buffer(256)
                user.GetWindowTextW(handle, title, len(title))
                rect = wintypes.RECT()
                user.GetWindowRect(handle, ctypes.byref(rect))
                found[title.value] = (bool(user.IsWindowVisible(handle)), rect.left, rect.top)
            return True

        user.EnumWindows(inspect, 0)
        return found

    def wait_until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            assert process.poll() is None, process.stderr.read()
            time.sleep(.05)
        pytest.fail(f"Native helper condition did not occur; windows={windows()}")

    try:
        wait_until(lambda: windows().get("SPARKER iCLI", (False,))[0])
        wait_until(lambda: "SPARKER iCLI debug" in windows())
        if not initially_hidden:
            wait_until(lambda: windows().get("SPARKER iCLI debug", (False,))[0])
        assert windows()["SPARKER iCLI debug"][0] == (not initially_hidden)
        assert windows()["SPARKER iCLI debug"][1:] == (0, 0)
        startup.write_state(state, "ready", "Ready")
        wait_until(lambda: "SPARKER iCLI" not in windows())
        assert process.poll() is None
        assert windows()["SPARKER iCLI debug"][0] == (not initially_hidden)

        config.set("debug.enabled", False, persist=False)
        debug.write_text(json.dumps({"diagnostics": collect_diagnostics(config=config)}))
        wait_until(lambda: not windows().get("SPARKER iCLI debug", (True,))[0])
        config.set("debug.enabled", True, persist=False)
        debug.write_text(json.dumps({"diagnostics": collect_diagnostics(config=config)}))
        wait_until(lambda: windows().get("SPARKER iCLI debug", (False,))[0])
        startup.write_state(state, "closed", "Closed")
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process.stderr.close()


@pytest.mark.skipif(os.name != "nt", reason="Native helper lifecycle requires Windows")
def test_native_helper_exits_when_running_session_switches_to_low_memory(tmp_path, monkeypatch):
    from termatelier.config import RuntimeConfig
    from termatelier.diagnostics import collect_diagnostics

    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    state = tmp_path / "startup.json"
    debug = tmp_path / "runtime.json"
    startup.write_state(state, "ready", "Ready")
    config = RuntimeConfig.load()
    debug.write_text(json.dumps({"diagnostics": collect_diagnostics(config=config)}), encoding="utf-8")
    process = subprocess.Popen([sys._base_executable, "-S", *startup.helper_arguments(state, os.getpid()),
                                "--no-splash", "--debug-state", str(debug)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(.6)
        assert process.poll() is None
        config.set("memory.mode", "low", persist=False)
        debug.write_text(json.dumps({"diagnostics": collect_diagnostics(config=config)}), encoding="utf-8")
        assert process.wait(timeout=5) == 0, process.stderr.read()
        assert startup.read_state(state)["phase"] == "ready"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process.stderr.close()
