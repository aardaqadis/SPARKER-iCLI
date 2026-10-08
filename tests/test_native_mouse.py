"""Real SGR terminal gestures exercise the native painting and selection UI."""
import time

from PIL import Image, ImageDraw
import pytest
from textual._xterm_parser import XTermParser
from textual.widgets import OptionList

from termatelier.app import Studio, TOOLS
from termatelier.config import RuntimeConfig
from termatelier.model import Document, Layer
from termatelier.painting_tools import MOUSE_TOOLS


@pytest.fixture(autouse=True)
def settings(tmp_path,monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE",str(tmp_path / "settings.json"))
    monkeypatch.setenv("SPARKER_EXPORT_DIR",str(tmp_path / "exports"))


def painting(size=(64,48),transparent=False):
    doc = Document(*size)
    doc.layers = [Layer("Paint", Image.new("RGBA",size,(70,90,120,0 if transparent else 255)))]
    doc.active = 0
    if not transparent:
        for y in range(3,14):
            for x in range(3,14):
                value = 180 if (x+y) % 2 else 70
                doc.layer.image.putpixel((x,y),(value,20,30,255))
    return doc


def studio(doc):
    return Studio(doc,config=RuntimeConfig({"preview.enabled":False,"debug.enabled":False}))


async def report(app,pilot,parser,code,cell,state="M"):
    region = app.canvas.region
    wire = f"\x1b[<{code};{region.x+cell[0]+1};{region.y+cell[1]+1}{state}"
    for event in parser.feed(wire):
        app._driver.process_message(event)
    await pilot.pause()


async def drag(app,pilot,parser,cells):
    await report(app,pilot,parser,0,cells[0])
    for cell in cells[1:]:
        await report(app,pilot,parser,32,cell)
    await report(app,pilot,parser,0,cells[-1],"m")


@pytest.mark.asyncio
async def test_toolbox_includes_every_native_mouse_family_and_selection_family():
    app = studio(painting())
    names = MOUSE_TOOLS | {"select_color","scissors","foreground"}
    assert names <= {identifier for identifier,_ in TOOLS}
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        tools = app.query_one("#tool-list",OptionList)
        for name in names:
            assert tools.get_option(name).id == name
            app.action_tool(name)
            assert app.tool == name


@pytest.mark.asyncio
@pytest.mark.parametrize("tool",sorted(MOUSE_TOOLS))
async def test_sgr_native_tool_drags_create_pixels_and_one_undo_without_hover_paint(tool):
    doc = painting()
    original = doc.layer.image.tobytes()
    doc.settings["retouch_options"] = {"strength":1,"radius":1,"tonal_range":"all",
        "exposure":1,"source_quad":[[3,3],[14,3],[14,14],[3,14]],
        "dest_quad":[[18,14],[40,14],[40,34],[18,34]],"rate":4}
    app = studio(doc)
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool(tool)
        app.brush_size,app.hardness,app.opacity = 5,1,.8
        parser = XTermParser()
        await report(app,pilot,parser,35,(40,15))
        assert doc.layer.image.tobytes() == original and not doc.undo_stack
        if tool in ("clone","heal","perspective-clone"):
            await report(app,pilot,parser,16,(8,4))
            await report(app,pilot,parser,16,(8,4),"m")
            assert doc.settings["clone_source"] == [8,8]
            assert not doc.undo_stack and not app.canvas.dragging and app.mouse_captured is None
        cells = [(20,8),(26,8),(35,10)] if tool in ("clone","heal") else [(8,4),(22,8),(35,12)]
        await drag(app,pilot,parser,cells)
        assert doc.pending is None and not app.canvas.dragging and app.mouse_captured is None
        assert len(doc.undo_stack) == 1 and doc.layer.image.tobytes() != original
        changed = doc.layer.image.tobytes()
        await report(app,pilot,parser,35,(40,15))
        assert doc.layer.image.tobytes() == changed
        assert doc.undo() and doc.layer.image.tobytes() == original
        assert doc.redo() and doc.layer.image.tobytes() == changed


@pytest.mark.asyncio
async def test_stationary_airbrush_continues_accumulating_and_escape_cancels_one_transaction():
    doc = painting(transparent=True)
    doc.settings["retouch_options"] = {"rate":1}
    app = studio(doc)
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool("airbrush")
        app.brush_size,app.hardness,app.opacity = 5,1,1
        parser = XTermParser()
        await report(app,pilot,parser,0,(20,10))
        initial = doc.layer.image.getpixel((20,20))[3]
        assert initial > 0 and doc.pending is not None
        await pilot.pause(.25)
        accumulated = doc.layer.image.getpixel((20,20))[3]
        assert accumulated > initial
        assert not doc.undo_stack
        await report(app,pilot,parser,0,(20,10),"m")
        assert doc.pending is None and len(doc.undo_stack) == 1
        assert app.canvas._spray_timer is None
        finished = doc.layer.image.tobytes()
        await pilot.pause(.15)
        assert doc.layer.image.tobytes() == finished
        await report(app,pilot,parser,0,(30,10))
        await pilot.pause(.15)
        await pilot.press("escape")
        await pilot.pause()
        assert doc.layer.image.tobytes() == finished and len(doc.undo_stack) == 1
        assert app.canvas._spray_timer is None and app.mouse_captured is None


@pytest.mark.asyncio
async def test_clone_without_source_and_locked_retouch_cancel_without_partial_state():
    doc = painting()
    original = doc.layer.image.tobytes()
    app = studio(doc)
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool("clone")
        parser = XTermParser()
        await report(app,pilot,parser,0,(20,10))
        await report(app,pilot,parser,0,(20,10),"m")
        assert doc.layer.image.tobytes() == original and not doc.undo_stack and doc.pending is None
        assert not app.canvas.dragging and app.mouse_captured is None
        doc.layer.locked = True
        app.action_tool("burn")
        await drag(app,pilot,parser,[(20,10),(30,10)])
        assert doc.layer.image.tobytes() == original and not doc.undo_stack and doc.pending is None
        assert not app.canvas.dragging and app.mouse_captured is None


@pytest.mark.asyncio
async def test_select_by_color_mouse_selects_disconnected_islands_and_one_undo():
    doc = painting()
    doc.layer.image.putpixel((8,8),(255,0,0,255))
    doc.layer.image.putpixel((50,32),(255,0,0,255))
    app = studio(doc)
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool("select_color")
        app.tolerance = 0
        parser = XTermParser()
        await report(app,pilot,parser,0,(8,4))
        await report(app,pilot,parser,0,(8,4),"m")
        assert doc.selection.getpixel((8,8)) == doc.selection.getpixel((50,32)) == 255
        assert doc.selection.getpixel((25,20)) == 0
        assert len(doc.undo_stack) == 1 and doc.undo() and doc.selection is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool",["scissors","foreground"])
async def test_assisted_selection_mouse_outline_and_foreground_control_markers(tool):
    doc = painting()
    doc.layer.image = Image.new("RGBA",doc.size,"blue")
    ImageDraw.Draw(doc.layer.image).rectangle((18,12,42,36),fill="red")
    app = studio(doc)
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool(tool)
        parser = XTermParser()
        if tool == "foreground":
            await report(app,pilot,parser,16,(30,12))
            await report(app,pilot,parser,16,(30,12),"m")
            assert doc.settings["foreground_marks"] == [[30,24]]
            assert not doc.undo_stack and doc.selection is None
            cells = [(12,4),(48,4),(48,21),(12,21),(12,4)]
        else:
            cells = [(18,6),(42,6),(42,18),(18,18),(18,6)]
        await drag(app,pilot,parser,cells)
        assert doc.selection is not None and doc.selection.getpixel((30,24)) == 255
        assert doc.selection.getpixel((4,4)) == 0
        if tool == "foreground":
            assert doc.selection.getpixel((14,24)) == 0
        assert len(doc.undo_stack) == 1 and doc.pending is None and app.canvas.preview is None
        assert doc.undo() and doc.selection is None


@pytest.mark.asyncio
async def test_mouse_pixel_edits_refresh_live_effect_cache_and_full_filter_halo():
    doc = painting(transparent=True)
    doc.layer.effects = [{"kind":"filter","name":"gaussian-blur","options":{"radius":2},"enabled":True,"opacity":1}]
    app = studio(doc)
    async with app.run_test(size=(140,50)) as pilot:
        await pilot.pause()
        app.action_actual()
        app.action_tool("ink")
        app.brush_size,app.hardness,app.opacity = 5,1,1
        app.foreground = "#ff0000"
        before = doc.composite().tobytes()
        app.canvas._smooth_source()
        parser = XTermParser()
        await report(app,pilot,parser,0,(20,10))
        # Compare cached render with a forced fresh evaluation during the live
        # transaction, including pixels outside the original brush footprint.
        live = doc.composite().tobytes()
        assert live != before
        doc.layer.invalidate_effects()
        fresh = doc.composite()
        assert live == fresh.tobytes()
        assert fresh.getpixel((20,16))[3] > 0
        await report(app,pilot,parser,32,(30,10))
        changed = doc.composite().tobytes()
        doc.layer.invalidate_effects()
        assert doc.composite().tobytes() == changed and changed != live
        await report(app,pilot,parser,0,(30,10),"m")
        assert len(doc.undo_stack) == 1 and doc.layer.effects and doc.layer.image.getbbox()
        assert doc.undo() and doc.layer.image.getchannel("A").getbbox() is None and doc.layer.effects
