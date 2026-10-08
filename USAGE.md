# SPARKER iCLI

A minimalist terminal image editor with a full-screen command workspace and
**1,248 executable art tools**.
Draw with the mouse, type commands inside the editor, work in a plain CLI, or
run repeatable scripts. Every mode uses the same RGBA document, layers,
selections, file format and undo history.

The editor uses a flat charcoal-and-white layout with an orange accent from
the supplied logo. Single-line menus and controls leave more room for the
canvas. History and the coordinate ruler are hidden until requested; F4 hides
the side panels for a clear canvas view.

Large canvases use local stroke updates and cached viewport rendering. Zoomed-out
views average pixel coverage so thin brush strokes remain continuous at Fit.
The first fitted frame prepares the image; later strokes update only their local
area. CLI
information commands reuse their preview image. Full-resolution pixels and undo
state are preserved; measured improvements and a repeatable benchmark are in
[PERFORMANCE.md](PERFORMANCE.md).

## Start on Windows

Requires Python 3.11+ and a true-color Unicode terminal. Use Windows Terminal
with a monospaced font and at least 90 columns × 30 rows (120 × 40 preferred).

Run **run.cmd**, or from a terminal in this folder:

```powershell
.\run.ps1
```

Both launchers show the exact supplied logo in a centered **borderless loading
window**. It remains visible while the environment and dependencies are prepared,
then closes when the editor's first frame is ready, with a **2.5-second minimum**
so quick launches still show the logo. Setup time counts toward that minimum.
The window is draggable and displays the current startup stage with an orange
activity pulse. It also closes when startup fails or its launcher exits.

The first launch installs dependencies into this folder's `.venv` and needs
internet access. Later launches work offline, and the launcher repairs moved or
incomplete installations. Errors remain visible in the terminal. The logo window
requires Python's standard Tk support; without it, the terminal still starts.
Use `--no-splash` to disable the window when desired.

A separate **transparent, borderless debug overlay** sits at desktop position
0,0 and remains available while the program runs. It shows Python and `.venv`
details, program version and paths, dependencies, memory, terminal dimensions,
document/layer/history state and current tools/view. Its background is truly
transparent on Windows and mouse clicks pass through it. `--no-debug` starts
with it hidden; `debug on` in the command console can show it again. Both native
windows close when the launcher exits. Direct Python launches support diagnostics
commands, but use the Windows launchers for the desktop overlay.

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

Direct Python launches open the editor without a separate launcher window.
Installed console commands are `sparker` and `sparkericli`. The old `termatelier`
command and internal package are retained for compatibility.

## Command workflows

**Inside the editor:** press **F3** or `:` for the full-screen CLI. A separate
painting preview opens automatically and follows commands, layers and undo/redo.
The output fills the terminal; the prompt stays at the bottom. Type `help` or
`help COMMAND`. Up/Down recall commands; Tab or Ctrl+Space completes command and
tool names. PageUp/PageDown scroll output; Ctrl+L clears it. F3 / Escape returns
to painting with your edits preserved. In CLI, F5 reopens a closed preview.

**Interactive CLI:** `run.cmd --cli` or `./run.ps1 --cli` opens the same full-screen
workspace and pop-out viewer. Type commands, `help`, and `quit`; unsaved work is
guarded before exiting. `--repl` keeps a plain `sparker>` prompt for scripts and
simple terminals. Piped `--cli` input automatically uses that plain mode and
does not open a preview.

The preview is a resizable, movable viewer with checkerboard transparency.
It opens without taking keyboard focus from the Windows terminal. Click it to
use F for fit, 1 for actual pixels, wheel zoom, or drag pan. It closes when you
leave CLI or quit. It never resizes the document or writes permanent exports.
`config set preview.enabled false` turns it off; set true and press F5 to restore
it. `preview.width`, `preview.height`, `preview.topmost`, `preview.resampling`,
and `preview.refresh_ms` customize its display. The window needs standard Tk
support; CLI editing remains usable if the viewer cannot open.

## File explorer

Press **F6** in the painter or full-screen CLI to open the file explorer.
The File menu also has dedicated actions; **Ctrl+O**, **Ctrl+E**, Save As,
Import and the first **Ctrl+S** use the same browser.

Browse folders with the mouse or keyboard, type a folder address or filename,
search/filter the file list, sort by name/size/date, show hidden entries and
create a new folder. Places provide quick access to Home, Pictures, the project,
the external export folder and available drives. Selected images and native
projects have a display preview; text files show a bounded excerpt with metadata.
Selection and preview do not run scripts or alter painting pixels.
Ctrl+L focuses the address, Ctrl+F searches, Alt+Up goes to the parent,
Alt+Left/Right navigate history, F5 refreshes and Escape cancels.

The operation menu covers **open**, **import as layer**, **save editable project**,
**export image/text**, **run script**, **export debug report**, **font selection**
and **choose export folder**. The text dialog also has a Browse button beside its
font field. Export offers format, text-column and explicit lossy-format options.
PNG/TIFF/WebP keep original dimensions and RGBA pixels; exports stay outside the
application project. Existing files and unsaved replacement documents retain
confirmation dialogs. A normal Ctrl+S updates the current native project directly.

In the full-screen CLI:

```text
files
files open
files import
files save
files export
files script
files debug
files font
files directory
```

`files` is an interactive UI action. Plain `--repl` and batch jobs continue
using explicit `open`, `save`, `import`, `export`, `script` and `debug export`
paths for automation.

## Art tool library

Press **F5 in the painter**, or choose **Tools → Browse art tool library**.
Search by motif/material, filter category/type, and inspect a visual preview.
Use Tool selects it for mouse drawing; Apply Now works at the canvas center or
inside the current selection. The top Size field and Tools → Library Options
control size, rotation, density, texture seed and effect strength. The top
Alpha% field controls painting opacity.

The catalog has **600 botanical/floral/geometric stamps, 200 natural-media
brushes, 400 textile/tessellation patterns and 48 pixel effects**. Its 1,200
drawable recipes combine different silhouettes, arrangements and internal
construction, and have distinct rendered footprints. Tool IDs are stable and
searchable. These are procedural art recipes and image operations; selecting a
tool, changing its colors or size does not add to the catalog count.

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

Brushes interpolate between stroke points; stamps mark individual points;
patterns tile a dragged box or the whole canvas; effects process the active layer
or a specified box. Every pixel operation respects selection, layer locks and
opacity, and shares undo/redo with the classic tools. Native projects preserve
the chosen library tool and its options. Read **TOOLS.md** for every registered
tool, and **COMMANDS.md** for syntax. `TOOLS.json` provides the catalog as data.

**Batch work:** use repeatable `-c` / `--command`, or a UTF-8 `--script` file.
Commands and scripts execute in the order given. Command mode is headless by
default; add `--ui` to inspect the resulting document in the editor.

```powershell
.\run.ps1 --new 128x96 -c "rectangle 8 8 90 60 --filled --color orange" --export art.png
.\run.ps1 --script examples\poster.sparker --ui
.\run.ps1 --script examples\library-study.sparker --ui
.\run.ps1 study.tart -c "filter grayscale" --export gray.png
```

Read **COMMANDS.md** for the full reference, examples, Windows path quoting,
script rollback and file replacement behavior. Commands cover drawing, shapes,
text, fills, gradients, colors, layers, masks, selections, transforms, filters,
clipboard, palette, guides, metadata, history and files. These are editor
commands; they do not execute shell commands or Python code.

## Mouse controls and menus

| Action | Control |
| --- | --- |
| Paint, shape, select, move | Left mouse drag |
| Pan / zoom around pointer | Right or middle drag / mouse wheel |
| Cancel gesture or dialog | Escape |
| Brush / pencil / eraser | B / P / E |
| Fill / gradient / text | F / G / T |
| Line / rectangle / ellipse | L / R / O |
| Rectangle selection / wand | S / W |
| Picker / move / hand | I / M / H |
| Brush size / swap colors | [ and ] / X |
| Fit / actual pixels / zoom | 0 / 1 / + and − |
| Save / export / open / new | Ctrl+S / Ctrl+E / Ctrl+O / Ctrl+N |
| Undo / redo | Ctrl+Z / Ctrl+Y |
| Copy / cut / paste as layer | Ctrl+C / Ctrl+X / Ctrl+V |
| Select all / deselect / clear | Ctrl+A / Ctrl+D / Delete |
| File menu / full-screen CLI / panels / tools / explorer | F2 / F3 or : / F4 / F5 / F6 |
| Help / quit | F1 / Ctrl+Q |

Top color, size and opacity fields apply on Enter. Menus, tools, layers,
palettes and history support mouse clicks. Tab and arrows navigate controls.
Shortcuts apply while the canvas has focus; click it to return to drawing.
View → History restores clickable undo; View → Coordinate ruler and Palette
toggle optional controls.

The embedded console also accepts `view fit`, `view actual`, `view zoom 200`,
`view pan 0 0`, `view grid`, `view guides`, `view panels`, `view history`,
`view ruler`, `view palette`, and `view resampling bicubic`. These affect the
terminal viewport only; smoothing never changes stored pixels.

Brush sizes are measured in original image pixels. Thin strokes become lighter
when zoomed out as their coverage is averaged, and stay visible along the path.
Press **1** to inspect individual pixels. Mouse motion joins successive points
into one stroke, including sparse or repeated held-button reports; releasing a
different button leaves that stroke captured. Each completed stroke takes one
undo step.

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
