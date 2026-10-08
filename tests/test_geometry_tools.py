"""Pixels, masks, handles and undo evidence for native geometric tools."""
import json
import time

import numpy as np
from PIL import Image, ImageDraw
import pytest

from termatelier.commands import CommandSession, tokenize
from termatelier.geometry_commands import execute_geometry
from termatelier.geometry_tools import geometry_matrix, remap_image, transform_image
from termatelier.model import Document, Layer


def image(size=(31,23)):
    result = Image.new("RGBA",size)
    ImageDraw.Draw(result).rectangle((5,4,13,11),fill=(210,60,90,180))
    result.putpixel((9,7),(0,200,30,255))
    return result


def workspace(size=(31,23)):
    doc = Document(*size)
    doc.layers = [Layer("Paint",image(size))]
    doc.active = 0
    return CommandSession(doc)


def run(session,value):
    tokens = tokenize(value)
    return execute_geometry(session,tokens[0],tokens[1:])


def state(doc):
    return ([layer.image.tobytes() for layer in doc.layers],
            [layer.mask.tobytes() if layer.mask is not None else None for layer in doc.layers],
            doc.selection.tobytes() if doc.selection is not None else None,doc.revision,len(doc.undo_stack))


@pytest.mark.parametrize("operation,kwargs",[
    ("translate",{}),("scale",{}),("rotate",{}),("shear",{}),("unified",{}),("3d",{}),
    ("perspective",{"corners":((0,0),(31,0),(31,23),(0,23))}),
    ("handles",{"source":((0,0),(20,0)),"destination":((0,0),(20,0))}),
    ("handles",{"source":((0,0),(20,0),(0,20)),"destination":((0,0),(20,0),(0,20))}),
    ("cage",{"source":((3,3),(25,3),(25,20),(3,20)),"destination":((3,3),(25,3),(25,20),(3,20))}),
    ("warp",{"amount":0}),
])
def test_identity_is_byte_exact_and_independent(operation,kwargs):
    source = image()
    result = transform_image(source,operation,**kwargs)
    assert result.size == source.size and result.mode == "RGBA"
    assert result.tobytes() == source.tobytes()
    assert result is not source
    mask = source.getchannel("A")
    assert transform_image(mask,operation,**kwargs).tobytes() == mask.tobytes()


def test_translation_moves_pixels_with_zero_border_and_mask_exactly():
    source = image()
    translated = transform_image(source,"translate",dx=3,dy=2)
    assert translated.getpixel((12,9)) == source.getpixel((9,7))
    assert translated.getpixel((5,4)) == (0,0,0,0)
    expected = Image.new("RGBA",source.size); expected.paste(source,(3,2))
    assert translated.tobytes() == expected.tobytes()
    mask = source.getchannel("A")
    assert transform_image(mask,"translate",dx=3,dy=2).tobytes() == expected.getchannel("A").tobytes()


@pytest.mark.parametrize("count",[1,2,3,4])
def test_handle_transform_solves_actual_corresponding_points(count):
    source = ((2,2),(18,2),(18,18),(2,18))[:count]
    destination = [(x+3,y+2) for x,y in source]
    matrix = geometry_matrix("handles",(31,23),source=source,destination=destination)
    for before,after in zip(source,destination):
        mapped = matrix @ np.array((*before,1))
        assert mapped[:2]/mapped[2] == pytest.approx(after)
    assert transform_image(image(),"handles",source=source,destination=destination).tobytes() == transform_image(image(),"translate",dx=3,dy=2).tobytes()


def test_perspective_maps_all_four_corners_and_shear_has_real_off_diagonal():
    destination = ((2,1),(28,3),(26,21),(4,20))
    matrix = geometry_matrix("perspective",(31,23),corners=destination)
    for before,after in zip(((0,0),(31,0),(31,23),(0,23)),destination):
        result = matrix @ np.array((*before,1)); assert result[:2]/result[2] == pytest.approx(after)
    shear = geometry_matrix("shear",(31,23),shx=.5,pivot=(0,0))
    result = shear @ np.array((6,8,1)); assert result[:2] == pytest.approx((10,8))
    assert transform_image(image(),"perspective",corners=destination).tobytes() != image().tobytes()


def test_unified_transforms_keep_pivot_and_compose_translation_rotation_scale():
    matrix = geometry_matrix("unified",(31,23),pivot=(5,8),sx=2,sy=3,angle=90,dx=4,dy=6)
    assert (matrix @ np.array((5,8,1)))[:2] == pytest.approx((9,14))
    assert (matrix @ np.array((6,8,1)))[:2] == pytest.approx((9,16))
    three_d = geometry_matrix("3d",(31,23),rx=30,ry=20,rz=15)
    assert three_d[2,0] != 0 and three_d[2,1] != 0
    projected = transform_image(image(),"3d",rx=30,ry=20,rz=15,resample="bilinear")
    assert projected.getbbox() is not None and projected.tobytes() != image().tobytes()


def test_bilinear_sampling_retains_transparent_color_without_dark_edge():
    source = Image.new("RGBA",(2,1)); source.putpixel((0,0),(255,0,0,255))
    result = remap_image(source,lambda x,y:(x+.5,y),resample="bilinear")
    assert result.getpixel((0,0)) == (255,0,0,128)
    mask = Image.new("L",(2,1)); mask.putpixel((0,0),255)
    assert remap_image(mask,lambda x,y:(x+.5,y),resample="bilinear").getpixel((0,0)) == 128


@pytest.mark.parametrize("mode",["push","grow","shrink","whirl"])
def test_warp_brush_changes_local_pixels_and_preserves_outside(mode):
    source = image((81,71))
    ImageDraw.Draw(source).rectangle((22,22,42,40),fill=(180,30,210,255))
    result = transform_image(source,"warp",mode=mode,center=(30,30),radius=18,amount=.8,delta=(9,5),resample="bilinear")
    assert result.tobytes() != source.tobytes()
    assert result.crop((0,0,10,10)).tobytes() == source.crop((0,0,10,10)).tobytes()
    assert result.crop((55,55,80,70)).tobytes() == source.crop((55,55,80,70)).tobytes()


def test_cage_handles_shift_content_and_keep_outside_untouched():
    source = image()
    before = ((3,2),(23,2),(23,19),(3,19))
    after = [(x+3,y+1) for x,y in before]
    result = transform_image(source,"cage",source=before,destination=after)
    assert result.getpixel((12,8)) == source.getpixel((9,7))
    assert result.getpixel((0,0)) == source.getpixel((0,0))


def test_layer_and_mask_commands_selection_target_and_atomic_history():
    session = workspace(); doc = session.document
    doc.layer.mask = doc.layer.image.getchannel("A")
    before = state(doc)
    assert run(session,"geometry translate 3 2").changed
    assert doc.layer.image.getpixel((12,9)) == (0,200,30,255)
    assert doc.layer.mask.getpixel((12,9)) == 255
    translated = state(doc)
    assert doc.undo(); assert state(doc)[:3] == before[:3]
    assert doc.redo(); assert state(doc)[:3] == translated[:3]
    doc.selection = Image.new("L",doc.size); doc.selection.putpixel((8,8),128)
    run(session,"geometry translate 2 1 --target selection")
    assert doc.selection.getpixel((10,9)) == 128 and doc.selection.getpixel((8,8)) == 0
    assert doc.layer.image.tobytes() == translated[0][0]


@pytest.mark.parametrize("value",[
    "geometry shear 1 1","geometry scale 0","geometry warp grow 8 8 4 -1",
    "geometry perspective 0,0 20,20 20,0 0,20",
    "geometry handles --from '0,0;0,0' --to '1,1;2,2'",
    "geometry cage --from '0,0;1,1;2,2' --to '0,0;1,1;2,2'",
    "geometry warp push 8 8 4 1 --resample bicubic",
    "geometry translate 2 3 --target unknown","geometry rotate nan",
])
def test_bad_geometry_is_transactional(value):
    session = workspace(); before = state(session.document)
    with pytest.raises(ValueError): run(session,value)
    assert state(session.document) == before


def test_locked_layers_and_mid_transform_failures_roll_back_every_target(monkeypatch):
    session = workspace(); doc = session.document
    doc.layers.append(Layer("Second",image()))
    doc.layers[1].locked = True
    before = state(doc)
    with pytest.raises(ValueError): run(session,"geometry translate 1 2 --target all-layers")
    assert state(doc) == before
    doc.layers[1].locked = False
    from termatelier import geometry_commands
    original = geometry_commands.transform_image
    calls = []
    def fail(*args,**kwargs):
        calls.append(1)
        if len(calls) == 2: raise RuntimeError("Injected renderer error")
        return original(*args,**kwargs)
    monkeypatch.setattr(geometry_commands,"transform_image",fail)
    with pytest.raises(RuntimeError): run(session,"geometry translate 1 2 --target all-layers")
    assert state(doc) == before


def test_measure_distances_and_angles_does_not_modify_state():
    session = workspace(); before = state(session.document)
    data = json.loads(run(session,"measure 0,0 3,4 6,0 --json").text)
    assert data["segments"] == [5,5] and data["length"] == 10
    assert data["direction"] == 0 and data["angles"][0] == pytest.approx(73.739795)
    assert state(session.document) == before


def test_multi_layer_distribution_equal_gaps_and_mask_alignment():
    session = workspace((80,40)); doc = session.document; doc.layers = []
    for index,(x,width) in enumerate(((2,8),(22,5),(64,10))):
        pixels = Image.new("RGBA",doc.size)
        ImageDraw.Draw(pixels).rectangle((x,5,x+width-1,12),fill="red")
        doc.layers.append(Layer(str(index+1),pixels,mask=pixels.getchannel("A")))
    doc.active = 0
    before = state(doc)
    run(session,"distribute horizontal --layers 1,2,3")
    boxes = [layer.image.getbbox() for layer in doc.layers]
    gaps = [boxes[i+1][0]-boxes[i][2] for i in range(2)]
    assert abs(gaps[0]-gaps[1]) <= 1
    assert boxes[0][0] == 2 and boxes[-1][2] == 74
    assert all(layer.image.getchannel("A").tobytes() == layer.mask.tobytes() for layer in doc.layers)
    assert doc.undo() and state(doc)[:3] == before[:3]
    run(session,"distribute right --layers 1,2,3")
    assert all(layer.image.getbbox()[2] == 80 for layer in doc.layers)


def test_large_deformation_finishes_with_tiled_sampling():
    source = Image.new("RGBA",(1024,1024),(90,110,200,180))
    start = time.monotonic()
    result = transform_image(source,"warp",mode="whirl",center=(512,512),radius=400,amount=.8,resample="bilinear")
    assert result.size == source.size and result.getpixel((0,0)) == source.getpixel((0,0))
    assert time.monotonic()-start < 20
