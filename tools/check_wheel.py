"""Check the built wheel's isolated entry point, drawing and exact PNG export."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
        assert "SPARKER" in execute(["-m", "sparkericli", "--help"])
        execute(["-m", "sparkericli", "--new", "31x19", "--no-debug", "-c",
                 "pencil 2,3 --color orange", "--export", "native.png"])
        execute(["-c", "from PIL import Image; "
            "from termatelier.model import Document; "
            "d=Document(31,19); d.paint_mask(d.stroke_mask([(2,3)],1,1),'orange'); "
            "p=Image.open('exports/native.png'); "
            "assert p.size==(31,19); assert p.convert('RGBA').tobytes()==d.composite().tobytes()"])
        tools = json.loads(execute(["-m", "sparkericli", "-c", "tools count --json"]))
        assert tools["total"] == 1248, tools
    print(f"Installed {version} wheel passed: module/metadata, help, 1,248 tools, exact 31x19 PNG.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", type=Path, default=Path("dist"))
    run_check(parser.parse_args().dist)
