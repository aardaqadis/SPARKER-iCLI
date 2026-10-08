"""OpenRaster interoperable ZIP/XML layout and exact layered pixel workflows."""
import io
from pathlib import Path
import random
import stat
import struct
import warnings
import xml.etree.ElementTree as ET
import zipfile
import zlib

from PIL import Image, ImageChops
import pytest

from termatelier.model import Document, Layer
from termatelier.ora_tools import BLEND_TO_ORA, MIMETYPE, load_ora, save_ora
from termatelier.storage import png_bytes


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKER_CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("SPARKER_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("SPARKER_PROJECT_ROOT", str(tmp_path / "checkout"))


def document(size=(31,19)):
    doc = Document(*size)
    rng = random.Random(17)
    for layer in doc.layers:
        layer.image.putdata([tuple(rng.randrange(256) for _ in range(4)) for _ in range(size[0]*size[1])])
    doc.layer.name = 'Ink & "color" <snow> 漫画'
    doc.layer.opacity = .37
    doc.layer.mask = Image.new("L", doc.size, 180)
    doc.layer.mask.putpixel((5,5), 0)
    doc.layer.locked = True
    doc.metadata.update(title="Mountainside & snow", resolution_x=144., resolution_y=200.)
    return doc


def member_image(path, name):
    with zipfile.ZipFile(path) as archive:
        with Image.open(io.BytesIO(archive.read(name))) as image:
            return image.convert("RGBA")


def archive(tmp_path, *, xml=None, layers=None, extra=None, first="mimetype", mimetype=MIMETYPE,
            mimetype_compression=zipfile.ZIP_STORED, omit=(), compression=zipfile.ZIP_STORED):
    path = tmp_path / "input.ora"
    default = Image.new("RGBA", (3,2), (255,0,0,128))
    xml = xml if xml is not None else b'<image version="0.0.6" w="5" h="4"><stack><layer src="data/one.png" x="1" y="1" /></stack></image>'
    entries = {"mimetype": mimetype, "stack.xml": xml,
               "mergedimage.png": png_bytes(Image.new("RGBA", (5,4))),
               "Thumbnails/thumbnail.png": png_bytes(Image.new("RGBA", (5,4))),
               "data/one.png": png_bytes(default)}
    if layers:
        entries.update(layers)
    if extra:
        entries.update(extra)
    with zipfile.ZipFile(path,"w",compression=compression) as z:
        if first in entries and first not in omit:
            z.writestr(first, entries.pop(first), compress_type=mimetype_compression if first == "mimetype" else compression)
        for name, data in entries.items():
            if name not in omit:
                z.writestr(name,data,compress_type=mimetype_compression if name == "mimetype" else compression)
    return path


@pytest.mark.parametrize("blend", list(BLEND_TO_ORA))
def test_standard_roundtrip_layer_order_rgba_alpha_opacity_blend_and_no_document_mutation(tmp_path, blend):
    doc = document()
    doc.layer.blend = blend
    doc.layers.append(Layer("Hidden", Image.new("RGBA", doc.size, "yellow"), visible=False))
    raw = [layer.image.tobytes() for layer in doc.layers]
    mask = doc.layers[1].mask.tobytes()
    expected = doc.composite().tobytes()
    path = save_ora(doc, tmp_path / "painting.ora")
    imported = load_ora(path)
    assert imported.size == doc.size and len(imported.layers) == 3
    assert [layer.name for layer in imported.layers] == [layer.name for layer in doc.layers]
    assert imported.layers[1].opacity == .37 and imported.layers[1].blend == blend
    assert imported.layers[1].locked and not imported.layers[2].visible and imported.active == 1
    assert imported.composite().tobytes() == expected
    assert imported.layers[0].image.tobytes() == doc.layers[0].image.tobytes()
    baked = doc.layers[1].image.copy()
    baked.putalpha(ImageChops.multiply(baked.getchannel("A"), doc.layers[1].mask))
    assert imported.layers[1].image.tobytes() == baked.tobytes()
    assert imported.layers[1].mask is None
    assert imported.metadata["title"] == doc.metadata["title"]
    assert imported.metadata["resolution_x"] == 144 and imported.metadata["resolution_y"] == 200
    assert [layer.image.tobytes() for layer in doc.layers] == raw and doc.layers[1].mask.tobytes() == mask
    assert doc.revision == 0 and not doc.undo_stack


def test_writes_actual_openraster_layout_first_uncompressed_mimetype_and_native_preview(tmp_path):
    doc = document((600,200))
    path = save_ora(doc, tmp_path / "layout.ora")
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        assert entries[0].filename == "mimetype" and entries[0].compress_type == zipfile.ZIP_STORED
        assert entries[0].header_offset == 0 and z.read("mimetype") == b"image/openraster"
        tree = ET.fromstring(z.read("stack.xml"))
        assert tree.tag == "image" and tree.attrib["w"] == "600" and tree.attrib["h"] == "200"
        assert tree[0][0].get("name") == doc.layers[-1].name
        assert tree[0][0].get("opacity") == "0.37" and tree[0][0].get("selected") == "true"
        assert tree[0][0].get("edit-locked") == "true"
        assert all(layer.get("src") in z.namelist() for layer in tree[0])
        assert z.testzip() is None
    assert member_image(path,"mergedimage.png").tobytes() == doc.composite().tobytes()
    assert member_image(path,"Thumbnails/thumbnail.png").size == (256,85)
    assert doc.size == (600,200)


def test_bakes_effects_and_mask_once_retains_separate_opacity(tmp_path):
    doc = document()
    doc.layer.effects = [{"kind":"tone", "name":"exposure", "options":{"stops":1}, "enabled":True, "opacity":.5}]
    original = doc.layer.image.tobytes()
    expected = doc.composite().tobytes()
    path = save_ora(doc,tmp_path / "effect.ora")
    result = load_ora(path)
    assert result.composite().tobytes() == expected
    assert not result.layer.effects and result.layer.mask is None and result.layer.opacity == .37
    assert doc.layer.image.tobytes() == original and doc.layer.effects


def test_signed_offsets_and_small_source_images_have_exact_rgba_canvas_pixels(tmp_path):
    src = Image.new("RGBA",(3,2))
    src.putdata([(10+i,20+i,30+i,40+i) for i in range(6)])
    xml = b'<image w="5" h="4"><stack><layer name="negative" src="data/one.png" x="-1" y="1" opacity="0.5" selected="1" edit-locked="1" /></stack></image>'
    path = archive(tmp_path,xml=xml,layers={"data/one.png":png_bytes(src)})
    doc = load_ora(path)
    expected = Image.new("RGBA",(5,4))
    expected.paste(src,(-1,1))
    assert doc.layer.image.tobytes() == expected.tobytes()
    assert doc.layer.opacity == .5 and doc.layer.locked and doc.active == 0


def test_png_palette_alpha_preserved_and_deflated_zip_supported(tmp_path):
    source = Image.new("P", (3,2), 0)
    source.putpalette([255,0,0,0,255,0]+[0]*762)
    source.putpixel((1,0),1)
    source.info["transparency"] = bytes([0,128])
    path = archive(tmp_path,layers={"data/one.png":png_bytes(source)},compression=zipfile.ZIP_DEFLATED)
    result = load_ora(path)
    assert result.layer.image.getpixel((2,1)) == (0,255,0,128)
    assert result.layer.image.getpixel((1,1)) == (255,0,0,0)


@pytest.mark.parametrize("blend", ["add","subtract"])
def test_unsupported_native_blends_are_rejected_without_clobbering_destination(tmp_path, blend):
    doc = document()
    doc.layer.blend = blend
    path = tmp_path / "existing.ora"
    path.write_bytes(b"Existing data")
    with pytest.raises(ValueError,match="blend mode"):
        save_ora(doc,path)
    assert path.read_bytes() == b"Existing data"


def test_default_external_directory_nested_export_and_project_folder_rejection(tmp_path):
    result = save_ora(document(), "nested/painting.ora")
    assert result == tmp_path / "exports" / "nested" / "painting.ora" and result.is_file()
    forbidden = tmp_path / "checkout" / "leak.ora"
    with pytest.raises(ValueError,match="program/project folder"):
        save_ora(document(),forbidden)
    assert not forbidden.exists()


def test_atomic_midwrite_failure_keeps_original_and_document_intact(tmp_path,monkeypatch):
    import termatelier.ora_tools as ora
    target = tmp_path / "protected.ora"
    target.write_bytes(b"Existing data")
    doc = document()
    original = doc.composite().tobytes()
    encode = ora.png_bytes
    calls = 0
    def fail(image):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Encoder interrupted")
        return encode(image)
    monkeypatch.setattr(ora,"png_bytes",fail)
    with pytest.raises(RuntimeError,match="Encoder interrupted"):
        save_ora(doc,target)
    assert target.read_bytes() == b"Existing data" and doc.composite().tobytes() == original
    assert not list(tmp_path.glob(".termatelier-*"))


@pytest.mark.parametrize("first,content,compression", [
    ("stack.xml",MIMETYPE,zipfile.ZIP_STORED),
    ("mimetype",b"image/openraster\n",zipfile.ZIP_STORED),
    ("mimetype",MIMETYPE,zipfile.ZIP_DEFLATED),
])
def test_rejects_wrong_mimetype_layout(tmp_path,first,content,compression):
    path = archive(tmp_path,first=first,mimetype=content,mimetype_compression=compression)
    with pytest.raises(ValueError,match="mimetype"):
        load_ora(path)


@pytest.mark.parametrize("missing",["mimetype","stack.xml","mergedimage.png","Thumbnails/thumbnail.png","data/one.png"])
def test_missing_required_member_or_layer_reference_rejected(tmp_path,missing):
    path = archive(tmp_path,omit=(missing,))
    with pytest.raises(ValueError):
        load_ora(path)


@pytest.mark.parametrize("src",["../outside.png","/absolute.png","data/../one.png","data\\one.png",
                               "https://site/image.png","data//one.png","data/./one.png","data/","missing.png"])
def test_rejects_unsafe_references_without_extracting_anything(tmp_path,src):
    xml = f'<image w="5" h="4"><stack><layer src="{src}" /></stack></image>'.encode()
    path = archive(tmp_path,xml=xml)
    with pytest.raises(ValueError):
        load_ora(path)
    assert not (tmp_path.parent / "outside.png").exists()


@pytest.mark.parametrize("xml",[
    b'<image w="5" h="4"><stack><stack><layer src="data/one.png"/></stack></stack></image>',
    b'<image w="5" h="4"><stack><filter name="blur"/></stack></image>',
    b'<image w="5" h="4"><stack><text/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" composite-op="svg:plus"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" composite-op="svg:subtract"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" opacity="nan"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" opacity="2"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" visibility="maybe"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" x="32769"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" y="1.5"/></stack></image>',
    b'<image w="5" h="4"><stack><layer src="data/one.png" selected="yes"/></stack></image>',
    b'<image w="5" h="4"><stack opacity=".5"><layer src="data/one.png" /></stack></image>',
    b'<image w="5" h="4"><stack visibility="hidden"><layer src="data/one.png" /></stack></image>',
    b'<image w="4096" h="4096"><stack><layer src="data/one.png" /></stack></image>',
    b'<image w="5" h="4"><stack/></image>',
    b'<image w="5" h="4"><stack/><stack/></image>',
    b'<notimage w="5" h="4"><stack/></notimage>',
    b'<image w="0" h="4"><stack/></image>',
    b'<image w="5.0" h="4"><stack/></image>',
    b'<image w="5" h="4"><stack>',
    b'<?xml version="1.0" encoding="ISO-8859-1"?><image w="5" h="4"><stack/></image>',
    b'<!DOCTYPE image [<!ENTITY subject "expanded">]><image w="5" h="4"><stack><layer src="data/one.png" name="&subject;"/></stack></image>',
    b'<!DOCTYPE image SYSTEM "file:///private"><image w="5" h="4"><stack/></image>',
])
def test_malformed_or_unsupported_xml_is_rejected(tmp_path,xml):
    with pytest.raises(ValueError):
        load_ora(archive(tmp_path,xml=xml))


def test_duplicate_zip_names_and_unsafe_unused_members_rejected(tmp_path):
    path = archive(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore",UserWarning)
        with zipfile.ZipFile(path,"a") as z:
            z.writestr("data/one.png",png_bytes(Image.new("RGBA",(3,2))))
    with pytest.raises(ValueError,match="duplicate"):
        load_ora(path)
    path = archive(tmp_path,extra={"../unused.txt":b"ignored"})
    with pytest.raises(ValueError,match="relative paths"):
        load_ora(path)


def test_zip_symlink_and_unsupported_compression_rejected(tmp_path):
    path = archive(tmp_path)
    with zipfile.ZipFile(path,"a") as z:
        info = zipfile.ZipInfo("data/link.png")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info,b"outside.png")
    with pytest.raises(ValueError,match="symlinks"):
        load_ora(path)
    path = archive(tmp_path)
    with zipfile.ZipFile(path,"a") as z:
        z.writestr("extra.txt",b"extra",compress_type=zipfile.ZIP_BZIP2)
    with pytest.raises(ValueError,match="compression"):
        load_ora(path)


def test_xml_size_member_count_and_zip_expanded_budget_enforced_before_decode(tmp_path,monkeypatch):
    import termatelier.ora_tools as ora
    path = archive(tmp_path,xml=b" " * (ora.MAX_STACK_BYTES+1))
    with pytest.raises(ValueError,match="256 KiB"):
        load_ora(path)
    path = archive(tmp_path,extra={f"extra/{i}.txt":b"" for i in range(252)})
    with pytest.raises(ValueError,match="member count"):
        load_ora(path)
    path = archive(tmp_path,extra={"large.txt":b"x" * 5000},compression=zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(ora,"MAX_ORA_BYTES",4000)
    with pytest.raises(ValueError,match="expanded data budget"):
        load_ora(path)


def test_layer_count_and_pixel_budget_validated_before_loading_any_png(tmp_path,monkeypatch):
    import termatelier.ora_tools as ora
    xml = '<image w="5" h="4"><stack>'+ '<layer src="data/one.png" />'*65+'</stack></image>'
    path = archive(tmp_path,xml=xml.encode())
    monkeypatch.setattr(ora,"_read_png",lambda *a,**k:pytest.fail("Unexpected PNG decode"))
    with pytest.raises(ValueError,match="64 layer"):
        load_ora(path)


def test_non_png_apng_16bit_and_wrong_previews_rejected(tmp_path):
    jpeg = io.BytesIO()
    Image.new("RGB",(3,2)).save(jpeg,"JPEG")
    with pytest.raises(ValueError,match="single-frame PNGs"):
        load_ora(archive(tmp_path,layers={"data/one.png":jpeg.getvalue()}))
    sixteen = Image.new("I;16",(3,2),32000)
    with pytest.raises(ValueError,match="16-bit"):
        load_ora(archive(tmp_path,layers={"data/one.png":png_bytes(sixteen)}))
    animation = io.BytesIO()
    Image.new("RGBA", (3,2), "red").save(animation, "PNG", save_all=True,
        append_images=[Image.new("RGBA", (3,2), "blue")], duration=100, loop=0)
    with pytest.raises(ValueError,match="single-frame PNGs"):
        load_ora(archive(tmp_path,layers={"data/one.png":animation.getvalue()}))
    with pytest.raises(ValueError,match="merged image dimensions"):
        load_ora(archive(tmp_path,extra={"mergedimage.png":png_bytes(Image.new("RGBA",(2,2)))}))
    with pytest.raises(ValueError,match="thumbnails"):
        load_ora(archive(tmp_path,extra={"Thumbnails/thumbnail.png":png_bytes(Image.new("RGBA",(257,100)))}))


def test_png_headers_large_dimensions_and_sources_budget_rejected_without_loading(tmp_path,monkeypatch):
    import termatelier.ora_tools as ora
    source = Image.new("RGBA",(64,64),"red")
    path = archive(tmp_path,layers={"data/one.png":png_bytes(source)})
    read = ora._read_png
    def limited(z,m,name,**kwargs):
        if name == "data/one.png":
            kwargs["pixel_budget"] = 4096
        return read(z,m,name,**kwargs)
    monkeypatch.setattr(ora,"_read_png",limited)
    with pytest.raises(ValueError,match="decoded pixel budget"):
        load_ora(path)


def test_oversized_png_header_is_rejected_before_pixel_decode(tmp_path):
    encoded = png_bytes(Image.new("RGBA", (3,2), "red"))
    header = struct.pack(">IIBBBBB", 5000, 1, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + header
    oversized = encoded[:8] + struct.pack(">I", len(header)) + chunk + struct.pack(">I", zlib.crc32(chunk)) + encoded[33:]
    with pytest.raises(ValueError, match="4096"):
        load_ora(archive(tmp_path,layers={"data/one.png":oversized}))


def test_export_invalid_names_suffix_alpha_budget_and_native_dimensions(tmp_path):
    doc = document()
    doc.layer.name = "bad\x00name"
    with pytest.raises(ValueError,match="XML text"):
        save_ora(doc,tmp_path / "bad.ora")
    assert not (tmp_path / "bad.ora").exists()
    doc.layer.name = "Valid"
    with pytest.raises(ValueError,match=".ora extension"):
        save_ora(doc,tmp_path / "wrong.png")
    doc.layer.opacity = float("inf")
    with pytest.raises(ValueError,match="opacity"):
        save_ora(doc,tmp_path / "bad.ora")


def test_random_non_zip_is_clear_error(tmp_path):
    path = tmp_path / "corrupt.ora"
    path.write_bytes(b"not a zip archive")
    with pytest.raises(ValueError,match="OpenRaster archive"):
        load_ora(path)
