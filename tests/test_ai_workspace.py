"""On-demand MCP settings and responsive full-screen image-command workflows."""
import asyncio
import base64
import io
import threading

from PIL import Image
import pytest
from textual.widgets import Input,RichLog

from termatelier.cli_app import CLIApp
from termatelier.commands import CommandSession
from termatelier.mcp_client import MCPError,MCPRegistry
from termatelier.mcp_settings import MCPSettingsScreen
from termatelier.model import Document
from termatelier.storage import png_bytes


class PreviewProbe:
    def __init__(self):
        self.running=False; self.error=""; self.updated=[]; self.reopened=[]
    def start(self,document,metadata=None): self.running=True; return True
    def update(self,document,metadata=None):
        self.updated.append((document,document.composite().tobytes()))
        return self.running
    def reopen(self,document,metadata=None): self.reopened.append(document); self.running=True; return True
    def close(self): self.running=False


def configured_session():
    session=CommandSession(Document(16,12))
    registry=MCPRegistry.load(session.config)
    registry.add({"name":"images","transport":"http","url":"http://127.0.0.1:9999/mcp"})
    registry.map("background-remove",{"server":"images","tool":"remove_background","input_key":"image_base64","input_mode":"base64"})
    return session


def response(image):
    return {"result":{"content":[{"type":"image","mimeType":"image/png","data":base64.b64encode(png_bytes(image)).decode("ascii")}],"isError":False}}


def log_text(screen):
    output=screen.query_one("#cli-command-output",RichLog)
    return " ".join(segment.text for line in output.lines for segment in line._segments)


async def wait_finished(screen,pilot):
    for _ in range(200):
        if not screen.remote_busy:
            await pilot.pause(); return
        await asyncio.sleep(.02)
    raise AssertionError("The MCP worker did not restore the prompt within four seconds.")


@pytest.mark.asyncio
@pytest.mark.parametrize("command",["ai settings","mcp settings"])
async def test_ai_settings_commands_open_without_connecting(command,monkeypatch):
    calls=[]
    def forbidden(*args,**kwargs): calls.append(args); raise AssertionError("Settings must not initiate network requests")
    monkeypatch.setattr("termatelier.ai_commands.request",forbidden)
    app=CLIApp(CommandSession(Document(16,12)),preview=PreviewProbe())
    async with app.run_test(size=(110,45)) as pilot:
        await pilot.pause(); app.workspace.submit_command(command); await pilot.pause()
        assert isinstance(app.screen,MCPSettingsScreen)
        assert app.screen.query_one("#mcp-name",Input).has_focus
        assert not calls
        await pilot.press("escape"); await pilot.pause()
        assert app.screen is app.workspace
        assert app.workspace.query_one("#cli-command-line",Input).has_focus
        assert not calls


@pytest.mark.asyncio
async def test_first_unconfigured_ai_command_prompts_then_prefills_without_transmitting(monkeypatch):
    calls=[]
    def forbidden(*args,**kwargs): calls.append(args); raise AssertionError("Unconfigured commands must not send pixels")
    monkeypatch.setattr("termatelier.ai_commands.request",forbidden)
    app=CLIApp(CommandSession(Document(16,12)),preview=PreviewProbe())
    async with app.run_test(size=(110,45)) as pilot:
        await pilot.pause(); screen=app.workspace
        field=screen.query_one("#cli-command-line",Input)
        line="ai background-remove --mask"
        field.value=line; await pilot.press("enter"); await pilot.pause()
        assert isinstance(app.screen,MCPSettingsScreen) and not screen.remote_busy
        assert not screen.document.undo_stack and not calls
        await pilot.press("escape"); await pilot.pause()
        assert app.screen is screen and field.value==line and field.has_focus
        assert not calls


@pytest.mark.asyncio
async def test_mapped_ai_worker_is_responsive_blocks_edits_and_syncs_shared_history(monkeypatch):
    started,release=threading.Event(),threading.Event()
    calls=[]; returned=Image.new("RGBA",(16,12),(90,130,210,128))
    def service(connection,tool_name=None,arguments=None):
        calls.append((connection,tool_name,arguments,threading.current_thread().ident))
        started.set()
        if not release.wait(10): raise MCPError("Test response timed out")
        return response(returned)
    monkeypatch.setattr("termatelier.ai_commands.request",service)
    session=configured_session(); doc=session.document; preview=PreviewProbe()
    app=CLIApp(session,preview=preview)
    try:
        async with app.run_test(size=(110,35)) as pilot:
            await pilot.pause(); screen=app.workspace; field=screen.query_one("#cli-command-line",Input)
            original=[layer.image.tobytes() for layer in doc.layers]; before=(doc.revision,len(doc.undo_stack),len(doc.layers))
            field.value="ai background-remove --layer Subject"; await pilot.press("enter")
            assert await asyncio.to_thread(started.wait,2)
            assert screen.remote_busy and field.disabled
            assert calls[0][1]=="remove_background" and calls[0][3]!=threading.current_thread().ident
            with Image.open(io.BytesIO(base64.b64decode(calls[0][2]["image_base64"]))) as sent:
                assert sent.convert("RGBA").tobytes()==doc.layer.rendered().tobytes()
            await pilot.press("alt+x","alt+v","ctrl+z","ctrl+y","ctrl+n","ctrl+o","ctrl+s","ctrl+e","f3","escape")
            assert app.screen is screen and screen.remote_busy
            screen.submit_command("pixel 0 0 red")
            assert (doc.revision,len(doc.undo_stack),len(doc.layers))==before
            assert [layer.image.tobytes() for layer in doc.layers]==original
            await pilot.press("f5")
            assert not preview.reopened and screen.remote_busy
            screen.write("Busy responsiveness probe"); await pilot.pause()
            assert screen.query_one("#cli-command-output",RichLog).lines
            await pilot.press("pageup","pagedown","ctrl+l")
            assert not screen.query_one("#cli-command-output",RichLog).lines and screen.remote_busy
            release.set(); await wait_finished(screen,pilot)
            assert not screen.remote_busy and not field.disabled and field.has_focus
            assert session.document is doc and doc.layer.name=="Subject"
            assert doc.layer.image.tobytes()==returned.tobytes()
            assert len(doc.undo_stack)==before[1]+1
            assert preview.updated[-1][0] is doc and preview.updated[-1][1]==doc.composite().tobytes()
            screen.submit_command("undo")
            assert [layer.image.tobytes() for layer in doc.layers]==original
            screen.submit_command("redo")
            assert doc.layer.image.tobytes()==returned.tobytes()
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["service-error","wrong-size"])
async def test_ai_worker_errors_unlock_and_refocus_without_changing_pixels(failure,monkeypatch):
    started,release=threading.Event(),threading.Event()
    def service(*args,**kwargs):
        started.set()
        if not release.wait(10): raise MCPError("Test response timed out")
        if failure=="service-error": raise MCPError("Image service unavailable")
        return response(Image.new("RGBA",(10,10),"red"))
    monkeypatch.setattr("termatelier.ai_commands.request",service)
    session=configured_session(); doc=session.document; preview=PreviewProbe(); app=CLIApp(session,preview=preview)
    try:
        async with app.run_test(size=(110,35)) as pilot:
            await pilot.pause(); screen=app.workspace; field=screen.query_one("#cli-command-line",Input)
            original=[layer.image.tobytes() for layer in doc.layers]; revision=doc.revision
            screen.submit_command("ai background-remove --replace")
            assert await asyncio.to_thread(started.wait,2)
            assert screen.remote_busy and field.disabled
            release.set(); await wait_finished(screen,pilot)
            assert not screen.remote_busy and not field.disabled and field.has_focus
            assert doc.revision==revision and not doc.undo_stack and doc.pending is None
            assert [layer.image.tobytes() for layer in doc.layers]==original
            assert "Error:" in log_text(screen)
            assert "unavailable" in log_text(screen) if failure=="service-error" else "canvas is 16" in log_text(screen)
            screen.submit_command("pixel 0 0 blue")
            assert doc.layer.image.getpixel((0,0))==(0,0,255,255)
    finally:
        release.set()
