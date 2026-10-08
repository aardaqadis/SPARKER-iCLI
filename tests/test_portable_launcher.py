"""Verify bootstrap behavior without accessing the network or user .venv."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sparker_portable_launcher", PROJECT / "run.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    root = tmp_path / "painting editor with spaces"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="sparker-icli"\nversion="0.5.2"\n')
    state = {"environment": False, "installation": False, "calls": [], "fail": None}

    def run(arguments, **kwargs):
        state["calls"].append((arguments, kwargs))
        status = 0
        if arguments[1:3] == ["-m", "venv"]:
            status = 1 if state["fail"] == "venv" else 0
            if not status:
                python = launcher.environment_python(root / ".venv")
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("fake interpreter")
                state["environment"] = True
        elif arguments[1:4] == ["-m", "pip", "install"]:
            status = 1 if state["fail"] == "pip" else 0
            if not status:
                state["installation"] = True
        elif arguments[1] == "-c":
            if "sys.prefix" in arguments[2]:
                status = 0 if state["environment"] else 1
            else:
                status = 0 if state["installation"] else 1
        else:
            raise AssertionError(f"Unexpected command: {arguments}")
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(arguments, status)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    return root, state


def ready_environment(root, state):
    python = launcher.environment_python(root / ".venv")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake interpreter")
    state["environment"] = state["installation"] = True
    (root / ".venv" / "sparker-installed.json").write_text(json.dumps(launcher.project_signature(root)))


def test_native_environment_layouts(tmp_path):
    assert launcher.environment_python(tmp_path, "nt") == tmp_path / "Scripts" / "python.exe"
    assert launcher.environment_python(tmp_path, "posix") == tmp_path / "bin" / "python"


def test_first_launch_installs_with_absolute_argument_list_and_valid_stamp(bootstrap):
    root, state = bootstrap
    assert launcher.prepare_environment(root) == launcher.environment_python(root / ".venv")
    commands = [call[0] for call in state["calls"]]
    assert commands[0] == [sys.executable, "-m", "venv", str(root / ".venv")]
    assert [str(launcher.environment_python(root / ".venv")), "-m", "pip", "install", "-e", str(root)] in commands
    assert json.loads((root / ".venv" / "sparker-installed.json").read_text()) == launcher.project_signature(root)


def test_cached_launch_checks_source_without_reinstall(bootstrap):
    root, state = bootstrap
    ready_environment(root, state)
    launcher.prepare_environment(root)
    commands = [call[0] for call in state["calls"]]
    assert len(commands) == 2
    assert all(arguments[1] == "-c" for arguments in commands)


@pytest.mark.parametrize("damage", ["project", "moved", "dependencies", "stamp"])
def test_changed_or_damaged_installation_is_repaired(bootstrap, damage):
    root, state = bootstrap
    ready_environment(root, state)
    stamp = root / ".venv" / "sparker-installed.json"
    if damage == "project":
        (root / "pyproject.toml").write_text('[project]\nname="sparker-icli"\nversion="0.5.3"\n')
    elif damage == "moved":
        stamp.write_text(json.dumps({**launcher.project_signature(root), "root": str(root / "old")}))
    elif damage == "dependencies":
        state["installation"] = False
    else:
        stamp.write_text("incomplete json{")
    launcher.prepare_environment(root)
    commands = [call[0] for call in state["calls"]]
    assert sum(arguments[1:4] == ["-m", "pip", "install"] for arguments in commands) == 1
    assert not any(arguments[1:3] == ["-m", "venv"] for arguments in commands)


def test_invalid_environment_is_recreated_without_recursive_delete(bootstrap):
    root, state = bootstrap
    ready_environment(root, state)
    state["environment"] = False
    marker = root / ".venv" / "keep-local-data.txt"
    marker.write_text("preserve me")
    launcher.prepare_environment(root)
    assert marker.read_text() == "preserve me"
    assert any(arguments[1:3] == ["-m", "venv"] for arguments, _ in state["calls"])


@pytest.mark.parametrize("failed_operation", ["venv", "pip"])
def test_failed_preparation_does_not_write_success_stamp(bootstrap, failed_operation, capsys):
    root, state = bootstrap
    state["fail"] = failed_operation
    assert launcher.main(["--inspect"], root=root) == 1
    assert not (root / ".venv" / "sparker-installed.json").exists()
    assert "SPARKER iCLI:" in capsys.readouterr().err


def test_exec_preserves_arguments_directory_and_configuration_without_resident_helper(bootstrap, monkeypatch):
    root, state = bootstrap
    ready_environment(root, state)
    monkeypatch.setenv("SPARKER_STARTUP_STATE", "other startup.json")
    monkeypatch.setenv("SPARKER_DEBUG_STATE", "other debug.json")
    monkeypatch.setenv("SPARKER_CONFIG_OVERRIDES", '{"memory.mode":"low"}')
    caller_directory = Path.cwd()
    captured = {}

    class Executed(BaseException):
        pass

    def execute(path, arguments, environment):
        captured.update(path=path, arguments=arguments, environment=environment)
        raise Executed()

    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix", environ=os.environ, execve=execute))
    monkeypatch.setattr(launcher, "prepare_environment", lambda _: launcher.environment_python(root / ".venv"))
    app_arguments = ["--low-memory", "--no-splash", "-c", 'text 1 2 "hello world; $HOME"',
                     "--script", "relative art/my drawing.sparker"]
    with pytest.raises(Executed):
        launcher.main(app_arguments, root=root)
    assert captured["arguments"] == [captured["path"], "-m", "termatelier",
                                     *[argument for argument in app_arguments if argument != "--no-splash"]]
    assert Path.cwd() == caller_directory
    assert "SPARKER_STARTUP_STATE" not in captured["environment"]
    assert "SPARKER_DEBUG_STATE" not in captured["environment"]
    assert captured["environment"]["SPARKER_CONFIG_OVERRIDES"] == '{"memory.mode":"low"}'
    assert captured["environment"]["SPARKER_PROJECT_ROOT"] == str(root)
    assert os.environ["SPARKER_STARTUP_STATE"] == "other startup.json"


def test_windows_launcher_uses_safe_argv_and_preserves_editor_exit_code(tmp_path, monkeypatch):
    python = tmp_path / "directory with spaces" / "python.exe"
    monkeypatch.setattr(launcher, "prepare_environment", lambda _: python)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="nt", environ=os.environ))
    arguments = ["--low-memory", "-c", 'text 1 2 "a b"', "relative image.png"]
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(launcher, "_run", run)
    assert launcher.main(arguments, root=tmp_path) == 7
    assert calls[0][0] == [str(python), "-m", "termatelier", *arguments]
    assert "shell" not in calls[0][1]
    assert "SPARKER_STARTUP_STATE" not in calls[0][1]["env"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell launcher")
def test_shell_launcher_forwards_quoted_arguments_in_directory_with_spaces(tmp_path):
    folder = tmp_path / "project with spaces"
    folder.mkdir()
    shutil.copyfile(PROJECT / "run.sh", folder / "run.sh")
    # Stand-in bootstrap proves the shell passes every argument literally.
    (folder / "run.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
    env = {**os.environ, "SPARKER_PYTHON": sys.executable}
    arguments = ["-c", 'text 1 2 "a b"; $(touch forbidden)', "path with spaces/art.png", "--low-memory"]
    result = subprocess.run(["sh", str(folder / "run.sh"), *arguments], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == arguments
    assert not (folder / "forbidden").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell launcher")
def test_shell_launcher_rejects_explicit_missing_python(tmp_path):
    result = subprocess.run(["sh", str(PROJECT / "run.sh")],
                            env={**os.environ, "SPARKER_PYTHON": str(tmp_path / "missing python")},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "SPARKER_PYTHON" in result.stderr
