"""Color correctness, alpha fidelity and effect-stack-ready pure operations."""
import json
import time

import numpy as np
from PIL import Image, ImageDraw
import pytest

from termatelier.adjustment_tools import (ADJUSTMENTS,EFFECTS,apply_adjustment,apply_effect,
                                         validate_adjustment,validate_effect,rgb_to_hsv,hsv_to_rgb)
from termatelier.commands import CommandSession,tokenize
from termatelier.geometry_commands import execute_geometry
from termatelier.model import Document,Layer


def patterned(size=(41,33)):
    x,y = np.meshgrid(np.arange(size[0]),np.arange(size[1]))
    channels = ((x*17+y*3)%256,(x*5+y*23)%256,(x*11+y*7)%256,(x*9+y*13)%256)
    return Image.fromarray(np.stack(channels,axis=-1).astype(np.uint8))


def workspace():
    source = patterned()
    doc = Document(*source.size); doc.layers = [Layer("Paint",source)]; doc.active=0
    return CommandSession(doc)


def run(session,value):
    tokens=tokenize(value)
    return execute_geometry(session,tokens[0],tokens[1:])


@pytest.mark.parametrize("name",ADJUSTMENTS)
def test_all_adjustments_are_independent_rgba_preserve_alpha(name):
    source=patterned(); original=source.tobytes()
    result=apply_adjustment(source,name)
    assert result.mode == "RGBA" and result.size == source.size and result is not source
    assert source.tobytes() == original
    assert result.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    options = validate_adjustment(name)
    assert apply_adjustment(source,name,**json.loads(json.dumps(options))).tobytes() == result.tobytes()


@pytest.mark.parametrize("name",EFFECTS)
def test_all_effects_are_independent_rgba_reproducible(name):
    source=patterned(); original=source.tobytes()
    result=apply_effect(source,name)
    assert result.mode == "RGBA" and result.size == source.size and result is not source
    assert source.tobytes() == original
    options=validate_effect(name)
    assert apply_effect(source,name,**json.loads(json.dumps(options))).tobytes() == result.tobytes()


def test_exposure_levels_shadow_and_highlight_behavior():
    source=Image.new("RGBA",(2,1),(40,60,90,83)); source.putpixel((1,0),(230,240,250,191))
    assert apply_adjustment(source,"exposure",stops=1).getpixel((0,0)) == (80,120,180,83)
    levels=apply_adjustment(source,"levels",black=40,white=240)
    assert levels.getpixel((0,0)) == (0,26,64,83)
    assert levels.getpixel((1,0)) == (242,255,255,191)
    shadows=apply_adjustment(source,"shadows-highlights",shadows=.8)
    assert shadows.getpixel((0,0))[0]>40
    assert shadows.getpixel((1,0)) == source.getpixel((1,0))
    highlights=apply_adjustment(source,"shadows-highlights",highlights=.8)
    assert highlights.getpixel((1,0))[0]<230
    assert highlights.getpixel((0,0)) == source.getpixel((0,0))


def test_curves_support_channels_exact_control_points_and_transparent_rgb():
    source=Image.new("RGBA",(3,1)); source.putdata([(0,128,255,0),(64,128,192,128),(255,64,0,255)])
    identity=apply_adjustment(source,"curves")
    assert identity.tobytes() == source.tobytes()
    changed=apply_adjustment(source,"curves",channel="red",points="0,255;64,192;255,0")
    assert changed.getpixel((0,0)) == (255,128,255,0)
    assert changed.getpixel((1,0)) == (192,128,192,128)
    assert changed.getpixel((2,0)) == (0,64,0,255)
    alpha=apply_adjustment(source,"curves",channel="alpha",points=[[0,0],[128,64],[255,255]])
    assert alpha.getpixel((1,0)) == (64,128,192,64)


def test_hue_colorize_desaturation_and_range_balance():
    red=Image.new("RGBA",(1,1),(255,0,0,99))
    green=apply_adjustment(red,"hue-saturation",hue=120)
    assert green.getpixel((0,0)) == (0,255,0,99)
    gray=apply_adjustment(red,"hue-saturation",saturation=0)
    assert gray.getpixel((0,0)) == (128,128,128,99)
    white=apply_adjustment(red,"hue-saturation",lightness=1)
    assert white.getpixel((0,0)) == (255,255,255,99)
    values={apply_adjustment(red,"desaturate",method=method).getpixel((0,0))[0] for method in ("luma","luminance","lightness","average","value")}
    assert len(values)==5
    source=Image.new("RGBA",(2,1),(30,30,30,180)); source.putpixel((1,0),(230,230,230,100))
    balanced=apply_adjustment(source,"color-balance",red=.2,range="shadows")
    assert balanced.getpixel((0,0))[0]-30 > balanced.getpixel((1,0))[0]-230
    colorized=apply_adjustment(source,"colorize",hue=240,saturation=1)
    assert colorized.getpixel((0,0))[:2] == (0,0) and colorized.getpixel((1,0))[2]>colorized.getpixel((0,0))[2]


def test_hsv_conversion_round_trip_is_numerically_stable():
    rng=np.random.default_rng(4)
    rgb=rng.random((64,31,3),dtype=np.float32)
    assert np.allclose(hsv_to_rgb(rgb_to_hsv(rgb)),rgb,atol=1e-6)


def test_motion_blur_direction_premultiplied_alpha_and_pixelize_exact_blocks():
    source=Image.new("RGBA",(15,15)); source.putpixel((7,7),(255,0,0,255))
    blurred=apply_effect(source,"motion-blur",length=5,angle=0)
    assert blurred.getpixel((7,7)) == (255,0,0,51)
    assert blurred.getpixel((5,7)) == (255,0,0,51)
    assert blurred.getpixel((7,6)) == (0,0,0,0)
    vertical=apply_effect(source,"motion-blur",length=5,angle=90)
    assert vertical.getpixel((7,5)) == (255,0,0,51)
    assert vertical.getpixel((6,7)) == (0,0,0,0)
    source=patterned((19,13)); pixelized=apply_effect(source,"pixelize",size=8)
    for left,top in ((0,0),(8,0),(16,8)):
        block=pixelized.crop((left,top,min(19,left+8),min(13,top+8)))
        assert len(np.unique(np.asarray(block).reshape(-1,4),axis=0)) == 1


def test_gaussian_transparency_has_no_dark_fringe_and_zero_radius_exact():
    source=Image.new("RGBA",(9,1)); source.putpixel((4,0),(255,0,0,255))
    assert apply_effect(source,"gaussian-blur",radius=0).tobytes() == source.tobytes()
    result=apply_effect(source,"gaussian-blur",radius=1)
    assert result.getpixel((3,0))[0] == 255
    assert result.getpixel((3,0))[3]>0


def test_red_eye_targets_red_dominant_pixels_and_denoise_removes_isolated_noise():
    source=Image.new("RGBA",(9,9),(50,50,50,123))
    source.putpixel((4,4),(240,20,30,200)); source.putpixel((3,3),(20,220,30,80))
    corrected=apply_effect(source,"red-eye")
    assert corrected.getpixel((4,4)) == (25,20,30,200)
    assert corrected.getpixel((3,3)) == source.getpixel((3,3))
    denoised=apply_effect(source,"denoise")
    assert denoised.getpixel((4,4)) == (50,50,50,200)


def test_shadow_uses_alpha_and_offsets_without_wrapping():
    source=Image.new("RGBA",(15,15)); source.putpixel((4,4),(255,0,0,255))
    result=apply_effect(source,"shadow",dx=3,dy=2,radius=0,opacity=.5,color="#0000ff")
    assert result.getpixel((4,4)) == (255,0,0,255)
    assert result.getpixel((7,6)) == (0,0,255,128)
    assert result.getpixel((0,0)) == (0,0,0,0)
    source.putpixel((14,14),(255,0,0,255))
    assert apply_effect(source,"shadow",dx=3,dy=2,radius=0).getpixel((2,1)) == (0,0,0,0)


@pytest.mark.parametrize("name,options",[
    ("ripple",{"amount":0}),("waves",{"amount":0}),("lens",{"amount":0}),
    ("whirl-pinch",{"angle":0,"pinch":0}),("displacement",{"amount":0}),
    ("rgb-noise",{"amount":0}),("hsv-noise",{"hue":0,"saturation":0,"value":0}),
])
def test_neutral_distortion_or_noise_preserves_every_pixel(name,options):
    source=patterned()
    assert apply_effect(source,name,**options).tobytes() == source.tobytes()


def test_seeded_noise_clouds_and_bump_mapping_are_real_operations():
    source=patterned()
    for name in ("rgb-noise","hsv-noise","clouds"):
        first=apply_effect(source,name,seed=37)
        second=apply_effect(source,name,seed=38)
        assert first.tobytes()!=second.tobytes() and first.tobytes()!=source.tobytes()
    cloud=apply_effect(source,"clouds",seed=3,**{"from":"#00000000","to":"#ff0000ff"})
    assert cloud.getchannel("R").tobytes() == cloud.getchannel("A").tobytes()
    bump=apply_effect(source,"bump-map",depth=2,azimuth=135)
    assert bump.tobytes()!=source.tobytes()
    assert bump.getchannel("A").tobytes()==source.getchannel("A").tobytes()


def test_tone_and_filter_commands_preserve_selection_mask_lock_and_undo():
    session=workspace(); doc=session.document
    doc.selection=Image.new("L",doc.size); doc.selection.putpixel((5,5),128)
    original=doc.layer.image.copy()
    assert run(session,"tone exposure --stops 1 --opacity 50%").changed
    full=apply_adjustment(original,"exposure",stops=1)
    expected=Image.composite(Image.blend(original,full,.5),original,doc.selection)
    assert doc.layer.image.tobytes()==expected.tobytes()
    assert doc.undo() and doc.layer.image.tobytes()==original.tobytes()
    run(session,"effect-filter rgb-noise --amount .2 --seed 42")
    assert doc.layer.image.getpixel((0,0))==original.getpixel((0,0))
    assert doc.layer.image.getpixel((5,5))!=original.getpixel((5,5))
    assert doc.undo() and doc.layer.image.tobytes()==original.tobytes()
    doc.layer.locked=True
    with pytest.raises(ValueError): run(session,"tone exposure --stops 2")
    assert doc.layer.image.tobytes()==original.tobytes()


@pytest.mark.parametrize("value",[
    "tone exposure --stops nan","tone levels --black 200 --white 100",
    "tone curves --points '0,0;128,128;64,255'","tone colorize --hue 400",
    "effect-filter clouds --octaves 100","effect-filter motion-blur --length 1.5",
    "effect-filter gaussian-blur --radius -1","effect-filter rgb-noise --seed 4294967296",
    "effect-filter unknown","tone exposure --unknown 1",
])
def test_invalid_operations_leave_pixels_and_history_unchanged(value):
    session=workspace(); doc=session.document; before=(doc.layer.image.tobytes(),doc.revision,len(doc.undo_stack))
    with pytest.raises(ValueError): run(session,value)
    assert (doc.layer.image.tobytes(),doc.revision,len(doc.undo_stack))==before


@pytest.mark.parametrize("validator,name,options",[
    (validate_adjustment,"curves",{"points":[[0,0],[255,float("nan")]]}),
    (validate_effect,"shadow",{"color":"bad-color"}),
    (validate_effect,"shadow",{"color":42}),
    (validate_effect,"rgb-noise",{"amount":[]}),
    (validate_effect,"lens",{"center":"3,4,5"}),
    (validate_adjustment,"exposure",{"shell":"arbitrary code"}),
    (validate_effect,"unknown",{}),
])
def test_pure_validators_reject_malformed_or_unknown_persisted_data(validator,name,options):
    with pytest.raises(ValueError): validator(name,options)


def test_single_pixel_filters_have_defined_no_crash_output():
    source=Image.new("RGBA",(1,1),(90,70,50,123))
    for name in EFFECTS:
        assert apply_effect(source,name).size==(1,1)


def test_large_tone_and_effect_processing_finishes_with_bounded_tiles():
    source=Image.new("RGBA",(1024,1024),(90,110,200,180))
    start=time.monotonic()
    for name in ("exposure","hue-saturation","shadows-highlights"):
        assert apply_adjustment(source,name).size==source.size
    for name in ("motion-blur","waves","rgb-noise","bump-map","clouds"):
        assert apply_effect(source,name).size==source.size
    assert time.monotonic()-start<30
