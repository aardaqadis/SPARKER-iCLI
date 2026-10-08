"""Command discovery, keyboard editing and complete CLI-to-file workflows."""
import json

from PIL import Image
import pytest
from textual.widgets import Input

from termatelier.cli_app import CLIApp
from termatelier.commands import CommandSession, syntax_completions
from termatelier.model import Document
from termatelier.storage import load_project


class PreviewProbe:
    running = False
    error = ""
    document = None

    def start(self, document, metadata=None):
        self.running = True
        self.document = document
        return True

    def update(self, document, metadata=None):
        self.document = document
        return self.running

    def close(self):
        self.running = False


@pytest.mark.parametrize("draft,expected", [
    ("clipboard i", ["clipboard info"]),
    ("paste --cen", ["paste --center"]),
    ('text 3 4 "A title" --al', ['text 3 4 "A title" --align']),
    ('text 3 4 "A title" --align c', ['text 3 4 "A title" --align center']),
    ('text 3 4 "A title" --anchor bottom-c', ['text 3 4 "A title" --anchor bottom-center']),
    ("selection sh", ["selection shrink"]),
    ("adjust lev", ["adjust levels"]),
    ("layer blend mul", ["layer blend multiply"]),
    ("help clip", ["help clipboard"]),
    ("copy --merged --mer", []),
    ('text 3 4 "unfinished --al', None),
    ("tool era", None),
    ("files imp", None),
])
def test_completion_handles_options_quotes_and_existing_specialized_completers(draft, expected):
    assert syntax_completions(draft) == expected


def test_command_search_is_discoverable_and_does_not_edit_artwork():
    session = CommandSession(Document(16, 12))
    before = session.document.composite().tobytes()
    revision = session.document.revision
    search = json.loads(session.execute("commands clipboard --json").text)
    assert {"copy", "paste", "clipboard"}.intersection(search) == {"paste", "clipboard"}
    assert "--wrap" in session.execute("help text").text
    assert "--grid" in session.execute("help paste").text
    assert session.execute("commands no-such-command").text == "No matching commands."
    assert session.document.revision == revision
    assert session.document.composite().tobytes() == before


@pytest.mark.asyncio
async def test_cli_image_shortcuts_preserve_command_draft_cursor_and_share_undo():
    session = CommandSession(Document(32, 24))
    session.execute("rectangle 3 4 7 8 --filled --color orange")
    session.execute("select rectangle 3 4 7 8")
    preview = PreviewProbe()
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(110, 35)) as pilot:
        await pilot.pause()
        screen = app.workspace
        field = screen.query_one("#cli-command-line", Input)
        field.value = 'text 16 2 "still typing"'
        field.cursor_position = 7
        draft = field.value
        revision = session.document.revision
        await pilot.press("alt+c")
        assert session.document.clipboard[0].getpixel((0, 0)) == (255, 165, 0, 255)
        assert session.document.revision == revision
        await pilot.press("alt+x")
        assert session.document.layer.image.getpixel((4, 5))[3] == 0
        await pilot.press("alt+v")
        assert session.document.layer.name == "Pasted"
        assert session.document.layer.image.getpixel((4, 5)) == (255, 165, 0, 255)
        assert preview.document is session.document
        assert field.value == draft and field.cursor_position == 7
        assert screen.command_history == []
        await pilot.press("ctrl+z", "ctrl+z")
        assert session.document.layer.image.getpixel((4, 5)) == (255, 165, 0, 255)
        assert field.value == draft


@pytest.mark.asyncio
async def test_full_screen_cli_completion_and_new_features_export_every_canvas_pixel(tmp_path):
    session = CommandSession(Document(96, 64), base_dir=tmp_path)
    preview = PreviewProbe()
    app = CLIApp(session, preview=preview)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        field = app.workspace.query_one("#cli-command-line", Input)
        for draft, completed in (("clipboard i", "clipboard info "),
                                 ("paste --cen", "paste --center "),
                                 ('text 48 3 "Title" --al', 'text 48 3 "Title" --align ')):
            field.value = draft
            await pilot.press("ctrl+space")
            assert field.value == completed
        commands = [
            "pixel 1 1 #ff003380",
            "polygon 5,30 20,20 35,30 --filled --color teal",
            "bezier 2,50 25,35 55,62 90,45 --width 1 --color orange",
            "copy --box 5,20,36,31",
            "paste 55 30 --name Copy --flip-h",
            'text 48 2 "CLI title" --size 11 --anchor top-center --stroke 1 --shadow 1,1 --layer Title',
            "select rectangle 1 1 93 61",
            "selection shrink 2",
            "adjust temperature .1",
            "select none",
            "save full-detail.tart",
            "export full-detail.png",
            "export full-detail.webp",
            "export full-detail.tiff",
        ]
        for command in commands:
            field.value = command
            await pilot.press("enter")
        assert app.workspace.command_history == commands
        original = session.document.composite()
        assert preview.document is session.document
        assert len(session.document.layers) == 4
        for extension in ("png", "webp", "tiff"):
            with Image.open(tmp_path / "exports" / f"full-detail.{extension}") as image:
                assert image.size == original.size == (96, 64)
                assert image.convert("RGBA").tobytes() == original.tobytes()
        reopened = load_project(tmp_path / "full-detail.tart")
        assert reopened.composite().tobytes() == original.tobytes()
        assert [layer.name for layer in reopened.layers] == [layer.name for layer in session.document.layers]
        assert field.has_focus
