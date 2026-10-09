# SPARKER iCLI

<img src="src/termatelier/assets/logo.png" alt="SPARKER iCLI" width="420">

Ever had the need to have MS paint in your command prompt? No? Download it anyway!

Holding around many more features than conventional raster canvas editors, this program has support for Windows, Linux and MacOS* systems, with high quality pixel drawings.
Unusually, this program has mouse support, meaning that your command line interrace transforms into a quicker and an ease-of-use factor.

## Start

Install **Python 3.11+**, then run from this folder:

| System | Command |
| --- | --- |
| Windows | `run.cmd` or `.\run.ps1` |
| Linux/macOS | `sh ./run.sh` |
| Portable Python launcher | `python run.py` or `python3 run.py` |

The first launch downloads dependencies into a local environment.
Full-screen painting needs a Unicode terminal with mouse support.
Limited terminals and pipes use the plain command interface. Desktop previews
are optional and need Tk plus a display. Windows and Linux have been tested;
macOS/BSD support depends on available Python libraries and terminal capabilities.

Add `--cli` for the full-screen command view, `--repl` for plain commands,
`--no-debug` to hide Windows diagnostics, or `--low-memory` to use less RAM.
Low-memory mode disables desktop helpers and limits lossless compressed undo
to 8 checkpoints / 16 MiB. Image quality stays unchanged; compression uses extra CPU.

## Controls

| Action | Control |
| --- | --- |
| Paint | Left mouse drag |
| Pan / zoom | Right or middle drag / wheel |
| Commands / tools / files | F3 / F5 / F6 |
| Save / export / open | Ctrl+S / Ctrl+E / Ctrl+O |
| Undo / redo | Ctrl+Z / Ctrl+Y |
| Copy / cut / paste image in CLI | Alt+C / Alt+X / Alt+V |
| Help / quit | F1 / Ctrl+Q |

## Commands and files

Type `help`, `help COMMAND` or `commands QUERY` in the command view. Tab completes
command names, subcommands and options. For example:

```text
new 128x96
pencil 10,10 80,60 --color orange
copy --merged --box 10,10,81,61
paste --center --flip-h --name Copy
text 64 8 "Hello" --size 18 --anchor top-center --stroke 1 --layer Title
tools search oak
save study.tart
export study.png
debug off
config set memory.mode low
```

Use `config set memory.mode standard` to restore normal memory settings.
`.tart` saves original editable layers and metadata. Standard image exports retain
every original canvas pixel at the exact canvas width and height. Zoom, preview
sampling, grid and guides never affect exported detail. PNG, TIFF and WebP encode
losslessly, including transparency. ASCII/ANSI exports create text art.
Use `export detail.png --scale 4` or the explorer's Scale field for crisp 4×
enlargement; a 960×640 canvas becomes 3840×2560, with every pixel preserved.
Set `config set export.scale 4` to make it the default; set it to `1` to restore
canvas-sized exports. Enlargements are limited to 16,777,216 pixels.
JPEG/GIF/BMP require `--allow-lossy`. Exports go to
`Pictures/SPARKER-iCLI/Exports` or your configured folder, outside the app.
OpenRaster (`.ora`) imports/exports raster layers at canvas size with supported
blend modes; masks/effects are baked into those layers. Use `.tart` to retain
editable paths, channels, text sources and effects. Earlier `.tart` files open.

For external AI tools, install optional support from this folder:
Windows: `.venv\Scripts\python.exe -m pip install ".[mcp]"`;
Linux/macOS: `.venv/bin/python -m pip install ".[mcp]"`.
Open **Tools → AI / MCP connections**
or type `ai settings`: SPARKER prompts for the service URL or launch command,
discovers tools, and lets you map background removal, upscaling and other tasks.
Authentication uses an environment-variable name. Your service supplies the AI;
SPARKER includes no models or accounts. The first unconfigured AI command opens
settings. `help ai` shows inputs, output options and result handling.
`sparker-mcp` starts SPARKER's own stdio MCP server for external editing clients.

## Development

Inside your Python environment:

```sh
python -m pip install -e ".[test]" build
python -m pytest -q
python -m build
```


Built with Textual, Pillow and NumPy. [MIT license](LICENSE).
