# SPARKER iCLI

<img src="src/termatelier/assets/logo.png" alt="SPARKER iCLI" width="420">

Ever had the need to have MS paint in your command prompt? No? Download it anyway!
 
A terminal paint editor with mouse drawing, layers, selections, brushes, shapes,
filters, undo/redo and **1,248 art tools**. Use the canvas or editing commands.

## Start
=======
SPARKER iCLI is a raster canvas editor inside of any command line interface. Consuming less memory than Microsoft paint, it offers an optimised way of digitally producing art.
>>>>>>> 086c2798747f0a50eb21600ba4f25ec6385fa090

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
| Help / quit | F1 / Ctrl+Q |

## Commands and files

Type `help` or `help COMMAND` in the command view. For example:

```text
new 128x96
pencil 10,10 80,60 --color orange
tools search oak
save study.tart
export study.png
debug off
config set memory.mode low
```

Use `config set memory.mode standard` to restore normal memory settings.
`.tart` saves editable layers and metadata. PNG, TIFF and WebP preserve canvas
dimensions and RGBA pixels; ASCII/ANSI exports create text art.
JPEG/GIF/BMP require `--allow-lossy`. Exports go to
`Pictures/SPARKER-iCLI/Exports` or your configured folder, outside the app.

## Development

Inside your Python environment:

```sh
python -m pip install -e ".[test]" build
python -m pytest -q
python -m build
```


Built with Textual and Pillow. [MIT license](LICENSE).
=======
The Windows launchers include a borderless logo loading screen and a configurable
debug overlay. `--no-debug` hides the overlay; `debug off` in the command workspace
turns it off while editing.

## What it does

- Brush, pencil, eraser, fills, gradients, text and geometric shapes; mouse drag,
  zoom and pan with continuous thin-stroke previews at Fit.
- Layers, masks, opacity and blend modes; selections, transforms, filters,
  palettes, guides and a grid, with shared undo/redo.
- A searchable library of 600 stamps, 200 textured brushes, 400 patterns and
  48 image effects. Color, size and angle settings are separate from that count.
- **F3** opens the full-screen CLI and detached live painting preview;
  **F5** opens the tool library; **F6** opens the shared file explorer.
- Editable **.tart** projects preserve layers, masks and metadata. PNG, TIFF and
  WebP exports preserve original dimensions and RGBA pixels. ASCII and ANSI
  exports produce text representations. JPEG/GIF/BMP require explicit lossy opt-in.

Exports default to **Pictures\SPARKER-iCLI\Exports**, outside the application
folder. Display zoom and terminal character resolution do not resize saved images.

## Documentation

- [Complete usage and shortcuts](USAGE.md)
- [Command reference](COMMANDS.md)
- [Art tool catalog](TOOLS.md)
- [Native project format](FORMAT.md)
- [Verification](TESTING.md) and [performance measurements](PERFORMANCE.md)
- [Contributing and local checks](CONTRIBUTING.md)
- [Changes](CHANGELOG.md)

Application code lives in **src/** and tests in **tests/**. The internal
`termatelier` package name and .tart identifier are retained for compatibility;
the public commands are `sparker` and `sparkericli`.

## Install with Python

From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m sparkericli
```

On Linux/macOS, use `python3 -m venv .venv`, then
`.venv/bin/python -m pip install -e .` and
`.venv/bin/python -m sparkericli`. Native windows require Tk; terminal painting
and plain command editing remain usable without it. The Windows logo/debug
integration is specific to the Windows launchers.

## Scope and license

This is a raster editor. Vector paths, editable text objects, tablet pressure,
animation timelines, CMYK/ICC workflows and layered PSD/XCF import are absent.
See the detailed usage guide for document and history limits.

Built with Textual and Pillow. Released under the existing [MIT license](LICENSE).

