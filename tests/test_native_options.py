"""CLI option discovery and strict portable mouse settings persistence."""
import copy
import json
import zipfile

import pytest

from termatelier.commands import CommandError,CommandSession,HELP
from termatelier.model import Document
from termatelier.native_options import feature_families,validate_retouch_options
from termatelier.storage import load_project,save_project


def session():
    return CommandSession(Document(32,24))


def test_features_commands_report_real_implemented_names_and_limits():
    workspace=session()
    result=json.loads(workspace.execute("features --json").text)
    assert result==feature_families()
    assert "clone" in result["painting"]["tools"]
    assert "cage" in result["geometry"]["tools"]
    assert "curves" in result["tone"]["tools"]
    assert "clouds" in result["effects"]["tools"]
    assert "mcp-http" in result["files-and-ai"]["tools"]
    assert all(command in HELP for family in result.values() for command in family["commands"])
    assert "no libMyPaint" in result["painting"]["notes"]
    assert "no GEGL" in result["effects"]["notes"]
    matches=json.loads(workspace.execute("features alpha --json").text)
    assert "tone" in matches
    assert workspace.execute("features definitely-no-family").text=="No matching feature family."


def test_retouch_options_support_json_read_reset_and_native_roundtrip(tmp_path):
    workspace=session(); doc=workspace.document
    options={"strength":.7,"radius":12,"tonal_range":"midtones","exposure":1.2,"rate":24,
             "angle":45,"aspect":.4,"pressure":.8,"preset":"chalk","seed":42,"density":1.1,"merged":True}
    line="retouch-options '"+json.dumps(options)+"'"
    assert json.loads(workspace.execute(line).text)==options
    assert json.loads(workspace.execute("retouch-options").text)==options
    target=tmp_path/"mouse-options.tart"; save_project(doc,target)
    assert load_project(target).settings["retouch_options"]==options
    workspace.execute("retouch-options reset")
    assert doc.settings["retouch_options"]=={}


def test_perspective_clone_mouse_options_validate_actual_four_point_mapping(tmp_path):
    options={"source_quad":[[0,0],[16,0],[16,16],[0,16]],
             "dest_quad":[[2,2],[18,2],[18,18],[2,18]],"strength":.5}
    workspace=session()
    workspace.execute("retouch-options '"+json.dumps(options)+"'")
    target=tmp_path/"perspective-options.tart"; save_project(workspace.document,target)
    assert load_project(target).settings["retouch_options"]==options


@pytest.mark.parametrize("options",[
    [],{"unknown":1},{"strength":True},{"strength":-1},{"strength":2},
    {"radius":0},{"radius":65},{"exposure":5},{"rate":0},{"rate":121},
    {"angle":361},{"aspect":0},{"pressure":1.1},{"density":0},
    {"merged":1},{"seed":True},{"seed":-1},{"seed":2147483648},
    {"seed":1.5},{"tonal_range":"unknown"},{"preset":123},{"preset":"unknown"},
    {"source_quad":[[0,0],[1,0],[1,1],[0,1]]},
    {"source_quad":[[0,0]]*4,"dest_quad":[[0,0]]*4},
])
def test_invalid_mouse_options_leave_existing_settings_unchanged(options):
    workspace=session(); workspace.execute("retouch-options '{\"strength\": 0.4}'")
    before=copy.deepcopy(workspace.document.settings)
    with pytest.raises((ValueError,KeyError,TypeError)):
        validate_retouch_options(options)
    with pytest.raises(CommandError): workspace.execute("retouch-options '"+json.dumps(options)+"'")
    assert workspace.document.settings==before


@pytest.mark.parametrize("value",[float("nan"),float("inf"),float("-inf")])
def test_nonfinite_mouse_values_rejected(value):
    with pytest.raises(ValueError): validate_retouch_options({"strength":value})


@pytest.mark.parametrize("settings",[
    {"retouch_options":{"strength":2}},
    {"retouch_options":{"merged":"true"}},
    {"retouch_options":{"source_quad":[[0,0]]*4,"dest_quad":[[0,0]]*4}},
    {"clone_source":[float("nan"),0]},
    {"clone_source":[1]},
    {"clone_source":[True,0]},
])
def test_invalid_settings_rejected_when_saving_and_loading_native(tmp_path,settings):
    workspace=session(); doc=workspace.document
    path=tmp_path/"valid.tart"; save_project(doc,path); original=path.read_bytes()
    doc.settings.update(settings)
    with pytest.raises(ValueError): save_project(doc,path)
    assert path.read_bytes()==original
    with zipfile.ZipFile(path) as archive:
        entries={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(entries["manifest.json"]); manifest["settings"].update(settings)
    entries["manifest.json"]=json.dumps(manifest).encode("utf-8")
    broken=tmp_path/"broken.tart"
    with zipfile.ZipFile(broken,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,data in entries.items(): archive.writestr(name,data)
    with pytest.raises(ValueError): load_project(broken)


def test_source_command_saved_coordinates_roundtrip_and_no_history_noise(tmp_path):
    workspace=session(); doc=workspace.document
    workspace.execute("tool-source 8 6")
    assert doc.settings["clone_source"]==[8,6] and not doc.undo_stack
    path=tmp_path/"source.tart"; save_project(doc,path)
    assert load_project(path).settings["clone_source"]==[8,6]
