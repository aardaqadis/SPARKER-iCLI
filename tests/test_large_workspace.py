"""Large documents stay idle during informational CLI commands and pointer hover."""
import json

import pytest
from textual.widgets import Static

from termatelier.app import Studio
from termatelier.cli_app import CLIApp
from termatelier.commands import CommandSession
from termatelier.model import Document
from termatelier.preview import PreviewController


class Process:
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0


@pytest.mark.asyncio
async def test_cli_information_reuses_megapixel_preview(tmp_path, monkeypatch):
    monkeypatch.setattr("termatelier.preview.desktop_available", lambda: True)
    monkeypatch.setattr("termatelier.preview.subprocess.Popen", lambda *a, **kw: Process())
    doc = Document(1024, 1024)
    preview = PreviewController()
    app = CLIApp(CommandSession(doc), preview=preview)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        before = json.loads(preview.state_path.read_text())
        def unexpected_composite(*args, **kwargs):
            pytest.fail("Informational commands must reuse the unchanged full-resolution frame")
        monkeypatch.setattr(doc, "composite", unexpected_composite)
        for command in ("info", "history", "help brush", "tools count", "config get brush.size"):
            app.workspace.execute_command(command)
        await pilot.pause()
        after = json.loads(preview.state_path.read_text())
        assert after["frame"] == before["frame"]
        assert (after["width"], after["height"]) == (1024, 1024)
        assert not doc.undo_stack and not doc.dirty
    assert not preview.running


@pytest.mark.asyncio
async def test_embedded_cli_information_does_not_rebuild_painter(monkeypatch):
    app = Studio(Document(1024, 1024))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_console()
        await pilot.pause()
        def unexpected_sync():
            pytest.fail("Read-only CLI commands must not rebuild the hidden painter")
        with monkeypatch.context() as patch:
            patch.setattr(app, "sync_ui", unexpected_sync)
            for command in ("info", "help", "history", "tools count", "config list"):
                app.execute_command(command)
            await pilot.pause()
        assert app.doc.size == (1024, 1024) and not app.doc.dirty
        app.action_console()
        await pilot.pause()
        assert app.canvas.has_focus


@pytest.mark.asyncio
async def test_status_hover_updates_only_changed_text(monkeypatch):
    app = Studio(Document(1024, 1024))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        status = app.query_one("#status", Static)
        ruler = app.query_one("#ruler", Static)
        updates = {"status": 0, "ruler": 0}
        for name, widget in (("status", status), ("ruler", ruler)):
            original = widget.update
            def update(value, *, key=name, method=original):
                updates[key] += 1
                return method(value)
            monkeypatch.setattr(widget, "update", update)
        app.update_status((500, 500))
        for _ in range(20):
            app.update_status((500, 500))
        assert updates == {"status": 1, "ruler": 0}
        app.update_status((501, 500))
        assert updates == {"status": 2, "ruler": 0}
        app.canvas.pan_x += 10
        app.update_status((501, 500))
        assert updates == {"status": 2, "ruler": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("shortcut", ["delete", "ctrl+a", "ctrl+d"])
async def test_edit_shortcuts_cancel_captured_large_stroke(shortcut):
    app = Studio(Document(1024, 768))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_actual()
        original = app.doc.layer.image.tobytes()
        await pilot.mouse_down("#canvas", offset=(5, 5))
        await pilot.hover("#canvas", offset=(8, 6))
        assert app.canvas.dragging and app.doc.pending is not None
        await pilot.press(shortcut)
        await pilot.pause()
        assert not app.canvas.dragging and app.mouse_captured is None
        assert app.doc.pending is None and len(app.doc.undo_stack) == 1
        # The shortcut applies to the pre-stroke document in its own undo step.
        assert app.doc.layer.image.tobytes() == original
        if shortcut == "ctrl+a":
            assert app.doc.selection.getextrema() == (255, 255)
        elif shortcut == "ctrl+d":
            assert app.doc.selection is None
        assert app.doc.undo()
        assert app.doc.layer.image.tobytes() == original
        await pilot.mouse_up("#canvas", offset=(8, 6))
