"""Manage the live CLI painting window without changing or exporting artwork.

The helper owns its Tk event loop in a separate process. Its disposable frame
files live in the operating system's temporary folder, outside the project.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid


PREVIEW_DEFAULTS = {
    "preview.enabled": True,
    "preview.width": 800,
    "preview.height": 600,
    "preview.topmost": False,
    "preview.resampling": "bilinear",
    "preview.refresh_ms": 150,
}


def desktop_available():
    """Whether Tk can reach a desktop, without importing it or opening a window.

    Native macOS Tk uses Aqua and Windows Tk does not need a display variable.
    Unix Tk uses X11, including XWayland; WAYLAND_DISPLAY alone is insufficient.
    Actual Tk availability is checked by the optional helper when it starts.
    """
    return os.name == "nt" or sys.platform == "darwin" or bool(os.environ.get("DISPLAY"))


def atomic_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PreviewController:
    """A restartable window tied to the editor process and CLI lifecycle."""

    def __init__(self, config=None):
        if config is None:
            from .config import RuntimeConfig
            config = RuntimeConfig.load()
        self.config = config
        self._temporary = None
        self._process = None
        self._generation = 0
        self._frames = []
        self._document = None
        self._frame_signature = None
        self._state_fingerprint = None
        self._lock = threading.RLock()
        self.error = ""
        atexit.register(self.close)

    @property
    def running(self):
        if self._process is None:
            return False
        if self._process.poll() is None:
            return True
        self._read_error()
        return False

    @property
    def state_path(self):
        """Disposable IPC path, useful for diagnostics and integration tests."""
        return Path(self._temporary.name) / "state.json" if self._temporary else None

    def _options(self):
        options = {}
        for name, fallback in PREVIEW_DEFAULTS.items():
            try:
                value = self.config.get(name) if self.config is not None else fallback
            except (ValueError, KeyError):
                value = fallback
            options[name.split(".", 1)[1]] = fallback if value is None else value
        return options

    def _read_error(self):
        if self._temporary is None:
            return
        try:
            state = json.loads((Path(self._temporary.name) / "window.json").read_text(encoding="utf-8"))
            if state.get("error"):
                self.error = str(state["error"])
        except (OSError, ValueError):
            if self._process is not None and self._process.returncode:
                self.error = "The preview helper could not start."

    def start(self, document, metadata=None):
        with self._lock:
            if not self._options()["enabled"]:
                self.close()
                return False
            if not desktop_available():
                self.close()
                self.error = "No desktop display is available; use the terminal painting view."
                return False
            if self.running:
                return self.update(document, metadata)
            self.close()
            self.error = ""
            try:
                # Windows can expose TEMP through an 8.3 alias. Publish the
                # canonical directory so IPC and diagnostic paths agree.
                self._temporary = tempfile.TemporaryDirectory(
                    prefix="sparker-preview-", dir=Path(tempfile.gettempdir()).resolve())
                self._generation = 0
                self._frames = []
                self._snapshot(document, metadata)
                executable = Path(sys.executable)
                if os.name == "nt" and executable.with_name("pythonw.exe").is_file():
                    executable = executable.with_name("pythonw.exe")
                command = [str(executable), "-m", "termatelier.preview_window", "--state",
                           str(self.state_path), "--parent-pid", str(os.getpid())]
                self._process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                return True
            except (OSError, ValueError, TypeError) as error:
                self.error = f"Preview unavailable: {error}"
                self.close()
                return False

    @staticmethod
    def _artwork_signature(document):
        """Check committed state without touching any full-resolution pixels.

        Interactive commands commit pixel edits through Document.revision. The
        layer identities and properties also catch replacements and visibility
        changes made outside an edit. In-place PIL mutations remain supported
        by update's conservative default, artwork_changed=True.
        """
        return (id(document), document.revision, document.size,
                tuple((id(layer), id(layer.image), id(layer.mask), layer.visible,
                       layer.opacity, layer.blend) for layer in document.layers))

    def _snapshot(self, document, metadata, *, artwork_changed=True):
        signature = self._artwork_signature(document)
        fresh_frame = (artwork_changed or not self._frames or
                       signature != self._frame_signature)
        directory = Path(self._temporary.name)
        frame = self._frames[-1] if self._frames else None
        details = {"title": str(document.metadata.get("title", "Untitled")),
                   "layers": len(document.layers), "dirty": bool(document.dirty),
                   "revision": document.revision}
        details.update(metadata or {})
        settings = {"metadata": details, "options": self._options()}
        fingerprint = json.dumps(settings, ensure_ascii=False, default=str)
        if not fresh_frame and fingerprint == self._state_fingerprint:
            return
        generation = self._generation + 1
        if fresh_frame:
            image = document.composite()
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            frame = directory / f"frame-{generation}-{uuid.uuid4().hex}.png"
            # IPC favors latency over storage. PNG compression level zero still
            # preserves every RGBA byte, including colors in transparent pixels.
            # Published exports retain their own compression preferences.
            image.save(frame, format="PNG", compress_level=0)
        try:
            atomic_json(self.state_path, {"generation": generation, "frame": frame.name,
                                         "width": document.width, "height": document.height,
                                         **settings})
        except Exception:
            if fresh_frame:
                frame.unlink(missing_ok=True)
            raise
        self._generation = generation
        self._document = document  # Prevent object-id reuse across replacements.
        self._frame_signature = signature
        self._state_fingerprint = fingerprint
        if fresh_frame:
            self._frames.append(frame)
        # Keep previous generations long enough for a concurrent read. A helper
        # encountering a removed old frame simply retains its last good painting.
        while len(self._frames) > 3:
            self._frames.pop(0).unlink(missing_ok=True)

    def update(self, document, metadata=None, *, artwork_changed=True):
        """Publish changed artwork or reuse pixels for a read-only command.

        Pass artwork_changed=False only when the caller knows the command did
        not mutate PIL images in place. Revision/layer changes still regenerate
        the frame, and changed metadata/preferences are always published.
        """
        with self._lock:
            if not self._options()["enabled"]:
                self.close()
                return False
            if not self.running:
                return False
            try:
                self._snapshot(document, metadata, artwork_changed=artwork_changed)
                return True
            except (OSError, ValueError, TypeError) as error:
                self.error = f"Preview update unavailable: {error}"
                return False

    def reopen(self, document, metadata=None):
        self.close()
        return self.start(document, metadata)

    def close(self):
        with self._lock:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                try:
                    (Path(self._temporary.name) / "closed").touch()
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                except OSError:
                    pass
            if self._temporary is not None:
                try:
                    self._temporary.cleanup()
                except OSError:
                    pass
                self._temporary = None
            self._frames = []
            self._document = None
            self._frame_signature = None
            self._state_fingerprint = None
