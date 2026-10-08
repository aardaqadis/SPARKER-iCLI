"""Structured runtime diagnostics without importing terminal/image libraries."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time

try:
    from .config import RuntimeConfig, project_root
except ImportError:  # Direct-file loading during the stdlib-only bootstrap.
    from config import RuntimeConfig, project_root

_STARTED = time.monotonic()


def _memory_measurement():
    """Return a byte count and its meaning; peak RSS is not current usage."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage")]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize, "resident", "Windows working set"
        except (OSError, AttributeError, ImportError):
            pass
    else:
        if sys.platform.startswith("linux"):
            try:
                # statm's second field is the resident set in pages. Unlike
                # ru_maxrss this falls again after image caches are released.
                pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
                return pages * os.sysconf("SC_PAGE_SIZE"), "resident", "Linux /proc/self/statm"
            except (OSError, ValueError, IndexError, AttributeError):
                pass
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return (peak if sys.platform == "darwin" else peak * 1024), "peak resident", "resource.ru_maxrss"
        except (ImportError, OSError, AttributeError):
            pass
    return None, "unavailable", "unavailable"


def _memory_bytes():
    """Compatibility accessor for callers needing only the byte count."""
    return _memory_measurement()[0]


def _version():
    try:
        from . import __version__
        return __version__
    except ImportError:
        try:
            return importlib.metadata.version("sparker-icli")
        except importlib.metadata.PackageNotFoundError:
            # Read metadata in source checkouts before installing dependencies.
            try:
                import tomllib
                with (project_root() / "pyproject.toml").open("rb") as stream:
                    return tomllib.load(stream)["project"]["version"]
            except (OSError, ValueError, KeyError, ImportError):
                return "unknown"


def _read_venv_config(config):
    values = {}
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() in ("home", "version", "executable", "include-system-site-packages", "prompt"):
                values[name.strip()] = value.strip()
    except (OSError, UnicodeError):
        pass
    return values


def _venv_details(root):
    location = root / ".venv"
    config = location / "pyvenv.cfg"
    active = sys.prefix != sys.base_prefix
    active_path = Path(sys.prefix).resolve() if active else None
    return {"path": str(location), "exists": location.is_dir(), "config_path": str(config),
            "config_exists": config.is_file(), "config": _read_venv_config(config),
            "active": active, "active_path": str(active_path) if active_path else None,
            "active_config": _read_venv_config(active_path / "pyvenv.cfg") if active_path else {},
            "prefix": sys.prefix, "base_prefix": sys.base_prefix}


def _history_cost(entries):
    # Compressed snapshots store byte records rather than PIL images. Importing
    # the model is appropriate only after a document has already been created.
    from .model import Document
    if hasattr(Document, "_snapshot_bytes"):
        return sum(Document._snapshot_bytes(state) for _, state in entries)
    total = 0
    for _, state in entries:
        size, layers, _, selection, *_ = state
        total += size[0] * size[1] * (sum(4 + (layer.mask is not None) for layer in layers) + (selection is not None))
    return total


def collect_diagnostics(document=None, config=None, program_root=None):
    cfg = config or RuntimeConfig.load()
    root = Path(program_root).resolve() if program_root else project_root()
    terminal = shutil.get_terminal_size(fallback=(0, 0))
    dependencies = {}
    for package in ("sparker-icli", "Pillow", "textual", "rich"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = "not installed"
    memory_bytes, memory_kind, memory_source = _memory_measurement()
    data = {
        "program": {"name": "SPARKER iCLI", "version": _version(), "root": str(root),
                    "working_directory": str(Path.cwd()), "pid": os.getpid(),
                    "memory_bytes": memory_bytes, "memory_kind": memory_kind, "memory_source": memory_source,
                    "memory_mode": cfg.get("memory.mode", "standard"),
                    "uptime_seconds": round(time.monotonic() - _STARTED, 3)},
        "python": {"executable": sys.executable, "version": platform.python_version(),
                   "implementation": platform.python_implementation(), "architecture": platform.machine()},
        "venv": _venv_details(root),
        "platform": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
        "terminal": {"columns": terminal.columns, "rows": terminal.lines,
                     "stdin_tty": bool(getattr(sys.stdin, "isatty", lambda: False)()),
                     "stdout_tty": bool(getattr(sys.stdout, "isatty", lambda: False)())},
        "dependencies": dependencies,
        "settings_path": str(cfg.path), "settings": cfg.as_dict(), "settings_warnings": list(cfg.warnings),
    }
    debug_path = os.environ.get("SPARKER_DEBUG_STATE")
    if debug_path:
        try:
            snapshot = Path(debug_path)
            if snapshot.is_file() and snapshot.stat().st_size <= 262144:
                state = json.loads(snapshot.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    data["startup"] = {key: state[key] for key in ("stage", "startup", "started_at", "ready_at", "launcher_pid") if key in state}
        except (OSError, ValueError, UnicodeError):
            pass
    if document is not None:
        data["document"] = {
            "width": document.width, "height": document.height, "pixel_mode": "RGBA",
            "layers": len(document.layers), "active_layer": document.active + 1,
            "active_name": document.layer.name, "dirty": document.dirty,
            "revision": document.revision, "selection": document.selection is not None,
            "undo_steps": len(document.undo_stack), "redo_steps": len(document.redo_stack),
            "history_bytes": (document.history_memory_bytes if hasattr(document, "history_memory_bytes")
                              else _history_cost(document.undo_stack + document.redo_stack)),
            "history_limit_steps": document.history_limit, "history_limit_bytes": document.history_bytes,
        }
    return data


def publish_diagnostics(document=None, config=None, stage=None):
    diagnostics = collect_diagnostics(document, config)
    destination = os.environ.get("SPARKER_DEBUG_STATE")
    if not destination:
        return diagnostics
    path = Path(destination)
    temporary = None
    try:
        state = {}
        if path.is_file() and path.stat().st_size <= 262144:
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(previous, dict):
                    state.update(previous)
            except (ValueError, UnicodeError):
                pass
        state["diagnostics"] = diagnostics
        state["updated_at"] = time.time()
        if stage is not None:
            state["stage"] = stage
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="debug-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
        os.replace(temporary, path)
    except OSError:
        # Diagnostic visibility must never interrupt an editing operation.
        pass
    finally:
        if temporary is not None and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return diagnostics


def format_diagnostics(data):
    program, python, venv = data["program"], data["python"], data["venv"]
    lines = [f"{program['name']} {program['version']} · PID {program['pid']}",
             f"Program: {program['root']}", f"Working directory: {program['working_directory']}",
             f"Python {python['version']} ({python['architecture']}): {python['executable']}",
             f".venv: {venv['path']} · {'present' if venv['exists'] else 'missing'} · {'active' if venv['active'] else 'inactive'}",
             f"Prefix: {venv['prefix']}", f"Base prefix: {venv['base_prefix']}",
             f"Settings: {data['settings_path']}",
             f"Exports: {data['settings']['export.directory']} · lossless={data['settings']['export.lossless']}",
             f"Terminal: {data['terminal']['columns']}×{data['terminal']['rows']} · stdout TTY={data['terminal']['stdout_tty']}",
             "Dependencies: " + ", ".join(f"{name}={version}" for name, version in data["dependencies"].items())]
    if program["memory_bytes"] is not None:
        lines.append(f"Process memory ({program.get('memory_kind', 'resident')}): "
                     f"{program['memory_bytes'] / 1048576:.1f} MiB · mode={program.get('memory_mode', 'standard')}")
    if venv.get("active_path"):
        lines.append(f"Active virtual environment: {venv['active_path']}")
    if "document" in data:
        doc = data["document"]
        lines += [f"Canvas: {doc['width']}×{doc['height']} RGBA · {doc['layers']} layers · active {doc['active_layer']}: {doc['active_name']}",
                  f"Document: {'unsaved' if doc['dirty'] else 'saved'} · revision {doc['revision']} · selection={doc['selection']}",
                  f"History: {doc['undo_steps']} undo / {doc['redo_steps']} redo · {doc['history_bytes'] / 1048576:.2f} MiB"]
    lines.extend("Settings warning: " + warning for warning in data["settings_warnings"])
    return "\n".join(lines)
