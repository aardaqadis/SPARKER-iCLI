"""Full-screen command mode shares pixels and leaves rendering to a viewer."""
import io
import json

import pytest
from textual.app import App
from textual.widgets import Input, RichLog, Static

from termatelier.cli_app import CLIApp, CLIWorkspaceScreen
from termatelier.commands import CommandSession
from termatelier.model import Document


class PreviewProbe:
    def __init__(self):
        self.running = False
        self.error = ""
        self.started = []
        self.updated = []
        self.reopened = []
        self.closed = 0

    def start(self, document, metadata=None):
        self.running = True
        self.started.append((document, metadata))
        return True

    def update(self, document, metadata=None):
        self.updated.append((document, metadata, document.composite().tobytes()))
        return self.running

    def reopen(self, document, metadata=None):
        self.running = True
        self.reopened.append((document, metadata))
        return True

    def close(self):
        self.running = False
        self.closed += 1


@pytest.mark.asyncio
async def test_full_screen_cli_shared_pixels_undo_preview_and_lifecycle():
    doc = Document(24, 20)
    preview = PreviewProbe()
    app = CLIApp(CommandSession(doc), preview=preview)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.workspace
        field = screen.query_one("#cli-command-line", Input)
        output = screen.query_one("#cli-command-output", RichLog)
        assert field.has_focus
        assert screen.size == app.size
        assert output.size.height >= 30
        assert output.region.width == 120
        assert preview.started[0][0] is doc
        field.value = "rectangle 2 2 8 8 --filled --color orange"
        await pilot.press("enter")
        assert doc.layer.image.getpixel((4, 4)) == (255, 165, 0, 255)
        assert preview.updated[-1][0] is doc
        assert preview.updated[-1][1]["dirty"]
        field.value = "undo"
        await pilot.press("enter")
        assert doc.layer.image.getchannel("A").getbbox() is None
        field.value = "redo"
        await pilot.press("enter")
        assert doc.layer.image.getpixel((4, 4)) == (255, 165, 0, 255)
        await pilot.press("f5")
        assert preview.reopened[-1][0] is doc
    assert preview.closed == 1


@pytest.mark.asyncio
async def test_recall_restores_draft_completion_errors_and_history():
    app = CLIApp(CommandSession(Document(16, 12)), preview=PreviewProbe())
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        screen = app.workspace
        field = screen.query_one("#cli-command-line", Input)
        for line in ("color red", "brush --size 7", "history"):
            field.value = line
            await pilot.press("enter")
        field.value = "draft command"
        await pilot.press("up", "up")
        assert field.value == "brush --size 7"
        await pilot.press("down", "down")
        assert field.value == "draft command"
        field.value = "cle"
        await pilot.press("ctrl+space")
        assert field.value == "clear "
        revision = screen.document.revision
        updates = len(screen.preview.updated)
        field.value = "filter absent-effect"
        await pilot.press("enter")
        assert screen.document.revision == revision
        assert screen.document.pending is None
        assert len(screen.preview.updated) == updates
        assert field.has_focus
        field.value = "info"
        await pilot.press("enter")
        assert field.has_focus


@pytest.mark.asyncio
async def test_catalog_search_completion_apply_and_live_preview_share_history():
    from termatelier.tool_library import TOOLS
    identifier = next(iter(TOOLS))
    preview = PreviewProbe()
    session = CommandSession(Document(40, 30))
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(110, 35)) as pilot:
        await pilot.pause()
        screen = app.workspace
        field = screen.query_one("#cli-command-line", Input)
        await pilot.press("f2")
        assert screen.command_history[-1] == "tools categories"
        field.value = "tools search lance --limit 5"
        await pilot.press("enter")
        field.value = "tool era"
        await pilot.press("tab")
        assert field.value == "tool eraser "
        field.value = "tool " + identifier[:-1]
        await pilot.press("ctrl+space")
        assert field.value == f"tool {identifier} "
        await pilot.press("enter")
        assert session.document.settings["library_tool"] == identifier
        field.value = "apply " + identifier[:-1]
        await pilot.press("tab")
        assert field.value == f"apply {identifier} "
        field.value += "20,15 --size 16 --color orange"
        await pilot.press("enter")
        pixels = session.document.layer.image.tobytes()
        assert session.document.layer.image.getchannel("A").getbbox() is not None
        assert preview.updated[-1][0] is session.document
        screen.submit_command("undo")
        assert session.document.layer.image.getchannel("A").getbbox() is None
        screen.submit_command("redo")
        assert session.document.layer.image.tobytes() == pixels
        await pilot.press("pageup", "pagedown")
        assert field.has_focus


@pytest.mark.asyncio
async def test_preview_help_documents_actual_viewer_controls():
    app = CLIApp(CommandSession(Document(16, 12)), preview=PreviewProbe())
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.workspace.submit_command("help preview")
        output = app.workspace.query_one("#cli-command-output", RichLog)
        text = " ".join(segment.text for line in output.lines for segment in line._segments)
        text = " ".join(text.split())
        assert "F fits" in text
        assert "1 shows actual pixels" in text
        assert "mouse wheel zooms" in text
        assert "left-drag pans" in text
        assert "F5 reopens" in text


@pytest.mark.asyncio
async def test_new_and_standalone_quit_guard_unsaved_pixels():
    app = CLIApp(CommandSession(Document(16, 12)), preview=PreviewProbe())
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        screen = app.workspace
        screen.submit_command("rectangle 1 1 5 5 --filled --color red")
        screen.submit_command("new 8x8")
        await pilot.pause()
        assert app.screen is not screen
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert screen.document.size == (16, 12)
        assert screen.document.layer.image.getpixel((3, 3)) == (255, 0, 0, 255)
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.screen is not screen
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert app.screen is screen
        assert screen.query_one("#cli-command-line", Input).has_focus
        screen.submit_command("new 8x8")
        await pilot.pause()
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert screen.document.size == (8, 8)
        assert screen.preview.updated[-1][0] is screen.document


@pytest.mark.asyncio
async def test_embedded_return_preserves_pixels_and_callback_replacement():
    session = CommandSession(Document(16, 12))
    preview = PreviewProbe()
    updates = []

    class Host(App):
        def on_mount(self):
            self.push_screen(CLIWorkspaceScreen(
                session, on_document_changed=lambda value, result: updates.append((value, result)),
                on_exit=self.pop_screen, preview=preview))

    app = Host()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.submit_command("rectangle 1 1 5 5 --filled --color red")
        assert updates[-1][0] is session
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen
        assert session.document.layer.image.getpixel((3, 3)) == (255, 0, 0, 255)
        assert session.document.dirty
        assert preview.closed == 1
    assert preview.closed == 1


@pytest.mark.asyncio
async def test_embedded_shortcuts_use_shared_cli_session_and_preview(tmp_path):
    from termatelier.app import Studio
    from termatelier.storage import load_project
    app = Studio(Document(24, 20))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        screen = app.screen
        preview = PreviewProbe()
        screen.preview = preview
        field = screen.query_one("#cli-command-line", Input)
        await pilot.press("ctrl+n")
        assert field.value == "new "
        from termatelier.file_explorer import FileExplorer
        for key, operation in (("ctrl+o", "open"), ("ctrl+s", "save"), ("ctrl+e", "export")):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, FileExplorer)
            assert app.screen.operation == operation
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is screen
            assert field.has_focus
        screen.submit_command("rectangle 1 1 5 5 --filled --color red")
        updates = len(preview.updated)
        await pilot.press("ctrl+z")
        assert app.doc.layer.image.getchannel("A").getbbox() is None
        assert len(preview.updated) == updates + 1
        assert "1 redo" in str(screen.query_one("#cli-document-status", Static).render())
        await pilot.press("ctrl+y")
        assert app.doc.layer.image.getpixel((3, 3)) == (255, 0, 0, 255)
        assert len(preview.updated) == updates + 2
        project = tmp_path / "shortcut.tart"
        screen.submit_command(f'save "{project}"')
        screen.submit_command("pencil 8,8 --color blue")
        await pilot.press("ctrl+s")
        assert not app.doc.dirty
        assert app.project_path == screen.session.project_path == project
        assert load_project(project).layer.image.getpixel((8, 8)) == (0, 0, 255, 255)
        assert not preview.updated[-1][1]["dirty"]
        await pilot.press("f4")
        assert screen.command_history[-1] == "history"
        field.value = "text command"
        field.cursor_position = 3
        await pilot.press("ctrl+a")
        assert field.cursor_position == 0
        assert app.doc.selection is None
        await pilot.press("escape")
        assert app.doc is screen.document


@pytest.mark.asyncio
async def test_failed_nonatomic_script_refreshes_retained_replacement_and_pixels(tmp_path):
    from termatelier.app import Studio
    script = tmp_path / "partial.sparker"
    script.write_text("new 8x8\npencil 2,2 --color red\nunknown-command\n", encoding="utf-8")
    app = Studio(Document(24, 20))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        screen = app.screen
        preview = PreviewProbe()
        screen.preview = preview
        screen.submit_command(f'script "{script}" --nonatomic')
        assert screen.document is app.doc
        assert app.doc.size == (8, 8)
        assert app.doc.layer.image.getpixel((2, 2)) == (255, 0, 0, 255)
        assert preview.updated[-1][0] is app.doc
        assert "8×8" in str(screen.query_one("#cli-document-status", Static).render())
        output = screen.query_one("#cli-command-output", RichLog)
        assert any("unknown-command" in str(line) for line in output.lines)
        assert screen.query_one("#cli-command-line", Input).has_focus


@pytest.mark.asyncio
async def test_failed_atomic_script_does_not_publish_changed_pixels(tmp_path):
    script = tmp_path / "atomic.sparker"
    script.write_text("new 8x8\npencil 2,2 --color red\nunknown-command\n", encoding="utf-8")
    doc = Document(24, 20)
    preview = PreviewProbe()
    app = CLIApp(CommandSession(doc), preview=preview)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        before = len(preview.updated)
        app.workspace.submit_command(f'script "{script}"')
        assert app.workspace.document is doc
        assert doc.size == (24, 20)
        assert doc.layer.image.getchannel("A").getbbox() is None
        assert len(preview.updated) == before


@pytest.mark.asyncio
async def test_workspace_save_external_export_and_first_frame_ready(tmp_path, monkeypatch):
    state = tmp_path / "startup.json"
    state.write_text(json.dumps({"phase": "opening", "status": "Opening CLI"}))
    monkeypatch.setenv("SPARKER_STARTUP_STATE", str(state))
    debug_state = tmp_path / "debug.json"
    monkeypatch.setenv("SPARKER_DEBUG_STATE", str(debug_state))
    preview = PreviewProbe()
    session = CommandSession(Document(13, 9), base_dir=tmp_path)
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        assert json.loads(state.read_text())["phase"] == "ready"
        diagnostics = json.loads(debug_state.read_text())["diagnostics"]
        assert diagnostics["runtime"]["mode"] == "command workspace"
        assert diagnostics["terminal"]["columns"] == 90
        app.workspace.submit_command('rectangle 0 0 5 5 --filled --color "#123456"')
        app.workspace.submit_command('save "editable.tart"')
        app.workspace.submit_command('export "painting.png"')
        assert (tmp_path / "editable.tart").is_file()
        assert (tmp_path / "exports" / "painting.png").is_file()
        assert not (tmp_path / "painting.png").exists()
        assert not session.document.dirty
        assert "editable.tart" in str(app.workspace.query_one("#cli-document-status", Static).render())


def test_piped_cli_and_explicit_repl_keep_plain_line_workflow(monkeypatch, capsys):
    import termatelier.__main__ as entry
    for arguments in (["--cli"], ["--repl"]):
        monkeypatch.setattr(entry.sys, "stdin", io.StringIO("files\ninfo\nquit\n"))
        assert entry.main(arguments) == 0
        captured = capsys.readouterr()
        assert "sparker>" in captured.out
        assert "file explorer requires" in captured.out + captured.err
        assert "batch/--repl" in captured.out + captured.err


def test_interactive_cli_runs_fullscreen_app(monkeypatch):
    import termatelier.__main__ as entry
    import termatelier.cli_app as workspace
    calls = []
    monkeypatch.setenv("TERM", "xterm-256color")

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    class ProbeApp:
        def __init__(self, session):
            calls.append(session)

        def run(self):
            calls.append("ran")

    monkeypatch.setattr(entry.sys, "stdin", Terminal())
    monkeypatch.setattr(entry.sys, "stdout", Terminal())
    monkeypatch.setattr(workspace, "CLIApp", ProbeApp)
    assert entry.main(["--cli", "--new", "13x9"]) == 0
    assert calls[0].document.size == (13, 9)
    assert calls[1] == "ran"


def test_explicit_repl_forces_plain_mode_on_interactive_terminal(monkeypatch):
    import termatelier.__main__ as entry

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(entry.sys, "stdin", Terminal("quit\n"))
    output = Terminal()
    monkeypatch.setattr(entry.sys, "stdout", output)
    assert entry.main(["--repl"]) == 0
    assert "sparker>" in output.getvalue()


def test_explorer_command_quoting_preserves_spaces_quotes_and_windows_backslashes():
    from termatelier.cli_app import quote_command_argument
    from termatelier.commands import tokenize
    for name in (r"C:\Paintings\Dawn's study.tart", 'two "quote" types\' here.png', "", "a\nb"):
        assert tokenize("open " + quote_command_argument(name)) == ["open", name]


async def choose_explorer_path(pilot, app, path):
    from termatelier.file_explorer import FileExplorer
    from textual.widgets import Button
    await pilot.pause()
    assert isinstance(app.screen, FileExplorer)
    app.screen.query_one("#path", Input).value = str(path)
    await pilot.pause()
    # Button ignores another click until its active animation ends. An idle
    # message queue alone does not advance that timer on faster platforms.
    await pilot.pause(app.screen.query_one("#file-submit", Button).active_effect_duration + .02)
    assert await pilot.click("#file-submit")
    await pilot.pause()


@pytest.mark.asyncio
async def test_explorer_open_import_undo_and_unsaved_guard_update_shared_preview(tmp_path):
    from PIL import Image
    from termatelier.dialogs import Confirm
    from termatelier.storage import save_project
    project = tmp_path / "Dawn's project.tart"
    saved = Document(19, 13)
    saved.paint_mask(Image.new("L", saved.size, 255), "#345678")
    save_project(saved, project)
    image = tmp_path / "imported image.png"
    Image.new("RGBA", (7, 5), (255, 0, 0, 180)).save(image)
    preview = PreviewProbe()
    session = CommandSession(Document(24, 20), base_dir=tmp_path)
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        screen = app.workspace
        await pilot.press("ctrl+o")
        await choose_explorer_path(pilot, app, project)
        assert app.screen is screen
        assert screen.document.size == (19, 13)
        assert session.project_path == project
        assert preview.updated[-1][0] is screen.document
        assert preview.updated[-1][1]["path"] == str(project)
        screen.submit_command("files import")
        await choose_explorer_path(pilot, app, image)
        assert len(screen.document.layers) == 3
        assert screen.document.layer.image.getpixel((3, 3)) == (255, 0, 0, 180)
        imported = screen.document.layer.image.tobytes()
        await pilot.press("ctrl+z")
        assert len(screen.document.layers) == 2
        await pilot.press("ctrl+y")
        assert screen.document.layer.image.tobytes() == imported
        old_document = screen.document
        old_pixels = old_document.composite().tobytes()
        await pilot.press("ctrl+o")
        await choose_explorer_path(pilot, app, project)
        assert isinstance(app.screen, Confirm)
        await pilot.press("ctrl+s", "ctrl+q", "ctrl+z")
        assert isinstance(app.screen, Confirm)
        assert screen.document is old_document
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert screen.document.composite().tobytes() == old_pixels
        await pilot.press("ctrl+o")
        await choose_explorer_path(pilot, app, project)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert screen.document is not old_document
        assert screen.document.size == (19, 13)
        assert preview.updated[-1][0] is screen.document


@pytest.mark.asyncio
async def test_explorer_save_export_and_debug_guard_overwrite_and_preserve_pixels(tmp_path):
    from PIL import Image
    from termatelier.dialogs import Confirm
    from termatelier.file_explorer import FileExplorer
    from termatelier.storage import load_project, save_project
    saved_path = tmp_path / "existing project.tart"
    previous = Document(8, 6)
    save_project(previous, saved_path)
    old_project_bytes = saved_path.read_bytes()
    export_dir = tmp_path / "external exports"
    export_dir.mkdir()
    exported_path = export_dir / "original.png"
    Image.new("RGBA", (2, 2), "blue").save(exported_path)
    old_export_bytes = exported_path.read_bytes()
    report_path = export_dir / "report.json"
    report_path.write_text('{"old": true}', encoding="utf-8")
    session = CommandSession(Document(17, 11), base_dir=tmp_path)
    session.config.set("export.directory", str(export_dir))
    preview = PreviewProbe()
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        screen = app.workspace
        screen.submit_command('rectangle 1 1 6 6 --filled --color "#123456"')
        pixels = screen.document.composite().tobytes()
        await pilot.press("ctrl+s")
        await choose_explorer_path(pilot, app, saved_path)
        assert isinstance(app.screen, Confirm)
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert saved_path.read_bytes() == old_project_bytes
        assert session.project_path is None
        assert screen.document.dirty
        await pilot.press("ctrl+s")
        await choose_explorer_path(pilot, app, saved_path)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert load_project(saved_path).composite().tobytes() == pixels
        assert session.project_path == saved_path
        assert not screen.document.dirty
        assert not preview.updated[-1][1]["dirty"]
        screen.submit_command("pencil 8,8 --color orange")
        await pilot.press("ctrl+s")
        assert app.screen is screen
        assert not screen.document.dirty
        assert load_project(saved_path).layer.image.getpixel((8, 8)) == (255, 165, 0, 255)
        pixels = screen.document.composite().tobytes()
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert app.screen.current_directory == export_dir
        await choose_explorer_path(pilot, app, exported_path)
        assert isinstance(app.screen, Confirm)
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert exported_path.read_bytes() == old_export_bytes
        await pilot.press("ctrl+e")
        await choose_explorer_path(pilot, app, exported_path)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        with Image.open(exported_path) as image:
            assert image.size == (17, 11)
            assert image.convert("RGBA").tobytes() == pixels
        screen.submit_command("files debug")
        await choose_explorer_path(pilot, app, report_path)
        assert isinstance(app.screen, Confirm)
        await pilot.click("#confirm-no")
        await pilot.pause()
        assert json.loads(report_path.read_text()) == {"old": True}
        screen.submit_command("files debug")
        await choose_explorer_path(pilot, app, report_path)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        data = json.loads(report_path.read_text())
        assert (data["document"]["width"], data["document"]["height"]) == (17, 11)
        assert screen.document.composite().tobytes() == pixels
        assert screen.query_one("#cli-command-line", Input).has_focus


@pytest.mark.asyncio
async def test_explorer_keeps_invalid_export_open_and_respects_lossless_settings(tmp_path):
    from PIL import Image
    from termatelier.file_explorer import FileExplorer
    from textual.widgets import Select
    from termatelier.storage import project_roots
    external = tmp_path / "exports"
    external.mkdir()
    session = CommandSession(Document(17, 11), base_dir=tmp_path)
    app = CLIApp(session, preview=PreviewProbe())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        screen = app.workspace
        await pilot.press("ctrl+e")
        await choose_explorer_path(pilot, app, project_roots()[0] / "blocked.png")
        assert isinstance(app.screen, FileExplorer)
        assert "project folder" in str(app.screen.query_one("#file-error", Static).render())
        await choose_explorer_path(pilot, app, external / "lossy.jpg")
        assert isinstance(app.screen, FileExplorer)
        assert "RGBA pixel" in str(app.screen.query_one("#file-error", Static).render())
        await pilot.press("ctrl+n", "ctrl+s", "ctrl+e", "ctrl+o", "ctrl+z", "ctrl+q")
        assert isinstance(app.screen, FileExplorer)
        assert not screen.document.dirty
        app.screen.query_one("#allow-lossy", Select).value = "yes"
        await pilot.click("#file-submit")
        await pilot.pause()
        assert app.screen is screen
        with Image.open(external / "lossy.jpg") as image:
            assert image.size == (17, 11)
        session.config.set("export.lossless", False)
        await pilot.press("ctrl+e")
        await choose_explorer_path(pilot, app, external / "configured.jpg")
        assert app.screen is screen
        assert (external / "configured.jpg").is_file()


@pytest.mark.asyncio
async def test_explorer_script_is_atomic_font_prefills_and_hub_has_help(tmp_path):
    from termatelier.file_explorer import FileExplorer
    from textual.widgets import Select
    script = tmp_path / "painting commands.sparker"
    script.write_text("new 9x7\npencil 2,2 --color red\n", encoding="utf-8")
    failed = tmp_path / "failed.sparker"
    failed.write_text("new 5x3\npencil 1,1 --color blue\nunknown-command\n", encoding="utf-8")
    font = tmp_path / "Painter's font.ttf"
    font.write_bytes(b"Font selection prepares a command; it never executes font code.")
    session = CommandSession(Document(17, 11), base_dir=tmp_path)
    preview = PreviewProbe()
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        screen = app.workspace
        screen.submit_command("files script")
        await choose_explorer_path(pilot, app, script)
        assert screen.document.size == (9, 7)
        assert screen.document.layer.image.getpixel((2, 2)) == (255, 0, 0, 255)
        assert preview.updated[-1][0] is screen.document
        document = screen.document
        pixels = document.composite().tobytes()
        updates = len(preview.updated)
        screen.submit_command("files script")
        await choose_explorer_path(pilot, app, failed)
        await pilot.click("#confirm-yes")
        await pilot.pause()
        assert screen.document is document
        assert screen.document.composite().tobytes() == pixels
        assert len(preview.updated) == updates
        screen.submit_command("files font")
        await choose_explorer_path(pilot, app, font)
        field = screen.query_one("#cli-command-line", Input)
        from termatelier.commands import tokenize
        assert tokenize(field.value) == ["text", "0", "0", "Your text", "--font", str(font)]
        assert field.has_focus
        assert screen.document.composite().tobytes() == pixels
        await pilot.press("f6")
        await pilot.pause()
        assert isinstance(app.screen, FileExplorer)
        assert app.screen.query_one("#file-operation", Select).value == "open"
        app.screen.query_one("#file-operation", Select).value = "export"
        await pilot.pause()
        assert app.screen.operation == "export"
        assert str(app.screen.query_one("#path", Input).value).endswith(".png")
        await pilot.press("escape")
        await pilot.pause()
        field.value = "files imp"
        await pilot.press("tab")
        assert field.value == "files import "
        screen.submit_command("help files")
        output = screen.query_one("#cli-command-output", RichLog)
        content = " ".join(segment.text for line in output.lines for segment in line._segments)
        assert "Piped CLI" in content
        assert "external export folder" in content
        screen.submit_command("files invalid")
        assert app.screen is screen
        assert field.has_focus


@pytest.mark.asyncio
async def test_explorer_directory_selects_external_preference_with_guard(tmp_path):
    from termatelier.file_explorer import FileExplorer
    from termatelier.storage import project_roots
    external = tmp_path / "chosen export folder"
    external.mkdir()
    app = CLIApp(CommandSession(Document(17, 11), base_dir=tmp_path), preview=PreviewProbe())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        screen = app.workspace
        screen.submit_command("files directory")
        await choose_explorer_path(pilot, app, project_roots()[0])
        assert isinstance(app.screen, FileExplorer)
        assert "project folder" in str(app.screen.query_one("#file-error", Static).render())
        await choose_explorer_path(pilot, app, external)
        assert app.screen is screen
        assert session_folder(screen) == external
        assert not screen.document.dirty
        screen.submit_command('export "default.png"')
        assert (external / "default.png").is_file()


def session_folder(screen):
    from pathlib import Path
    return Path(screen.session.config.get("export.directory"))
