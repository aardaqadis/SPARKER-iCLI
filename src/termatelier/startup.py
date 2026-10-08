"""Standard-library startup window and first-frame handshake.

This file can run directly before the project environment has been installed.
It deliberately imports neither the editor nor any third-party packages.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import tempfile
import time
import textwrap
from typing import Any


STATE_ENV = "SPARKER_STARTUP_STATE"
DEBUG_STATE_ENV = "SPARKER_DEBUG_STATE"
FINISHED_PHASES = frozenset({"ready", "failed", "closed"})
STOP_PHASES = frozenset({"failed", "closed"})
TRANSPARENT_COLOR = "#010101"


def read_state(path: Path) -> dict[str, Any]:
    """A concurrent writer or unavailable file must never block startup."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def write_state(path: Path, phase: str, status: str = "") -> None:
    """Replace the complete state in one filesystem operation."""
    state = read_state(path)
    state.update(phase=phase, status=status)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def signal_ready() -> bool:
    """Tell the launcher window that the editor's first frame is visible.

    Safe for direct, command-only and test launches without a startup window.
    A splash failure must never prevent an editor from opening.
    """
    value = os.environ.get(STATE_ENV)
    if not value:
        return False
    path = Path(value)
    if not path.is_file():
        return False
    try:
        write_state(path, "ready", "Ready")
        return True
    except (OSError, ValueError):
        return False


def write_debug_snapshot(snapshot: dict[str, Any]) -> bool:
    """Merge editor telemetry into the launcher's separate runtime channel."""
    value = os.environ.get(DEBUG_STATE_ENV)
    if not value:
        return False
    path = Path(value)
    temporary: Path | None = None
    try:
        state = read_state(path)
        state.update(snapshot)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False)
        os.replace(temporary, path)
        return True
    except (OSError, ValueError, TypeError):
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parent_alive(pid: int) -> bool:
    """Check the launcher without keeping its process alive or exposing data."""
    if pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is inconclusive.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False


def should_close(state: dict[str, Any], launcher_is_alive: bool,
                 elapsed: float = 0.0, minimum_seconds: float = 0.0) -> bool:
    """Hold a ready logo for its minimum duration; never delay errors or exit."""
    return (not launcher_is_alive or state.get("phase") in STOP_PHASES or
            (state.get("phase") == "ready" and elapsed >= minimum_seconds))


def helper_should_exit(state: dict[str, Any], launcher_is_alive: bool) -> bool:
    """The debug overlay outlives the logo, until the editor's launcher exits."""
    return not launcher_is_alive or state.get("phase") in STOP_PHASES


def helper_arguments(state: Path, parent_pid: int, logo: Path | None = None) -> list[str]:
    """Build direct-file arguments; usable before editable installation."""
    asset = logo or Path(__file__).with_name("assets") / "logo.png"
    return [str(Path(__file__).resolve()), "--state", str(state),
            "--parent-pid", str(parent_pid), "--logo", str(asset)]


def _runtime_helpers():
    # Direct execution is used before the editable package and Pillow exist.
    if __package__:
        from .config import RuntimeConfig
        from .diagnostics import collect_diagnostics
    else:
        from config import RuntimeConfig
        from diagnostics import collect_diagnostics
    return RuntimeConfig, collect_diagnostics


def parse_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if not separator or not name.strip():
            raise ValueError("--set expects a setting name and value, e.g. debug.enabled=false")
        overrides[name.strip()] = content
    return overrides


def format_debug_text(state: dict[str, Any], diagnostics: dict[str, Any], *,
                      elapsed: float = 0.0, detail: str = "full") -> str:
    """Readable curated diagnostics without dumping credentials or environment."""
    program = diagnostics.get("program", {})
    python = diagnostics.get("python", {})
    venv = diagnostics.get("venv", {})
    settings = diagnostics.get("settings", {})
    document = diagnostics.get("document", {})
    terminal = diagnostics.get("terminal", {})
    system = diagnostics.get("platform", {})
    runtime = diagnostics.get("runtime", {})
    lines = [f"SPARKER iCLI {program.get('version', '')}  /  DEBUG",
             f"Startup: {state.get('phase', 'starting')} · {state.get('status', 'Starting')}",
             f"Session: {elapsed:.1f}s · launcher PID {state.get('parent_pid', '?')} · app PID {program.get('pid', '?')}"]

    def append(name: str, value: Any) -> None:
        if value is not None:
            text = f"{name}: {value}"
            lines.extend(textwrap.wrap(text, width=112, subsequent_indent="  ") or [text])

    append("Python", f"{python.get('version', '?')} · {python.get('implementation', '')} · {python.get('architecture', '')}")
    append("Interpreter", python.get("executable"))
    append(".venv", f"{'present' if venv.get('exists') else 'missing'} · {'active' if venv.get('active') else 'inactive'} · {venv.get('path', '?')}")
    if document:
        append("Canvas", f"{document.get('width', '?')}×{document.get('height', '?')} {document.get('pixel_mode', 'RGBA')} · {document.get('layers', '?')} layers")
        append("Layer", f"{document.get('active_layer', '?')} · {document.get('active_name', '?')} · revision {document.get('revision', '?')} · {'unsaved' if document.get('dirty') else 'saved'}")
        append("History", f"{document.get('undo_steps', 0)} undo / {document.get('redo_steps', 0)} redo · selection {document.get('selection', False)}")
    append("Exports", settings.get("export.directory"))
    if detail == "compact":
        append("View", runtime.get("view", runtime.get("tool")))
        return "\n".join(lines[:12])

    append("Program", program.get("root"))
    append("Runtime stage", diagnostics.get("startup", {}).get("stage"))
    append("Working directory", program.get("working_directory"))
    memory = program.get("memory_bytes")
    append("Memory / uptime", f"{memory / 1048576:.1f} MiB {program.get('memory_kind', 'resident')} / {program.get('uptime_seconds', '?')}s" if isinstance(memory, (int, float)) else f"unavailable / {program.get('uptime_seconds', '?')}s")
    append("pyvenv.cfg", f"{'present' if venv.get('config_exists') else 'missing'} · {venv.get('config_path', '?')}")
    append("Prefix", venv.get("prefix"))
    append("Active environment", venv.get("active_path"))
    append("Base prefix", venv.get("base_prefix"))
    for key in ("version", "home", "executable", "include-system-site-packages"):
        append(f"Venv {key}", venv.get("config", {}).get(key))
    append("Platform", " ".join(str(system.get(key, "")) for key in ("system", "release", "version")))
    append("Terminal", f"{terminal.get('columns', '?')}×{terminal.get('rows', '?')} · stdin TTY={terminal.get('stdin_tty', False)} · stdout TTY={terminal.get('stdout_tty', False)}")
    append("Packages", " · ".join(f"{name} {version}" for name, version in diagnostics.get("dependencies", {}).items()))
    append("Settings", diagnostics.get("settings_path"))
    append("Debug", f"{settings.get('debug.detail', 'full')} · {settings.get('debug.refresh_ms', 500)}ms · font {settings.get('debug.font_size', 11)} · opacity {settings.get('debug.opacity', 1)}")
    append("Export / view", f"lossless={settings.get('export.lossless', True)} · preview={settings.get('view.resampling', 'nearest')} · startup ≥{settings.get('startup.minimum_seconds', 2.5)}s")
    if document:
        append("History memory", f"{document.get('history_bytes', 0) / 1048576:.2f} MiB · limit {document.get('history_limit_steps', '?')} steps / {document.get('history_limit_bytes', 0) / 1048576:.0f} MiB")
    for name, value in runtime.items():
        if isinstance(value, dict):
            value = " · ".join(f"{key}={item}" for key, item in value.items())
        append(f"Runtime {name}", value)
    for warning in diagnostics.get("settings_warnings", [])[:2]:
        append("Settings warning", warning)
    if len(lines) > 43:
        lines = lines[:42] + ["More details: debug info --json"]
    return "\n".join(lines)


def configure_overlay(window: Any, *, opacity: float = 1.0, topmost: bool = True) -> bool:
    """Use real Windows color-key transparency, with click-through text."""
    window.configure(bg=TRANSPARENT_COLOR)
    window.overrideredirect(True)
    window.attributes("-topmost", topmost)
    window.attributes("-alpha", opacity)
    if os.name != "nt":
        # Do not create an opaque imitation on platforms without this facility.
        return False
    window.attributes("-transparentcolor", TRANSPARENT_COLOR)
    window.update_idletasks()
    try:
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetParent.argtypes = (wintypes.HWND,)
        user.GetParent.restype = wintypes.HWND
        user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user.GetWindowThreadProcessId.restype = wintypes.DWORD
        child = window.winfo_id()
        handle = user.GetParent(child) or child
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value != os.getpid():
            return True  # Never alter another process's window styles.
        get_style = getattr(user, "GetWindowLongPtrW", user.GetWindowLongW)
        set_style = getattr(user, "SetWindowLongPtrW", user.SetWindowLongW)
        get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_style.restype = ctypes.c_ssize_t
        # TRANSPARENT | LAYERED | TOOLWINDOW | NOACTIVATE; no keyboard/mouse capture.
        set_style(handle, -20, get_style(handle, -20) | 0x20 | 0x80000 | 0x80 | 0x08000000)
    except (AttributeError, OSError):
        pass  # The transparent Tk window remains usable without native extras.
    return True


def show_startup(state_path: Path, parent_pid: int, logo_path: Path, *,
                 debug_path: Path | None = None, no_splash: bool = False,
                 no_debug: bool = False, overrides: dict[str, Any] | None = None) -> int:
    if helper_should_exit(read_state(state_path), parent_alive(parent_pid)):
        return 0
    try:
        import tkinter as tk
        RuntimeConfig, collect_diagnostics = _runtime_helpers()
    except ImportError:
        return 0
    root = None
    try:
        config = RuntimeConfig.load(overrides=overrides)
        root = tk.Tk()
        root.withdraw()
        root.title("SPARKER iCLI runtime")
        splash = None
        overlay = None
        status = tk.StringVar(value="Starting")
        debug_text = tk.StringVar()
        logo = None
        started = time.monotonic()
        minimum_seconds = float(config.get("startup.minimum_seconds"))
        if not no_splash:
            splash = tk.Toplevel(root)
            splash.withdraw()
            splash.title("SPARKER iCLI")
            splash.configure(bg="#000000")
            splash.overrideredirect(True)
            splash.attributes("-topmost", True)
            logo = tk.PhotoImage(file=str(logo_path))
            available = max(160, min(720, splash.winfo_screenwidth() - 80))
            if logo.width() > available:
                numerator = max(1, int(available / logo.width() * 8))
                logo = logo.zoom(numerator).subsample(8)
            tk.Label(splash, image=logo, background="#000000", borderwidth=0,
                     highlightthickness=0).pack(padx=30, pady=(28, 20))
            tk.Label(splash, textvariable=status, bg="#000000", fg="#8f8f8f",
                     font=("Segoe UI", 10), borderwidth=0).pack(pady=(0, 15))
            progress = tk.Canvas(splash, height=2, bg="#171717", borderwidth=0,
                                 highlightthickness=0)
            progress.pack(fill="x", padx=30, pady=(0, 26))
            pulse = progress.create_rectangle(0, 0, 70, 2, fill="#ed9635", outline="")
            splash.update_idletasks()
            width, height = splash.winfo_reqwidth(), splash.winfo_reqheight()
            left = (splash.winfo_screenwidth() - width) // 2
            top = (splash.winfo_screenheight() - height) // 2
            splash.geometry(f"{width}x{height}+{left}+{top}")
            splash.deiconify()
            drag = [0, 0]

            def begin_drag(event: Any) -> None:
                drag[:] = [event.x_root - splash.winfo_x(), event.y_root - splash.winfo_y()]

            def move_window(event: Any) -> None:
                splash.geometry(f"+{event.x_root - drag[0]}+{event.y_root - drag[1]}")

            splash.bind("<ButtonPress-1>", begin_drag)
            splash.bind("<B1-Motion>", move_window)

        # Unlike terminal cells, this native layer has a truly clear background.
        overlay = tk.Toplevel(root)
        overlay.withdraw()
        overlay.title("SPARKER iCLI debug")
        transparent_supported = configure_overlay(overlay)
        debug_label = tk.Label(overlay, textvariable=debug_text, bg=TRANSPARENT_COLOR,
                               fg=str(config.get("debug.color")), justify="left", anchor="nw",
                               font=("Consolas", int(config.get("debug.font_size"))),
                               borderwidth=0, highlightthickness=0, padx=0, pady=0)
        debug_label.pack(anchor="nw")
        overlay.geometry("+0+0")
        tick = 0
        next_debug = 0.0
        initial_diagnostics = collect_diagnostics(config=config, program_root=Path(__file__).resolve().parents[2])

        def poll() -> None:
            nonlocal tick, next_debug, splash, config
            state = read_state(state_path)
            alive = parent_alive(parent_pid)
            if helper_should_exit(state, alive):
                root.destroy()
                return
            now = time.monotonic()
            elapsed = now - started
            if splash is not None:
                if should_close(state, alive, elapsed, minimum_seconds):
                    splash.destroy()
                    splash = None
                else:
                    status.set(str(state.get("status") or "Starting"))
                    span = max(1, progress.winfo_width() - 70)
                    cycle = tick % (2 * span)
                    x = cycle if cycle <= span else 2 * span - cycle
                    progress.coords(pulse, x, 0, x + 70, 2)
                    tick += 7
            # No color-key overlay is available on this desktop. Once the logo
            # closes, an invisible Tk process has no work left to perform.
            if splash is None and not transparent_supported:
                root.destroy()
                return
            if now >= next_debug:
                config = RuntimeConfig.load(overrides=overrides)
                snapshot = read_state(debug_path) if debug_path else {}
                diagnostics = snapshot.get("diagnostics") or initial_diagnostics
                live_settings = diagnostics.get("settings") if snapshot else None
                if isinstance(live_settings, dict):
                    # The editor owns session overrides; `debug on` also works
                    # after a --no-debug launch without restarting the helper.
                    config = RuntimeConfig.load(overrides=live_settings)
                if config.get("memory.mode", "standard") == "low":
                    # Switching a running session to low memory also releases
                    # this helper, rather than retaining a hidden Tk process.
                    root.destroy()
                    return
                next_debug = now + int(config.get("debug.refresh_ms")) / 1000
                enabled = (bool(config.get("debug.enabled")) and
                           (not no_debug or bool(live_settings)) and transparent_supported)
                if enabled:
                    debug_text.set(format_debug_text(state, diagnostics, elapsed=elapsed,
                                                     detail=str(config.get("debug.detail"))))
                    debug_label.configure(fg=str(config.get("debug.color")),
                                          font=("Consolas", int(config.get("debug.font_size"))))
                    overlay.attributes("-alpha", float(config.get("debug.opacity")))
                    overlay.attributes("-topmost", bool(config.get("debug.topmost")))
                    overlay.update_idletasks()
                    overlay.geometry("+0+0")
                    overlay.deiconify()
                else:
                    overlay.withdraw()
            root.after(80, poll)

        root.after(0, poll)
        root.mainloop()
        return 0
    except (tk.TclError, OSError, ValueError):
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPARKER iCLI startup helper")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--logo", type=Path, default=Path(__file__).with_name("assets") / "logo.png")
    parser.add_argument("--check", action="store_true", help="Check lifecycle inputs without opening a window")
    parser.add_argument("--debug-state", type=Path)
    parser.add_argument("--no-splash", action="store_true")
    parser.add_argument("--no-debug", action="store_true")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--configure", action="store_true", help="Validate launcher settings without opening windows")
    args = parser.parse_args(argv)
    try:
        overrides = parse_overrides(args.set)
        RuntimeConfig, _ = _runtime_helpers()
        config = RuntimeConfig.load(overrides=overrides)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.configure:
        existing = json.loads(os.environ.get("SPARKER_CONFIG_OVERRIDES", "{}"))
        override_names = set(existing) | set(overrides)
        print(json.dumps({"minimum_seconds": config.get("startup.minimum_seconds"),
                          "debug_enabled": config.get("debug.enabled"),
                          "memory_mode": config.get("memory.mode", "standard"),
                          "overrides": {name: config.get(name) for name in sorted(override_names)}}))
        return 0
    if args.state is None or args.parent_pid is None:
        parser.error("--state and --parent-pid are required to run the startup helper")
    if args.check:
        state = read_state(args.state)
        print(json.dumps({"logo_exists": args.logo.is_file(), "state": state,
                          "parent_alive": parent_alive(args.parent_pid),
                          "should_close": should_close(state, parent_alive(args.parent_pid))}))
        return 0 if args.logo.is_file() else 1
    return show_startup(args.state, args.parent_pid, args.logo, debug_path=args.debug_state,
                        no_splash=args.no_splash, no_debug=args.no_debug, overrides=overrides)


if __name__ == "__main__":
    raise SystemExit(main())
