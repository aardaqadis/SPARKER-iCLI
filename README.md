# SPARKER iCLI

Ever had the need to have MS paint in your command prompt? No? Download it anyway!

SPARKER iCLI is a mouse interactive paint canvas inside the command prompt that allows to create digital pixel art. With over a wide range of tools, resources and commands that you can optionally use, you will certainly have great fun using this.

## Start on Windows

Requires Python 3.11+ and a true-color Unicode terminal. Use Windows Terminal
with a monospaced font and at least 90 columns × 30 rows (120 × 40 preferred).

Run **run.cmd**, or from a terminal in this folder:

```powershell
.\run.ps1
```
Optionally run with arguments:

```powershell
.\run.ps1 --demo
.\run.ps1 --cli
.\run.ps1 --no-splash --help
.\run.ps1 --set startup.minimum_seconds=3 --set debug.detail=compact
```

If PowerShell execution policy restricts scripts, use `run.cmd` or:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m sparkericli
```

## Command workflows

You can access various commands and the command prompt for SiCLI by pressing F3.

## Art tool library

The tool library holds various motifs and can be accessed by Tools > Browse Art Tool Library, or from pressing F5.

Optionally, some tool commands include:

```text
tools count
tools categories
tools search "oak wreath"
tools list --category natural-media --page 2 --limit 30
tools info botanical.oak.wreath.net
tool botanical.oak.wreath.net
tool-options --size 48 --angle 20 --density 1.5 --seed 7
apply botanical.oak.wreath.net 64,48 --color orange
apply textile.braid.woven.beaded --box 8,8,120,88 --size 24
apply effects.bloom --amount 0.7
```

**Batch work:** use repeatable `-c` / `--command`, or a UTF-8 `--script` file.
Commands and scripts execute in the order given. Command mode is headless by
default; add `--ui` to inspect the resulting document in the editor.

```powershell
.\run.ps1 --new 128x96 -c "rectangle 8 8 90 60 --filled --color orange" --export art.png
.\run.ps1 --script examples\poster.sparker --ui
.\run.ps1 --script examples\library-study.sparker --ui
.\run.ps1 study.tart -c "filter grayscale" --export gray.png
```

## Debug and custom variables

Type these in the F3 console or interactive CLI:

```text
debug info
debug info --json
debug off
debug on
debug set font_size 12
debug set detail compact
debug set refresh_ms 750
config list
config set startup.minimum_seconds 3
config set brush.size 8
config set history.max_steps 60
config set view.resampling bicubic
config set export.directory "C:\Art\Exports"
config reset debug.font_size
debug export diagnostics.json
```

Preferences persist in `%LOCALAPPDATA%\SPARKER-iCLI\settings.json`, separate from
project files. `config list` describes every allowed variable and `config path`
shows the settings file. Values are validated; arbitrary code and environment
variables cannot be assigned. Repeatable launch flags `--set NAME=VALUE` override
preferences for one run. `--debug` / `--no-debug` override initial visibility.
The Help menu also opens runtime information and settings in the console.

## Editing features

| Category | Features |
| --- | --- |
| Paint | Soft/hard round brush, pencil, eraser, opacity, line, outlined/filled rectangles and ellipses; 200 textured brushes and 600 stamps |
| Color | Connected fill with RGBA tolerance, linear/radial gradients, picker, palettes |
| Text | Raster text, size, optional TTF/OTF font, multiline input |
| Layers | Add, duplicate, delete, reorder, rename, visibility, lock, opacity, merge, flatten |
| Blending | Normal, multiply, screen, overlay, darken, lighten, difference, add |
| Masks | Selection masks, invert/remove/apply; CLI white/black masks |
| Selection | Rectangle, ellipse, lasso, wand; combine modes; all/none/invert; feather |
| Transforms | Move, flips, scale, rotation, image/canvas resize, crop to selection |
| Effects | Classic filters plus 48 catalog operations: tonal/channel edits, kernels, halftone, pixelate, grain, bloom, glow, oil paint, sketch, Sobel and relief |
| View/history | Checkerboard, grid, guides, zoom/pan, shape previews, gesture undo/redo |
| Patterns | 400 decorative textile and tessellation recipes, repeated at full resolution |
| Commands | Full-screen workspace, pop-out live preview, plain REPL, ordered commands/scripts, catalog search, JSON inspection and error exit status |

Painting, fills, erasing, filters and clipboard operations respect selections.
Transforms affect the whole active layer and its mask, clipping to the canvas.
Copy/cut and paste to transform a selection independently. Canvas resize and
image-as-layer import use a top-left anchor; Open image adopts image size.
Text and shapes are raster pixels rather than semantic objects.

## Projects and exports

Save **`.tart`** projects to retain layer pixels, masks, visibility, locks,
opacity, blend modes, active layer, selection, palette, guides, brush settings
and metadata. Existing Termatelier v1 projects open unchanged. `FORMAT.md`
documents the stable container; its legacy identifier intentionally remains
unchanged. Undo history, clipboard and viewport are session-only.

Export flattened **PNG, JPEG, WebP, BMP, GIF, TIFF, ASCII `.txt`, or true-color
`.ansi`**. Exports default to **`Pictures\SPARKER-iCLI\Exports`** in your user
folder. Ordinary relative filenames, including script outputs, go there; absolute
paths outside the application workspace are also accepted. Export paths inside
the program/project folder are rejected. `config set export.directory PATH`
changes the default. Native `.tart` saves still use the location you choose.

Image exports always keep the **full canvas width and height**, independent of
zoom, pan, grid, selection outlines or text column settings. PNG, TIFF and WebP
preserve the exact flattened RGBA pixels; WebP uses lossless encoding and preserves
even invisible RGB values. Lossless export is enabled by default. JPEG, GIF and
BMP cannot preserve all RGBA data, so they require `--allow-lossy` or the dialog's
explicit allowance. JPEG/BMP and text put transparency over white; GIF has a
limited palette. ASCII/ANSI are approximate text representations. Animated
imports use the first frame and respect EXIF orientation. Writes are atomic.
Explicit CLI saves/exports replace existing files; interactive file dialogs
confirm before replacing a different file.

`examples/twilight.tart` is a layered example. `examples/poster.sparker` is a
repeatable script. `examples/library-study.sparker` builds a six-layer painting
with a textured brush, tiled pattern, botanical stamps and rosette. Its matching
`.tart` file opens directly; the script's PNG/text outputs go to the external
export directory. Script failures roll back the in-memory session unless
`--nonatomic` is requested; files already written cannot be rolled back.

## Install elsewhere, test and build

On Linux/macOS: `python3 -m venv .venv`, then
`.venv/bin/python -m pip install -e .` and
`.venv/bin/python -m sparkericli`. The Windows logo launcher is optional;
the terminal editor and commands are portable Python.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip wheel --no-deps --wheel-dir dist .
```

See `TESTING.md` for verification. The supplied logo is packaged in the wheel.

## Practical limits

Vector paths, editable text objects, tablet pressure, healing/clone tools,
plugins, animation timelines, CMYK/ICC workflows and layered XCF/PSD import are
absent. Masks are selection-based rather than separately brush-editable.
Clipboard is internal; guides do not snap. Filters are destructive and undoable.

Terminal mouse coordinates identify character cells: zoom in for finer pixels.
Painting and navigation avoid full-canvas work where possible; larger documents
still need more memory for layers, undo, filters and exports. Limits: 4096 pixels per side / 4,194,304
total pixels, 64 layers and a conservative 128 MiB document pixel budget.
Combined undo/redo pixel data defaults to a 96 MiB budget and at most 40
checkpoints combined; `history.max_mb` and `history.max_steps` customize these
bounds. Large operations run synchronously and can be slow. `NO_COLOR` makes
the terminal monochrome; exports retain color.

Each terminal cell can show only two color samples. A small or zoomed-out preview
can look blocky even when the underlying image is full resolution. Zoom in or
choose bicubic preview smoothing; exporting does not enlarge or shrink the image.

Built with Textual and Pillow. Your supplied logo is included unchanged.
