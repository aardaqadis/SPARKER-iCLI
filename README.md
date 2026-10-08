# SPARKER iCLI

<img src="src/termatelier/assets/logo.png" alt="SPARKER iCLI" width="420">

Ever had the need to have MS paint in your command prompt? No? Download it anyway!

A terminal paint editor with mouse drawing, layers, selections, brushes, shapes,
filters, undo/redo and **1,248 art tools**. Use the canvas or editing commands.

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
