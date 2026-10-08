"""Check installed CLI workflows and exact full-resolution image exports."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import tomllib


def run_check(dist):
    project = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project/"pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = metadata["version"]
    wheel = (dist/f"sparker_icli-{version}-py3-none-any.whl").resolve()
    if not wheel.is_file():
        raise SystemExit(f"Build the project first; wheel missing: {wheel}")
    with tempfile.TemporaryDirectory(prefix="sparker-wheel-check-") as name:
        root = Path(name)
        site = root/"installed"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target",
                        str(site), str(wheel)], check=True, capture_output=True, text=True, timeout=60)
        env = dict(os.environ, PYTHONPATH=str(site), SPARKER_CONFIG_FILE=str(root/"settings.json"),
                   SPARKER_EXPORT_DIR=str(root/"exports"))
        for key in ("SPARKER_DEBUG_STATE", "SPARKER_STARTUP_STATE", "SPARKER_CONFIG_OVERRIDES",
                    "SPARKER_PROJECT_ROOT"):
            env.pop(key, None)

        def execute(args):
            completed = subprocess.run([sys.executable, *args], cwd=root, env=env,
                capture_output=True, text=True, timeout=60)
            if completed.returncode:
                raise RuntimeError(completed.stdout+completed.stderr)
            return completed.stdout

        details = json.loads(execute(["-c", "import json,importlib.metadata as m,termatelier; "
            "print(json.dumps({'file':termatelier.__file__,'version':m.version('sparker-icli'),"
            "'license':m.metadata('sparker-icli')['License-Expression']}))"]))
        assert Path(details["file"]).is_relative_to(site), details
        assert details["version"] == version and details["license"] == "MIT", details
        help_text = execute(["-m", "sparkericli", "--help"])
        assert "SPARKER" in help_text and "--low-memory" in help_text
        execute(["-m", "sparkericli", "--new", "31x19", "--no-debug", "-c",
                 "pencil 2,3 --color orange", "--export", "native.png"])
        execute(["-c", "from PIL import Image; "
            "from termatelier.model import Document; "
            "d=Document(31,19); d.paint_mask(d.stroke_mask([(2,3)],1,1),'orange'); "
            "p=Image.open('exports/native.png'); "
            "assert p.size==(31,19); assert p.convert('RGBA').tobytes()==d.composite().tobytes()"])
        execute(["-m", "sparkericli", "--new", "31x19", "--no-debug", "-c",
                 "pencil 2,3 --color orange", "--export", "enlarged.png", "--export-scale", "4",
                 "--save-project", "enlarged.tart"])
        execute(["-c", "from PIL import Image; from termatelier.storage import load_project; "
            "native=Image.open('exports/native.png').convert('RGBA'); "
            "enlarged=Image.open('exports/enlarged.png').convert('RGBA'); "
            "assert enlarged.size==(124,76); "
            "assert enlarged.tobytes()==native.resize((124,76),Image.Resampling.NEAREST).tobytes(); "
            "project=load_project('enlarged.tart'); assert project.size==(31,19); "
            "assert project.composite().tobytes()==native.tobytes()"])
        tools = json.loads(execute(["-m", "sparkericli", "-c", "tools count --json"]))
        assert tools["total"] == 1248, tools
        low = json.loads(execute(["-m", "sparkericli", "--low-memory", "-c", "config list --json"]))
        assert low["memory.mode"] == "low" and low["history.storage"] == "compressed"
        assert low["history.max_mb"] == 16 and low["history.max_steps"] == 8
        assert not low["preview.enabled"] and not low["debug.enabled"]
        execute(["-m", "sparkericli", "--new", "31x19", "--low-memory", "-c",
                 "pencil 2,3 --color orange", "--export", "low.png", "--save-project", "low.tart"])
        execute(["-c", "from PIL import Image; from termatelier.storage import load_project; "
            "a=Image.open('exports/native.png').convert('RGBA'); b=Image.open('exports/low.png').convert('RGBA'); "
            "assert a.size==b.size==(31,19); assert a.tobytes()==b.tobytes(); "
            "assert load_project('low.tart').composite().tobytes()==a.tobytes()"])
        painting = [
            "gradient 0 0 959 639 --from #15243e --to #e79335",
            "layer add Ink",
            "pencil 4,4 953,631 --color orange --size 1",
            "rectangle 300 200 335 239 --filled --color #38b2ac",
            "copy --box 300,200,336,240",
            "paste 500 360 --name Accent --flip-h",
            "polygon 20,500 90,470 150,510 --filled --color #cc3377",
            "bezier 5,500 200,600 300,350 500,500 --width 2 --color white",
            'text 480 20 "Full resolution" --size 22 --wrap 300 --align center --stroke 1 --shadow 2,2 --layer Title',
        ]
        arguments = ["-m", "sparkericli", "--new", "960x640", "--no-debug"]
        for command in painting:
            arguments.extend(["-c", command])
        arguments.extend(["-c", "export painting.tiff", "-c", "export painting.webp",
                          "--export", "painting.png", "--save-project", "painting.tart"])
        execute(arguments)
        execute(["-c", "from PIL import Image; "
            "from termatelier.commands import CommandSession; "
            "from termatelier.model import Document; "
            "from termatelier.storage import load_project; "
            f"session=CommandSession(Document(960,640)); commands={painting!r}; "
            "[session.execute(command) for command in commands]; "
            "original=session.document; project=load_project('painting.tart'); "
            "assert project.size==original.size==(960,640); "
            "assert len(project.layers)==len(original.layers)==5; "
            "assert all(a.image.tobytes()==b.image.tobytes() for a,b in zip(project.layers,original.layers)); "
            "raw=original.composite(); assert project.composite().tobytes()==raw.tobytes(); "
            "exports=[Image.open('exports/painting.'+extension).convert('RGBA') for extension in ('png','tiff','webp')]; "
            "assert all(image.size==(960,640) and image.tobytes()==raw.tobytes() for image in exports); "
            "assert exports[0].crop((0,0,64,64)).tobytes()==raw.crop((0,0,64,64)).tobytes()"])
        measurement = json.loads(execute(["-m", "sparkericli", "-c", 'text measure "Full resolution" --wrap 80 --json']))
        assert measurement["lines"], measurement
        search = json.loads(execute(["-m", "sparkericli", "-c", "commands clipboard --json"]))
        assert "clipboard" in search and "paste" in search, search
        features = json.loads(execute(["-m", "sparkericli", "-c", "features --json"]))
        assert {"painting", "selection", "geometry", "tone", "effects", "document", "files-and-ai"} <= set(features)
        assert {"clone", "heal", "airbrush", "smudge"} <= set(features["painting"]["tools"])
        assert {"curves", "exposure", "levels"} <= set(features["tone"]["tools"])
        assert "openraster" in features["files-and-ai"]["tools"]

        # Exercise actual installed command adapters and check changed pixels,
        # editable structures, lossless native persistence and ORA interchange.
        # This intentionally needs no remote AI service or optional MCP SDK.
        execute(["-c", textwrap.dedent('''\
            import json
            from pathlib import Path
            import zipfile
            from PIL import Image
            from termatelier.commands import CommandSession
            from termatelier.document_tools import decode_channel
            from termatelier.model import Document
            from termatelier.storage import load_project
            from termatelier.ora_tools import load_ora

            session = CommandSession(Document(96,64))
            doc = session.document
            session.execute("gradient 0 0 95 63 --from #204060 --to #6080a0")
            session.execute("layer add Retouch")
            session.execute("gradient 0 0 95 63 --from #406080 --to #8090a0")
            session.execute("rectangle 3 3 12 12 --filled --color #804020")
            session.execute("pencil 6,6 --color white --size 1")
            source = doc.layer.image.getpixel((6,6))
            previous = doc.layer.image.tobytes()
            session.execute("clone 40,10 44,10 --source 6,6 --size 1 --hardness 1")
            assert doc.layer.image.getpixel((40,10)) == source
            cloned = doc.layer.image.tobytes()
            session.execute("undo")
            assert doc.layer.image.tobytes() == previous
            session.execute("redo")
            assert doc.layer.image.tobytes() == cloned
            previous = doc.layer.image.getpixel((60,20))
            session.execute("heal 60,20 --source 6,6 --size 5 --radius 2 --hardness 1")
            assert doc.layer.image.getpixel((60,20)) != previous
            assert doc.layer.image.getpixel((60,20))[3] == previous[3]
            session.execute("smudge 6,6 28,6 --size 3 --strength .8")
            previous = doc.layer.image.getpixel((45,30))
            session.execute("retouch burn 45,30 --size 3 --strength 1 --range all --exposure 1")
            assert doc.layer.image.getpixel((45,30))[0] < previous[0]
            before = doc.layer.image.copy()
            session.execute("geometry translate 3 2 --resample nearest")
            shifted = Image.new("RGBA",doc.size)
            shifted.paste(before,(3,2))
            assert doc.layer.image.tobytes() == shifted.tobytes()
            before = doc.layer.image.copy()
            session.execute("tone exposure --stops .25")
            assert doc.layer.image.tobytes() != before.tobytes()
            assert doc.layer.image.getchannel("A").tobytes() == before.getchannel("A").tobytes()
            before = doc.layer.image.copy()
            session.execute("effect-filter rgb-noise --amount .03 --seed 7")
            assert doc.layer.image.tobytes() != before.tobytes()
            assert doc.layer.image.getchannel("A").tobytes() == before.getchannel("A").tobytes()
            session.execute("select rectangle 12 8 80 56")
            selected = doc.selection.tobytes()
            session.execute("channel save Subject")
            session.execute("select none")
            session.execute("channel load Subject")
            assert doc.selection.tobytes() == selected
            session.execute("channel mask Subject")
            assert doc.layer.mask.tobytes() == selected
            session.execute("select none")
            session.execute("layer blend multiply")
            session.execute("path curve Horizon 3,30 20,10 55,50 90,30")
            session.execute("path point Horizon 2 22,12")
            session.execute("path stroke Horizon --color orange --width 2")
            assert doc.metadata["paths"]["Horizon"]["points"][1] == [22,12]
            session.execute('text 4 4 "Original title" --layer Caption --size 12 --wrap 85')
            session.execute('text-edit "Edited title" --color white --align center')
            assert doc.layer.text_recipe["text"] == "Edited title"
            before = doc.layer.image.tobytes()
            session.execute('fx add tone exposure --args "{"stops":0.25}" --opacity .6')
            session.execute('fx add filter shadow --args "{"dx":1,"dy":1,"radius":1}" --opacity .75')
            assert doc.layer.image.tobytes() == before and len(doc.layer.effects) == 2
            session.execute("fx hide 2")
            assert not doc.layer.effects[1]["enabled"]
            session.execute("fx show 2")
            session.execute("select ellipse 1 1 60 35")
            original = doc.composite()
            session.execute("save features.tart")
            session.execute("export features.png --scale 1")
            session.execute("export features.ora --scale 1")
            project = load_project("features.tart")
            assert project.size == doc.size == (96,64)
            assert project.composite().tobytes() == original.tobytes()
            assert project.selection.tobytes() == doc.selection.tobytes()
            assert project.metadata["paths"] == doc.metadata["paths"]
            assert project.metadata["channels"] == doc.metadata["channels"]
            assert decode_channel(project.metadata["channels"]["Subject"],doc.size).tobytes() == selected
            assert project.layer.text_recipe == doc.layer.text_recipe
            assert project.layer.effects == doc.layer.effects
            assert all(a.image.tobytes()==b.image.tobytes() for a,b in zip(project.layers,doc.layers))
            assert project.layers[-2].mask.tobytes() == doc.layers[-2].mask.tobytes()
            with zipfile.ZipFile("features.tart") as native:
                assert json.loads(native.read("manifest.json"))["version"] == 2
            output = Path("exports/features.ora")
            imported = load_ora(output)
            assert imported.size == doc.size and len(imported.layers) == len(doc.layers)
            assert imported.composite().tobytes() == original.tobytes()
            assert [layer.opacity for layer in imported.layers] == [layer.opacity for layer in doc.layers]
            assert [layer.blend for layer in imported.layers] == [layer.blend for layer in doc.layers]
            with zipfile.ZipFile(output) as ora:
                assert ora.infolist()[0].filename == "mimetype"
                assert ora.infolist()[0].compress_type == zipfile.ZIP_STORED
                assert ora.read("mimetype") == b"image/openraster"
            with Image.open("exports/features.png") as exported:
                assert exported.size == doc.size and exported.convert("RGBA").tobytes() == original.tobytes()
            print("Installed native families, editable .tart v2 and OpenRaster pixel workflows passed.")
            ''')])

        # Simulate the base installation even on development machines which
        # have the optional SDK. Registry edits and help must remain available
        # without importing it, starting executables or connecting to services.
        execute(["-c", textwrap.dedent('''\
            import importlib.abc
            import json
            import sys
            class WithoutOptionalSDK(importlib.abc.MetaPathFinder):
                def find_spec(self,fullname,path=None,target=None):
                    if fullname.split(".")[0] in {"mcp","jsonschema"}:
                        raise ModuleNotFoundError("Optional SDK intentionally absent in wheel check")
            sys.meta_path.insert(0,WithoutOptionalSDK())
            from termatelier.commands import CommandSession
            from termatelier.mcp_client import sdk_available
            assert not sdk_available()
            session = CommandSession()
            assert "existing MCP" in session.execute("help mcp").text
            assert json.loads(session.execute("mcp list --json").text) == []
            session.execute("mcp add wheel-check --url http://127.0.0.1:65530/mcp --timeout 1")
            session.execute("ai map background-remove wheel-check remove_background --input-key image --input-mode base64")
            servers = json.loads(session.execute("mcp list --json").text)
            mapping = json.loads(session.execute("ai mappings --json").text)
            assert len(servers) == 1 and servers[0]["name"] == "wheel-check"
            assert mapping["background-remove"]["tool"] == "remove_background"
            session.execute("mcp remove wheel-check")
            assert json.loads(session.execute("mcp list --json").text) == []
            print("Installed MCP help and registry work without optional SDK or network access.")
            ''')])
    print(f"Installed {version} wheel passed: entry point, 1,248 art tools, native painting/geometry/tone/filter commands, editable paths/channels/text/effects, exact .tart v2/ORA and native/scaled image exports, low-memory profile and SDK-optional MCP settings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", type=Path, default=Path("dist"))
    run_check(parser.parse_args().dist)
