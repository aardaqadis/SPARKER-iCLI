"""Editable-source workflows and malformed-project atomic persistence checks."""
import copy
import json
import zipfile

from PIL import Image,ImageChops,ImageDraw
import pytest

from termatelier.adjustment_tools import apply_adjustment,apply_effect
from termatelier.commands import CommandError,CommandSession
from termatelier.document_tools import decode_channel,encode_channel,path_points
from termatelier.model import Document,Layer
from termatelier.storage import load_project,save_project,export_image
from termatelier.text_tools import TextStyle,render_text


def workspace(size=(96,64)):
    doc=Document(*size); doc.layers=[Layer("Paint",Image.new("RGBA",size))]; doc.active=0
    return CommandSession(doc)


def snapshot(doc):
    return (doc.size,[layer.image.tobytes() for layer in doc.layers],
            [layer.mask.tobytes() if layer.mask is not None else None for layer in doc.layers],
            [copy.deepcopy(layer.effects) for layer in doc.layers],
            [copy.deepcopy(layer.text_recipe) for layer in doc.layers],
            doc.selection.tobytes() if doc.selection is not None else None,
            copy.deepcopy(doc.metadata),doc.active)


def rewritten_project(source,destination,mutation):
    with zipfile.ZipFile(source) as original:
        items={entry.filename:original.read(entry.filename) for entry in original.infolist()}
    manifest=json.loads(items["manifest.json"])
    mutation(manifest)
    items["manifest.json"]=json.dumps(manifest).encode("utf-8")
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,data in items.items(): archive.writestr(name,data)


def text_from_recipe(recipe,size):
    args={key:value for key,value in recipe.items() if key not in ("point","text","color")}
    rendered=render_text(recipe["text"],recipe["color"],TextStyle(**args))
    result=Image.new("RGBA",size)
    result.alpha_composite(rendered.image,rendered.origin(recipe["point"]))
    return result


def test_paths_remain_editable_and_drive_pixels_selection_and_history(tmp_path):
    session=workspace(); doc=session.document
    session.execute("path new Area 8,8 32,8 32,28 8,28 --closed")
    area=copy.deepcopy(doc.metadata["paths"]["Area"])
    session.execute("path point Area 2 40,8")
    assert doc.metadata["paths"]["Area"]["points"][1]==[40,8]
    assert doc.undo() and doc.metadata["paths"]["Area"]==area
    session.execute("path move Area 3 2")
    assert doc.metadata["paths"]["Area"]["points"][0]==[11,10]
    session.execute("path select Area")
    assert doc.selection.getpixel((20,20))==255 and doc.selection.getpixel((0,0))==0
    session.execute("path fill Area --color red")
    assert doc.layer.image.getpixel((20,20))==(255,0,0,255)
    session.execute("select none")
    session.execute("path curve Arc 5,40 10,20 60,20 65,40")
    samples=path_points(doc.metadata["paths"]["Arc"])
    assert samples[0]==(5,40) and samples[-1]==(65,40)
    session.execute("path stroke Arc --color blue --width 2")
    assert doc.layer.image.getpixel((5,40))==(0,0,255,255)
    session.execute("path close Arc")
    assert doc.metadata["paths"]["Arc"]["closed"]
    target=tmp_path/"paths.tart"; save_project(doc,target); restored=load_project(target)
    assert restored.metadata["paths"]==doc.metadata["paths"]
    assert restored.layer.image.tobytes()==doc.layer.image.tobytes()
    session.execute("path delete Arc")
    assert "Arc" not in doc.metadata["paths"] and doc.undo()
    assert "Arc" in doc.metadata["paths"]


def test_channels_selection_combinations_color_components_and_mask_undo(tmp_path):
    session=workspace((25,19)); doc=session.document
    doc.layer.image=Image.new("RGBA",doc.size,(110,40,190,180))
    session.execute("select rectangle 2 3 8 9")
    selection=doc.selection.copy(); session.execute("channel save Shape")
    session.execute("channel save Red --from red")
    assert decode_channel(doc.metadata["channels"]["Red"],doc.size).getpixel((0,0))==110
    session.execute("select none"); session.execute("channel load Shape")
    assert doc.selection.tobytes()==selection.tobytes()
    session.execute("select rectangle 12 3 18 9")
    session.execute("channel load Shape --mode add")
    assert doc.selection.getpixel((4,5))==255 and doc.selection.getpixel((14,5))==255
    session.execute("channel load Shape --mode intersect")
    assert doc.selection.tobytes()==selection.tobytes()
    session.execute("channel mask Shape")
    assert doc.layer.mask.tobytes()==selection.tobytes()
    assert doc.composite().getpixel((0,0))[3]==0
    assert doc.undo() and doc.layer.mask is None
    target=tmp_path/"channels.tart"; save_project(doc,target)
    assert load_project(target).metadata["channels"]==doc.metadata["channels"]
    session.execute("channel delete Red")
    assert "Red" not in doc.metadata["channels"] and doc.undo()
    assert "Red" in doc.metadata["channels"]


def test_paths_and_channels_follow_canvas_resize_and_crop():
    session=workspace((32,24)); doc=session.document
    session.execute("path new Shape 3,4 12,4 12,10 --closed")
    session.execute("select rectangle 3 4 12 10")
    original=doc.selection.copy(); session.execute("channel save Area")
    session.execute("resize 64x48")
    assert doc.metadata["paths"]["Shape"]["points"]==[[6,8],[24,8],[24,20]]
    saved=decode_channel(doc.metadata["channels"]["Area"],doc.size)
    assert saved.tobytes()==original.resize((64,48),Image.Resampling.NEAREST).tobytes()
    session.execute("select rectangle 4 6 30 26")
    crop=doc.selection.getbbox(); before=snapshot(doc)
    session.execute("crop")
    assert doc.size==(crop[2]-crop[0],crop[3]-crop[1])
    assert doc.metadata["paths"]["Shape"]["points"][0]==[6-crop[0],8-crop[1]]
    assert decode_channel(doc.metadata["channels"]["Area"],doc.size).tobytes()==saved.crop(crop).tobytes()
    assert doc.undo() and snapshot(doc)==before


def test_canvas_resize_keeps_channel_and_path_origin_without_resampling():
    session=workspace((32,24)); doc=session.document
    session.execute("path new Shape 3,4 12,4")
    session.execute("select rectangle 3 4 12 10"); session.execute("channel save Area")
    old=doc.selection.copy(); session.execute("canvas 50x40")
    expected=Image.new("L",(50,40)); expected.paste(old,(0,0))
    assert decode_channel(doc.metadata["channels"]["Area"],doc.size).tobytes()==expected.tobytes()
    assert doc.metadata["paths"]["Shape"]["points"][0]==[3,4]


def test_fx_preserves_original_pixels_and_applies_to_whole_layer_then_mask():
    session=workspace((12,8)); doc=session.document
    doc.layer.image=Image.new("RGBA",doc.size,(60,80,110,200)); original=doc.layer.image.tobytes()
    doc.selection=Image.new("L",doc.size)
    session.execute("fx add tone exposure --args '{\"stops\":1}'")
    assert doc.layer.image.tobytes()==original
    assert doc.layer.pixels_with_effects().getpixel((0,0))==(120,160,220,200)
    doc.layer.mask=Image.new("L",doc.size,128); doc.layer.opacity=.5
    rendered=doc.layer.rendered()
    assert rendered.getpixel((0,0))==(120,160,220,50)
    session.execute("fx hide 1")
    assert doc.layer.rendered().getpixel((0,0))==(60,80,110,50)
    session.execute("fx show 1")
    session.execute("fx edit 1 --args '{\"stops\":2}' --opacity .5")
    expected=Image.blend(doc.layer.image,apply_adjustment(doc.layer.image,"exposure",stops=2),.5)
    assert doc.layer.pixels_with_effects().tobytes()==expected.tobytes()
    assert json.loads(session.execute("fx list --json").text)[0]["opacity"]==.5


def test_fx_reorder_remove_and_bake_have_real_pixels_and_undo():
    session=workspace((12,8)); doc=session.document
    doc.layer.image=Image.new("RGBA",doc.size,(60,80,110,200)); original=doc.layer.image.tobytes()
    session.execute("fx add tone exposure --args '{\"stops\":1}'")
    session.execute("fx add tone curves --args '{\"points\":\"0,255;255,0\"}'")
    forward=doc.layer.pixels_with_effects().tobytes()
    session.execute("fx reorder 2 1")
    reverse=doc.layer.pixels_with_effects().tobytes()
    assert reverse!=forward and doc.layer.image.tobytes()==original
    session.execute("fx remove 2")
    assert len(doc.layer.effects)==1 and doc.undo()
    before=snapshot(doc)
    session.execute("fx bake")
    assert not doc.layer.effects and doc.layer.image.tobytes()==reverse
    assert doc.undo() and snapshot(doc)==before


def test_fx_compressed_history_native_save_load_and_full_pixel_export(tmp_path):
    session=workspace((31,23)); doc=session.document
    doc.history_storage="compressed"
    doc.layer.image=Image.new("RGBA",doc.size,(50,90,120,160))
    session.execute("fx add filter rgb-noise --args '{\"amount\":0.1,\"seed\":7}'")
    session.execute("fx add filter shadow --args '{\"dx\":2,\"dy\":3,\"radius\":1}'")
    before=snapshot(doc); rendered=doc.composite().tobytes()
    session.execute("fx edit 1 --args '{\"amount\":0.2,\"seed\":8}'")
    assert doc.undo() and snapshot(doc)==before
    assert doc.composite().tobytes()==rendered
    target=tmp_path/"fx.tart"; save_project(doc,target); restored=load_project(target)
    assert snapshot(restored)==before and restored.composite().tobytes()==rendered
    exported=tmp_path/"fx.png"; export_image(restored,exported,scale=1)
    with Image.open(exported) as opened:
        assert opened.size==doc.size and opened.convert("RGBA").tobytes()==rendered


def test_effect_cache_reuses_render_but_refreshes_after_source_or_options_change(monkeypatch):
    from termatelier import document_tools
    session=workspace((20,12)); doc=session.document
    doc.layer.image=Image.new("RGBA",doc.size,(50,90,120,160))
    session.execute("fx add tone exposure --args '{\"stops\":1}'")
    original=document_tools.render_effects; calls=[]
    def traced(image,effects):
        calls.append(1); return original(image,effects)
    monkeypatch.setattr(document_tools,"render_effects",traced)
    first=doc.layer.pixels_with_effects().tobytes()
    assert doc.layer.pixels_with_effects().tobytes()==first and len(calls)==1
    session.execute("pixel 3 3 red")
    changed=doc.layer.pixels_with_effects().tobytes()
    assert changed!=first and len(calls)==2
    session.execute("fx edit 1 --args '{\"stops\":2}'")
    assert doc.layer.pixels_with_effects().tobytes()!=changed and len(calls)==3
    assert doc.undo() and doc.layer.pixels_with_effects().tobytes()==changed
    doc.layer.cache_effects=False
    count=len(calls)
    doc.layer.pixels_with_effects(); doc.layer.pixels_with_effects()
    assert len(calls)==count+2


def test_large_saved_channels_count_toward_compressed_history_memory_budget():
    import numpy as np
    session=workspace((64,64)); doc=session.document
    doc.history_storage="compressed"; doc.history_bytes=16_000; doc.history_limit=40
    rng=np.random.default_rng(3)
    doc.selection=Image.fromarray(rng.integers(0,256,(64,64),dtype=np.uint8))
    session.execute("channel save Noise")
    doc.selection=None
    encoded=len(doc.metadata["channels"]["Noise"])
    assert encoded>5_000
    for x in range(12): session.execute(f"pixel {x} 0 red")
    assert doc.history_memory_bytes<=doc.history_bytes
    assert len(doc.undo_stack)<=3


def test_editable_text_reflows_retains_style_and_follows_move_and_native_project(tmp_path):
    session=workspace((240,150)); doc=session.document
    session.execute('text 20 15 "A long title" --layer Title --size 16 --wrap 100 --align center --stroke 1 --stroke-color white --shadow 2,3 --shadow-color black --letter-spacing 1')
    assert doc.layer.text_recipe is not None
    original=doc.layer.image.tobytes(); old_recipe=copy.deepcopy(doc.layer.text_recipe)
    session.execute('text-edit "A different title" --size 18 --wrap 90 --align right --color blue')
    assert doc.layer.image.tobytes()!=original and doc.layer.text_recipe["text"]=="A different title"
    assert doc.layer.text_recipe["stroke"]==1 and doc.layer.text_recipe["shadow"]==(2,3)
    assert doc.layer.image.tobytes()==text_from_recipe(doc.layer.text_recipe,doc.size).tobytes()
    assert doc.undo() and doc.layer.image.tobytes()==original and doc.layer.text_recipe==old_recipe
    session.execute("move 7 9")
    assert doc.layer.text_recipe["point"]==[27,24]
    session.execute('text-edit "Moved title"')
    assert doc.layer.image.tobytes()==text_from_recipe(doc.layer.text_recipe,doc.size).tobytes()
    target=tmp_path/"text.tart"; save_project(doc,target); restored=load_project(target)
    assert restored.layer.text_recipe["point"]==[27,24]
    assert restored.layer.text_recipe["text"]=="Moved title"
    assert restored.layer.image.tobytes()==doc.layer.image.tobytes()


def test_editable_text_font_error_rolls_back_source_and_pixels():
    session=workspace((160,100)); doc=session.document
    session.execute('text 10 10 "Hello" --layer Title')
    before=snapshot(doc)
    with pytest.raises(CommandError): session.execute('text-edit "Failed change" --font "does-not-exist.ttf"')
    assert snapshot(doc)==before


def test_text_geometry_translation_alignment_and_recipe_retirement_are_undoable():
    session=workspace((240,150)); doc=session.document
    session.execute('text 20 15 "One" --layer One --size 16')
    session.execute("geometry translate 8 6")
    assert doc.layer.text_recipe["point"] == [28,21]
    session.execute('text-edit "Moved one"')
    assert doc.layer.image.tobytes() == text_from_recipe(doc.layer.text_recipe,doc.size).tobytes()
    session.execute('text 60 35 "Two" --layer Two --size 16')
    session.execute("distribute left --layers One,Two")
    for layer in doc.layers[1:]:
        assert layer.image.tobytes() == text_from_recipe(layer.text_recipe,doc.size).tobytes()
    before=snapshot(doc)
    session.execute("geometry shear .2")
    assert doc.layer.text_recipe is None
    with pytest.raises(CommandError): session.execute('text-edit "Would erase shear"')
    assert doc.undo() and snapshot(doc) == before
    doc.layer.locked=True
    with pytest.raises(CommandError): session.execute('text-edit "Locked"')
    assert snapshot(doc)==before


def test_v1_native_project_still_loads_without_new_fields(tmp_path):
    session=workspace(); doc=session.document
    session.execute("pencil 3,3 15,8 --color red")
    current=tmp_path/"v2.tart"; old=tmp_path/"v1.tart"; save_project(doc,current)
    def strip(manifest):
        manifest["version"]=1
        for layer in manifest["layers"]:
            layer.pop("effects",None); layer.pop("text_recipe",None)
        manifest["metadata"].pop("paths",None); manifest["metadata"].pop("channels",None)
    rewritten_project(current,old,strip)
    restored=load_project(old)
    assert restored.layer.image.tobytes()==doc.layer.image.tobytes()
    assert restored.layer.effects==[] and restored.layer.text_recipe is None


def valid_extended_project(tmp_path):
    session=workspace(); doc=session.document
    session.execute("path new Area 3,3 30,3 30,30 --closed")
    session.execute("select rectangle 3 3 30 30"); session.execute("channel save Area")
    session.execute("select none")
    session.execute('text 8 8 "Title" --layer Title')
    session.execute("fx add tone exposure --args '{\"stops\":1}'")
    target=tmp_path/"valid.tart"; save_project(doc,target)
    return session,target


@pytest.mark.parametrize("mutation",[
    lambda m:m["metadata"]["paths"]["Area"].__setitem__("kind","unknown"),
    lambda m:m["metadata"]["paths"]["Area"].__setitem__("points",[[0,0]]),
    lambda m:m["metadata"]["paths"]["Area"].__setitem__("closed",1),
    lambda m:m["metadata"]["channels"].__setitem__("Area","not-base64"),
    lambda m:m["metadata"]["channels"].__setitem__("Area",encode_channel(Image.new("L",(1,1)))),
    lambda m:m["layers"][-1]["effects"][0].__setitem__("name","unknown"),
    lambda m:m["layers"][-1]["effects"][0].__setitem__("options",{"stops":11}),
    lambda m:m["layers"][-1]["effects"][0].__setitem__("enabled",1),
    lambda m:m["layers"][-1]["effects"][0].__setitem__("opacity",2),
    lambda m:m["layers"][-1]["text_recipe"].__setitem__("point",[float("nan"),0]),
    lambda m:m["layers"][-1]["text_recipe"].__setitem__("size",0),
    lambda m:m["layers"][-1]["text_recipe"].__setitem__("text",["not","text"]),
    lambda m:m["layers"][-1]["text_recipe"].__setitem__("unexpected","field"),
])
def test_malformed_native_extended_fields_rejected_without_replacing_document(tmp_path,mutation):
    session,valid=valid_extended_project(tmp_path); before=snapshot(session.document); original=valid.read_bytes()
    broken=tmp_path/"broken.tart"; rewritten_project(valid,broken,mutation)
    with pytest.raises(ValueError): load_project(broken)
    with pytest.raises(CommandError): session.execute(f'open "{broken}"')
    assert snapshot(session.document)==before and valid.read_bytes()==original


@pytest.mark.parametrize("mutation",[
    lambda d:d.layer.effects[0].__setitem__("options",{"stops":100}),
    lambda d:d.layer.text_recipe.__setitem__("size",0),
    lambda d:d.metadata["channels"].__setitem__("Area","not-base64"),
    lambda d:d.metadata["paths"]["Area"].__setitem__("kind","unknown"),
])
def test_invalid_edits_cannot_overwrite_existing_native_project(tmp_path,mutation):
    session,target=valid_extended_project(tmp_path); original=target.read_bytes(); mutation(session.document)
    with pytest.raises(ValueError): save_project(session.document,target)
    assert target.read_bytes()==original
    assert load_project(target).layer.text_recipe["size"]==12


@pytest.mark.parametrize("command",[
    "path curve Bad 1,1 3,3 8,8","path new Bad nan,1 3,3","channel save Absent",
    "fx add tone unknown","fx add tone exposure --args '{\"stops\":100}'",
    "fx add filter clouds --args '[1,2,3]'","text-edit \"No editable layer\"",
])
def test_invalid_editable_commands_leave_document_unchanged(command):
    session=workspace(); before=snapshot(session.document)
    with pytest.raises(CommandError): session.execute(command)
    assert snapshot(session.document)==before
