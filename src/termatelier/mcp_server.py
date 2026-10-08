"""Optional stdio MCP facade for real editable SPARKER documents.

An MCP host can create a bounded session, edit it through the same command
engine, inspect pixels, and explicitly save/export files. It does not launch
the terminal painter or run shell commands, scripts, settings or AI services.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from threading import RLock
from uuid import uuid4

from PIL import Image

from .commands import CommandSession, tokenize
from .config import RuntimeConfig
from .model import Document, Layer, valid_size
from .storage import (export, import_document, load_project, png_bytes,
                      save_project)

# Deliberately explicit: additions to the interactive CLI do not automatically
# become remotely callable. Files and service configuration have separate tools.
EDIT_COMMANDS = frozenset((
    "tool", "tools", "apply", "tool-options", "color", "brush", "stroke", "pencil",
    "erase", "eraser", "fill", "gradient", "line", "rectangle", "rect", "ellipse",
    "text", "text-edit", "pick", "clear", "layer", "select", "move", "resize",
    "canvas", "transform", "crop", "filter", "copy", "cut", "paste", "palette",
    "guides", "grid", "meta", "undo", "redo", "history", "pixel", "polygon",
    "polyline", "bezier", "align", "selection", "adjust", "colors", "geometry",
    "tone", "effect-filter", "measure", "distribute", "clone", "heal",
    "perspective-clone", "smudge", "retouch", "airbrush", "ink", "natural",
    "select-color", "scissors", "foreground", "path", "channel", "fx",
    "tool-source", "retouch-options", "features",
))


class SessionStore:
    """Serialized session state with independent and aggregate memory budgets."""
    def __init__(self, config=None, max_sessions=8, max_bytes=128 * 1024 * 1024):
        if type(max_sessions) is not int or not 1 <= max_sessions <= 32:
            raise ValueError("MCP session limit must be 1–32.")
        if type(max_bytes) is not int or not 8 * 1024 * 1024 <= max_bytes <= 512 * 1024 * 1024:
            raise ValueError("MCP retained-pixel budget must be 8–512 MiB.")
        overrides = {"memory.mode": "low", "debug.enabled": False, "preview.enabled": False}
        self.config = (RuntimeConfig(config.as_dict(), path=config.path, overrides=overrides)
                       if config else RuntimeConfig.load(overrides=overrides))
        self.max_sessions, self.max_bytes = max_sessions, max_bytes
        self.sessions = {}
        self.lock = RLock()

    def _session(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity) or identity not in self.sessions:
            raise ValueError("Unknown MCP painting session. Create or open a document first.")
        return self.sessions[identity]

    @staticmethod
    def _bytes(session):
        doc = session.document
        state = (doc.size, doc.layers, doc.active, doc.selection, doc.metadata, doc.settings, doc.revision)
        total = doc._snapshot_bytes(state) + doc.history_memory_bytes
        if doc.clipboard:
            total += doc.clipboard[0].width * doc.clipboard[0].height * 4
        return total

    def _budget(self, candidate=None, replacing=None):
        total = sum(self._bytes(session) for name, session in self.sessions.items() if name != replacing)
        if candidate is not None:
            total += self._bytes(candidate)
        if total > self.max_bytes:
            raise ValueError("MCP sessions exceed their retained pixel/history memory budget. Close a session or use a smaller canvas.")

    def _register(self, document, path=None):
        if len(self.sessions) >= self.max_sessions:
            raise ValueError("MCP session limit reached. Close a painting session first.")
        session = CommandSession(document, project_path=path, config=self.config)
        self._budget(session)
        identity = uuid4().hex
        self.sessions[identity] = session
        return self.info(identity)

    def create(self, width=96, height=64, transparent=False, title="Untitled"):
        with self.lock:
            valid_size(width, height)
            if type(transparent) is not bool or not isinstance(title, str) or len(title) > 256:
                raise ValueError("Use a boolean transparency flag and a title up to 256 characters.")
            doc = Document(width, height)
            if transparent:
                doc.layers = [Layer("Paint", Image.new("RGBA", doc.size))]
                doc.active = 0
            doc.metadata["title"] = title
            return self._register(doc)

    def open(self, path):
        with self.lock:
            source = Path(path).expanduser().resolve()
            if source.suffix.lower() == ".tart":
                doc = load_project(source)
            elif source.suffix.lower() == ".ora":
                from .ora_tools import load_ora
                doc = load_ora(source)
            else:
                doc = import_document(source)
            return self._register(doc, source if source.suffix.lower() == ".tart" else None)

    def info(self, identity):
        with self.lock:
            session = self._session(identity)
            doc = session.document
            return {"session_id": identity, "width": doc.width, "height": doc.height,
                "title": doc.metadata.get("title", "Untitled"), "active_layer": doc.active + 1,
                "layers": [{"index": index + 1, "name": layer.name, "opacity": layer.opacity,
                            "visible": layer.visible, "locked": layer.locked, "blend": layer.blend,
                            "mask": layer.mask is not None} for index, layer in enumerate(doc.layers)],
                "selection": list(doc.selection.getbbox()) if doc.selection and doc.selection.getbbox() else None,
                "revision": doc.revision, "dirty": doc.dirty, "undo_steps": len(doc.undo_stack),
                "redo_steps": len(doc.redo_stack), "retained_bytes": self._bytes(session)}

    def edit(self, identity, commands):
        with self.lock:
            session = self._session(identity)
            if not isinstance(commands, list) or not 1 <= len(commands) <= 64 or any(not isinstance(line, str) or len(line) > 32768 for line in commands):
                raise ValueError("Supply 1–64 editing command strings, each at most 32,768 characters.")
            for line in commands:
                tokens = tokenize(line)
                if not tokens or tokens[0].lower() not in EDIT_COMMANDS:
                    raise ValueError("This MCP tool accepts only painting/edit commands. Use explicit open/save/export tools for files; scripts, settings, MCP/AI and shell operations are excluded.")
                if any(token == "--file" or token.startswith("--file=") for token in tokens):
                    raise ValueError("MCP editing accepts literal text; text --file is a separate file operation and is unavailable here.")
            doc = session.document
            # Keep immutable history references, but copy the current editable
            # state and clipboard before the first command for whole-batch rollback.
            before = doc.snapshot()
            undo, redo = list(doc.undo_stack), list(doc.redo_stack)
            clipboard = (doc.clipboard[0].copy(), doc.clipboard[1]) if doc.clipboard else None
            previous_serial, saved_revision = doc.serial, doc.saved_revision
            try:
                results = [session.execute(line).text for line in commands]
                self._budget(session, replacing=identity)
            except Exception:
                doc.cancel()
                doc.restore(before)
                doc.undo_stack, doc.redo_stack, doc.clipboard = undo, redo, clipboard
                doc.serial, doc.saved_revision = previous_serial, saved_revision
                raise
            return {**self.info(identity), "results": results}

    def save(self, identity, path):
        with self.lock:
            session = self._session(identity)
            target = Path(path).expanduser().resolve()
            if target.suffix.lower() == ".ora":
                from .ora_tools import save_ora
                target = save_ora(session.document, target, config=self.config)
            elif target.suffix.lower() == ".tart":
                save_project(session.document, target)
                session.project_path = target
            else:
                raise ValueError("Editable project output uses .tart or .ora. Use sparker_export for flattened images/text.")
            return {**self.info(identity), "path": str(target)}

    def export(self, identity, path=None, scale=1, columns=100, allow_lossy=False):
        with self.lock:
            session = self._session(identity)
            if type(allow_lossy) is not bool or type(columns) is not int or not 1 <= columns <= 500:
                raise ValueError("Use a boolean lossy flag and 1–500 text columns.")
            output = export(session.document, path, columns, scale=scale,
                            allow_lossy=allow_lossy, config=self.config)
            return {"session_id": identity, "path": str(output), "canvas_width": session.document.width,
                    "canvas_height": session.document.height, "scale": scale}

    def preview(self, identity, maximum=1024):
        with self.lock:
            if type(maximum) is not int or not 16 <= maximum <= 2048:
                raise ValueError("Preview edge limit must be 16–2048 pixels.")
            doc = self._session(identity).document
            ratio = min(1.0, maximum / max(doc.size))
            size = (max(1, round(doc.width * ratio)), max(1, round(doc.height * ratio)))
            affine = (doc.width / size[0], 0, 0, 0, doc.height / size[1], 0)
            return png_bytes(doc.composite_view(size, affine, Image.Resampling.NEAREST))

    def close(self, identity):
        with self.lock:
            self._session(identity)
            del self.sessions[identity]
            return {"closed": identity, "sessions_remaining": len(self.sessions)}


def make_server(config=None, max_sessions=8, max_bytes=128 * 1024 * 1024):
    try:
        from mcp.server.fastmcp import FastMCP, Image as MCPImage
    except ImportError:
        raise ValueError('Install optional MCP support with python -m pip install ".[mcp]" or "sparker-icli[mcp]".') from None
    store = SessionStore(config, max_sessions, max_bytes)
    server = FastMCP("SPARKER iCLI", instructions="Editable raster painting sessions. Each session is independent of the terminal UI. Use editing tools for pixels and explicit file tools for opening/saving/exporting. Native .tart preserves layers, selections, paths and metadata.")

    @server.tool()
    def sparker_create(width: int = 96, height: int = 64, transparent: bool = False, title: str = "Untitled") -> dict:
        """Create a bounded editable raster document and return its session ID."""
        return store.create(width, height, transparent, title)

    @server.tool()
    def sparker_open(path: str) -> dict:
        """Explicitly read a local .tart/.ora project or image into a new session."""
        return store.open(path)

    @server.tool()
    def sparker_info(session_id: str) -> dict:
        """Read canvas dimensions, layers, undo counts and retained pixel memory."""
        return store.info(session_id)

    @server.tool()
    def sparker_edit(session_id: str, commands: list[str]) -> dict:
        """Apply 1–64 whitelisted native edit commands atomically; no shell, scripts, file commands or external AI calls. A failed command rolls back the entire batch."""
        return store.edit(session_id, commands)

    @server.tool()
    def sparker_save(session_id: str, path: str) -> dict:
        """Explicitly save editable .tart or .ora output to the requested path."""
        return store.save(session_id, path)

    @server.tool()
    def sparker_export(session_id: str, path: str | None = None, scale: int = 1, columns: int = 100, allow_lossy: bool = False) -> dict:
        """Explicitly export lossless PNG/WebP/TIFF or text outside the project folder; 1–16x scale preserves crisp pixels. Other formats require allow_lossy."""
        return store.export(session_id, path, scale, columns, allow_lossy)

    @server.tool()
    def sparker_preview(session_id: str, maximum: int = 1024):
        """Return a PNG painting preview, at original size or a bounded maximum edge."""
        return MCPImage(data=store.preview(session_id, maximum), format="png")

    @server.tool()
    def sparker_close(session_id: str) -> dict:
        """Release the document, clipboard and undo history of a painting session."""
        return store.close(session_id)

    @server.tool()
    def sparker_sessions() -> dict:
        """List open painting sessions and the server's aggregate memory/session limits."""
        with store.lock:
            return {"sessions": [store.info(name) for name in store.sessions], "max_sessions": store.max_sessions,
                    "retained_bytes": sum(store._bytes(session) for session in store.sessions.values()), "max_bytes": store.max_bytes}

    # Useful to embedding applications/tests; the protocol only exposes tools.
    server.sparker_store = store
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="SPARKER iCLI optional stdio MCP painting server")
    parser.add_argument("--config", type=Path, help="Preferences file; connection tokens remain environment variables")
    parser.add_argument("--max-sessions", type=int, default=8)
    parser.add_argument("--max-memory-mb", type=int, default=128)
    options = parser.parse_args(argv)
    try:
        config = RuntimeConfig.load(path=options.config) if options.config else None
        server = make_server(config, options.max_sessions, options.max_memory_mb * 1024 * 1024)
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
