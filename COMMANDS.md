# SPARKER iCLI command workspace

Use the same editing language from the editor's full-screen CLI, an interactive
terminal prompt, or a batch job. Commands edit the same real document and undo
history used by mouse tools. A command is an editing instruction, never a shell
command: there is no shell execution, Python evaluation, variable expansion or
command substitution.

## Start a workspace

```powershell
.\run.ps1 --cli
.\run.ps1 --new 128x96 --cli
.\run.ps1 picture.png --cli
.\run.ps1 project.tart --cli
```

The Windows command launcher accepts the same options:

```bat
run.cmd --cli
run.cmd --new 128x96 -c "fill 0 0 --color orange" --export painting.png
run.cmd --script artwork.sparker
```

With no command options, the mouse editor opens. Press **F3** or **:** inside the
editor to open its full-screen CLI and separate live painting preview. With
`--command` / `-c` or `--script`, the default
is headless execution; add `--ui` to open the mouse editor afterward, or `--cli`
to continue in the full-screen workspace. `--command` and `--script` are repeatable
and execute in the order written. The original `--new`, `--demo`, `--export`,
`--save-project`, `--inspect` and `--columns` options remain available. New options
are `--set NAME=VALUE` (repeatable), `--debug`, `--no-debug` and `--allow-lossy`. Output
flags execute after all editing commands.

Interactive `--cli` uses the full terminal with an output log and bottom prompt.
It opens a resizable painting preview automatically; commands and undo/redo
update that window. F3/Escape returns to the painter when entered from Studio,
or asks to exit a standalone workspace. Unsaved paintings are guarded before
exit/replacement. F5 or `preview` reopens the viewer, and `help preview` explains
its controls. The viewer supports F fit, 1 actual size, wheel zoom and drag pan.
It closes on CLI exit and opens without stealing terminal focus on Windows.

Use `--repl` for a plain `sparker>` prompt; piped `--cli` also uses plain mode.
These modes do not open the viewer. Type `help` for the complete reference,
`help layer` for one command, or `quit` to leave. Ctrl+D (or Ctrl+Z followed by Enter on
Windows) sends end-of-input. Ctrl+C exits with status 130. A bad interactive
command prints an error and leaves the prompt available. Batch failures stop
execution and return status **2**; successful jobs return **0**.

Full-screen CLI keys: Up/Down recall (including an unfinished draft), Tab or
Ctrl+Space complete, PageUp/PageDown scroll, Ctrl+L clears output, F1 help, F2
tool categories, F4 history, F5 painting preview, F6 file explorer, Ctrl+Z/Y
document undo/redo. Ctrl+N prepares a new command; Ctrl+O/E open the file browser
for opening/exporting. Ctrl+S saves an existing project or opens the browser for
the first save. Clipboard shortcuts edit the command text.

## File explorer (interactive painter / full-screen CLI)

```text
files [open|import|save|export|script|debug|font|directory] [START_PATH]
```

F6 or `files` opens the operation hub. Choose an operation and a file/folder;
dedicated forms use the same browser. `files save` is Save As, even when the
painting already has a project path. `files directory` chooses the persistent
external export folder. The painter File menu also provides script, diagnostics
and export-folder actions. Font selection fills the text dialog in the painter
or prepares an editable `text` command in CLI; it does not paint immediately.

Use folder navigation/history, places/drives, typed location, file search,
type filters, sorting, hidden-file toggle, refresh and New Folder. Highlighting
a file shows its metadata and a bounded read-only preview. Selecting a folder
navigates into it; directory mode selects a folder. Cancel/Escape returns without
file writes or painting edits. The filename field accepts absolute paths or
names relative to the chosen folder.

Explorer keys: Ctrl+L focuses the folder address, Ctrl+F focuses file search,
Alt+Up opens the parent, Alt+Left/Right move through folder history, F5 refreshes,
Ctrl+Shift+N creates a folder, Enter submits a filename, and Escape cancels.

Export mode includes format, TXT/ANSI columns (1–500), and explicit lossy allowance.
PNG/TIFF/WebP preserve full original RGBA pixels. Project-folder exports are
rejected inside the explorer, including links that resolve into the project.
An initial missing export folder is created by the export operation when needed;
merely browsing does not create it. New Folder is an explicit creation action.
Native saves require an existing parent folder and `.tart` extension.

Explorer-triggered writes confirm before replacing an existing different project
or any existing export/report. Open/script retain unsaved-document guards; imports
share normal layer undo. Scripts execute only after submission, use the existing
atomic in-memory rollback and can write files that rollback cannot undo.

The browser is available only in the painter/full-screen workspace. Piped CLI,
plain `--repl` and batch jobs use the ordinary explicit-path commands below.

## Art tools: 1,248 executable catalog entries

```text
tools list [QUERY] [--category NAME] [--kind stamp|brush|pattern|effect] [--page N] [--limit N] [--json]
tools search QUERY [--category NAME] [--kind NAME] [--page N] [--limit N] [--json]
tools categories [--json]
tools count [--json]
tools info TOOL_ID [--json]
tools use TOOL_ID
tools apply TOOL_ID [X,Y ...] [OPTIONS]
tool TOOL_ID
apply TOOL_ID [X,Y ...] [OPTIONS]
tool-options [--size N] [--angle N] [--seed N] [--density N] [--amount N]
```

The library contains 600 botanical/floral/geometric stamps, 200 natural-media
brushes, 400 textile/tessellation patterns and 48 effects. Drawable recipes have
different shapes, layouts and internal details; color/size/rotation changes do
not increase the count. All 1,200 drawable footprints are distinct and all
1,248 tools can execute. **TOOLS.md** lists every ID; **TOOLS.json** is the data
reference. F5 in the painter opens the searchable visual browser.

Categories: `botanical rosettes textile geometric tessellation natural-media effects`.
Search matches all words across ID, name and description, for example
`tools search "oak wreath"`. Pagination defaults to 30 entries; limit 1–200.
Selecting a catalog tool with `tool ID` enables its mouse behavior on return to
the painter. Tool settings are saved in `.tart` and participate in undo.

Apply options: `--size 1..512`, `--angle -360..360`, `--seed 0..2147483647`,
`--density 0.1..4`, `--amount 0..4`, `--color COLOR`, `--background COLOR`,
`--opacity FRACTION`, and `--box X0,Y0,X1,Y1`. Choose points or a box in one
command. Stamp points mark individual centers; brushes interpolate dabs between
points; an omitted point places a stamp/brush at the canvas center. A box scales
a motif into those inclusive bounds. Patterns tile the box or complete canvas.
Effects process the active layer inside the box or canvas and keep original
dimensions and alpha. `--amount` blends effect output (0–1) or intensifies it
(1–4). Selection and opacity further constrain every operation. One application
is one undoable edit; locked layers reject pixel changes.

```text
tools search "oak wreath"
tool botanical.oak.wreath.net
tool-options --size 48 --angle 20 --seed 7
apply botanical.oak.wreath.net 64,48 --color orange
apply natural-media.sponge.centered.coarse 10,20 110,70 --size 24 --opacity 60%
apply textile.braid.woven.beaded --box 8,8,119,87 --size 24
apply effects.halftone --amount 0.8
undo
```

## Grammar and coordinates

- Write **one command per line**. Commands and options are case-sensitive except
  the leading command name. There are no semicolon command chains.
- Quote paths, layer names and text that contain spaces. Both single and double
  quotes work. Windows backslashes are preserved, including inside quotes:
  `open "C:\Art\My Art\picture.png"`. To include a quote in text, use the
  other kind as its outer quote. Backslash escapes are not interpreted.
- `#RRGGBB`, `#RRGGBBAA` and ordinary color names work. Color alpha and tool
  `--opacity` multiply to determine paint transparency.
- Positions use image pixels with **0,0 at the top left**. Shape endpoints are
  inclusive. Stroke/lasso points use `X,Y`; most other commands take separate
  `X Y` coordinates. Drawing outside the canvas is clipped; picking, flood fill
  and wand seeds must be inside it.
- Fractions such as opacity/hardness accept `0.5` or `50%`; plain `50` is not
  a fraction. Brush width is 1–128 pixels. Font size is 1–512 pixels.
- Layer and palette indices begin at **1**. Layers are indexed **bottom first**.
  Layer names may also be used for selection when unique. Quote a numeric layer
  name and it is still treated as an index; use the index for such names.
- Input and native-save relative paths resolve from the working folder; inside
  a script they resolve from that script's folder. Ordinary relative export
  filenames use the configured external export folder. Explicit `./` or `../`
  exports retain their anchored location, but locations inside the application
  workspace are rejected. `~` is expanded. Export paths also expand `%NAME%` on
  Windows; editing commands do not run shell substitutions.
- Put `--` before literal arguments beginning with `--`. Full-line `#` and `//`
  comments are ignored. Inline comments are not recognized, so hex colors work.

## Files and document information

```text
new WIDTHxHEIGHT [--transparent | --color COLOR]
open PATH
save [PATH.tart]
export [PATH] [--columns N] [--allow-lossy]
import PATH [--name NAME] [--x N] [--y N]
info [--json]
meta [KEY VALUE]
```

`new` normally creates a solid background and a transparent Paint layer.
`--transparent` creates one transparent Paint layer. `open` replaces the current
document. In the interactive prompt, save your work before replacing it.
`save` preserves editable state in a `.tart` project and uses the last native
project path if omitted. Opening a normal image does not select it as a project
save path. Import adds an image as a layer, clipping it to the current canvas.

Export supports PNG, JPEG, lossless WebP, BMP, GIF, TIFF, ASCII `.txt` and true
color half-block `.ansi`. An omitted path uses `Untitled.png`; ordinary relative
names use `Pictures\SPARKER-iCLI\Exports`, customizable with `export.directory`.
Absolute export destinations must stay outside the application project/workspace.
PNG/TIFF/lossless WebP preserve exact flattened RGBA pixels at the original
canvas dimensions. JPEG/GIF/BMP require `--allow-lossy` by default. Every image
format preserves dimensions; zoom/pan and preview smoothing never affect exports.
`--columns` is 1–500 and applies only to approximate text exports.
JPEG/BMP and text exports place transparency over white. `info --json` describes
layers, selection bounds, settings and metadata. `meta title "New title"` sets
project metadata; use `guides` and `grid` for their structured fields.

## Debug, preferences and runtime variables

```text
config list [PREFIX] [--json]
config get NAME
config set NAME VALUE
config reset [NAME|all]
config path
debug info [--json]
debug on|off|toggle
debug settings [--json]
debug set NAME VALUE
debug export [PATH.json]
```

`debug` alone prints runtime information: version, interpreter, virtual environment
and `pyvenv.cfg`, program paths, dependency versions, memory, terminal state,
canvas/layers and undo history. JSON includes the complete structured data. The
Windows launchers display live information at the desktop's top-left on a clear,
click-through background; it persists after the loading logo closes. Debug export
also uses the external export folder and rejects project destinations.

`debug set font_size 12` is shorthand for `config set debug.font_size 12`.
Settings persist in `%LOCALAPPDATA%\SPARKER-iCLI\settings.json`; they do not become
project variables. Type `config list` for descriptions. Available settings:

| Variable | Default | Allowed values |
| --- | --- | --- |
| `debug.enabled` / `debug.topmost` | true / true | true or false |
| `debug.detail` | full | full, compact |
| `debug.refresh_ms` | 500 | 100–10000 milliseconds |
| `debug.font_size` | 11 | 7–32 points |
| `debug.opacity` | 1 | 0.1–1; background remains transparent |
| `debug.color` | #d0d0d0 | #RRGGBB |
| `startup.minimum_seconds` | 2.5 | 0–30 seconds |
| `export.directory` | Pictures/SPARKER-iCLI/Exports | Absolute folder outside the project |
| `export.lossless` | true | true or false |
| `view.resampling` | nearest | nearest, bilinear, bicubic |
| `view.zoom_min` / `view.zoom_max` | 0.025 / 32 | 0.005–1 / 1–128 |
| `history.max_steps` / `history.max_mb` | 40 / 96 | 1–200 checkpoints / 8–512 MiB |
| `brush.size` / `brush.hardness` / `brush.opacity` | 3 / 0.8 / 1 | 1–128 / 0–1 / 0–1 |
| `fill.tolerance` | 20 | 0–255 |
| `library.size` / `library.angle` / `library.seed` | 32 / 0 / 0 | 1–512 pixels / -360–360 degrees / 0–2147483647 |
| `library.density` / `library.amount` | 1 / 1 | 0.1–4 / 0–4 |
| `preview.enabled` / `preview.topmost` | true / false | true or false |
| `preview.width` / `preview.height` | 800 / 600 | 240–3840 / 180–2160 pixels |
| `preview.resampling` | bilinear | nearest, bilinear, bicubic |
| `preview.refresh_ms` | 150 | 50–2000 milliseconds |

Brush and history preference changes affect the current session immediately.
`brush` edits project tool state; `config set brush.size` changes the default and
the active brush. Saved project tool state takes precedence when opening a project.
Preview preferences do not alter image pixels. `config reset` restores defaults.

```powershell
.\run.ps1 --set startup.minimum_seconds=3 --set debug.detail=compact
.\run.ps1 --no-debug --cli
.\run.ps1 --new 31x19 --export sample.png
```

Launch `--set` values apply to one session without replacing saved preferences;
`debug on` can show the overlay after a `--no-debug` launch. For temporary batch
work, combine `--no-splash --no-debug`. Arbitrary variable names, Python code and
invalid ranges are rejected. `SPARKER_CONFIG_FILE` can isolate a profile for tests;
`SPARKER_EXPORT_DIR` is an optional export-directory environment override.

## Tools and painting

```text
tool NAME
color COLOR [BACKGROUND]
color foreground|background COLOR
color swap
brush [--size N] [--hardness N] [--opacity N]
stroke X,Y [X,Y ...] [--color COLOR] [--size N] [--hardness N] [--opacity N]
pencil X,Y [X,Y ...] [--color COLOR] [--size N] [--opacity N]
erase X,Y [X,Y ...] [--size N] [--hardness N] [--opacity N]
fill X Y [--color COLOR] [--tolerance N] [--opacity N] [--merged]
gradient X0 Y0 X1 Y1 [--from COLOR] [--to COLOR] [--opacity N] [--radial]
line X0 Y0 X1 Y1 [--color COLOR] [--width N] [--opacity N]
rectangle X0 Y0 X1 Y1 [--filled] [--color COLOR] [--width N] [--opacity N]
ellipse X0 Y0 X1 Y1 [--filled] [--color COLOR] [--width N] [--opacity N]
text X Y "WORDS" [--color COLOR] [--size N] [--font PATH] [--opacity N]
pick X Y [--active]
clear
```

`rect` aliases `rectangle`; `eraser` aliases `erase`. Colors, brush size,
hardness, opacity and selected tool persist in native projects. Stroke defaults
come from the brush configuration; pencil defaults to a one-pixel hard edge.
Per-command options temporarily override defaults. `tool` selects a mouse tool;
coordinate drawing commands execute immediately regardless of selected tool.

`fill` and `select wand` use contiguous regions and tolerance 0–255; `--merged`
samples the visible composite instead of the active layer. `pick` normally samples
the composite; `--active` samples active-layer pixels. `clear` erases only inside
the current selection, or the whole active layer with no selection. Drawing,
fill, gradient, text, erasing and filters respect selections and pixel locks.

Tool names: `brush pencil eraser fill gradient text line rectangle ellipse
select_rect select_ellipse lasso wand picker move hand`.

## Layers and masks

```text
layer list
layer add [NAME]
layer select INDEX_OR_NAME
layer rename NAME
layer duplicate
layer delete
layer raise
layer lower
layer reorder INDEX
layer merge
layer flatten
layer opacity FRACTION
layer blend MODE
layer show|hide|lock|unlock
layer mask selection|white|black|invert|apply|remove
```

Layers can use `normal multiply screen overlay darken lighten difference add`
blend modes. `merge` combines the active layer with the layer below; both must
be visible and unlocked, and the lower layer must use normal blend mode.
`flatten` replaces all layers with the visible composite; all layers must be
unlocked. At least one layer is retained and the document is limited to 64 layers.

White masks reveal pixels; black masks hide them. `mask selection` copies the
current selection. `mask apply` permanently multiplies the mask into pixel alpha
then removes it; `mask remove` removes the mask without altering pixel data. Mask
edits require an unlocked layer. Moving/transforming a layer also changes its mask.

## Selection and clipboard

```text
select all|none|invert
select rectangle X0 Y0 X1 Y1 [--mode MODE]
select ellipse X0 Y0 X1 Y1 [--mode MODE]
select lasso X,Y X,Y X,Y [X,Y ...] [--mode MODE]
select wand X Y [--mode MODE] [--tolerance N] [--merged]
select feather RADIUS
copy
cut
paste
```

Selection modes are `replace add subtract intersect`. `select rect` aliases
`select rectangle`. `copy` and `cut` copy active-layer pixels; `paste` inserts
them as a new layer at the original position. Clipboard contents are session
state and are not stored in `.tart` files.

## Geometry and effects

```text
move DX DY
resize WIDTHxHEIGHT
canvas WIDTHxHEIGHT
crop
transform flip-h|flip-v|rotate-cw|rotate-ccw
transform rotate DEGREES
transform scale WIDTHxHEIGHT
filter NAME [AMOUNT]
```

`resize` resamples all layers, masks and the selection; `canvas` changes canvas
bounds from the top-left corner. `crop` crops the complete document to selection
bounds. Layer transforms operate on the complete active layer and mask, centered
within the existing canvas; arbitrary rotation/scale can clip content. Positive
arbitrary rotation is counterclockwise. Geometry is not constrained to a selection.

Filters: `invert grayscale sepia blur sharpen edges emboss posterize threshold
brightness contrast saturation autocontrast`. Amount ranges: blur 0–100,
sharpen/brightness/contrast/saturation 0–10, posterize 1–8, threshold 0–255.
Defaults are blur 2, posterize 4, threshold 128 and enhancement factor 1.
The remaining filters take no amount. Filters preserve alpha and operate on
active-layer pixels inside the selection.

## Palette, guides and history

```text
palette list
palette add COLOR
palette remove INDEX
palette set COLOR [COLOR ...]
guides
guides x POSITION [POSITION ...]
guides y POSITION [POSITION ...]
guides clear
grid [SPACING]
undo [COUNT]
redo [COUNT]
history
```

Palettes contain 1–64 colors. Guides accept up to 100 in-canvas pixel positions
per axis. Giving an axis with no positions clears that axis. Grid spacing is
1–4096 pixels. These commands configure drawing aids; the editor's View menu
controls their visibility. History lists undo labels oldest first and redo labels
in the order they will be applied. A requested undo/redo count must be fully
available; otherwise no steps are applied. Editing commands, selection changes
and project settings each create one undo step. Selecting a layer and changing
user preferences with `config` or `debug` do not create document undo steps.

## Scripts and rollback

Create a UTF-8 file such as `artwork.sparker`:

```text
# A small editable composition
new 128x96 --transparent
gradient 0 0 0 95 --from #161616 --to #363636
layer add Accent
rectangle 18 24 109 72 --filled --color #e89332 --width 1
layer add Lettering
text 26 40 "SPARKER" --size 16 --color white
meta title "SPARKER study"
save artwork.tart
export artwork.png
export artwork.ansi --columns 80
```

Run it with `run.cmd --script artwork.sparker`, or execute
`script "C:\My Art\artwork.sparker"` inside a workspace. Nested scripts resolve
paths relative to their own folders and may nest at most eight levels.

By default, the first failed line stops a script, reports its source and line
number, and restores **all in-memory state** from before that script: pixels,
layers, selection, clipboard, settings, active document identity, save path and
undo/redo history. **Files already saved or exported and persisted user preferences
changed by `config` / `debug` remain on disk.** File
operations themselves use atomic writes. Put output commands at the end of a
script to avoid writing results before subsequent edits have succeeded.

Use `--nonatomic` on the launcher, or `script PATH --nonatomic`, to retain
completed commands when a later line fails. Errors still stop execution and are
reported. `quit` is rejected inside scripts and batch `-c` commands; it is an
interactive workspace operation. Separate repeated `-c` commands each commit
independently; their earlier edits are not rolled back if a later `-c` fails.

## Shared command API

The editor, REPL and headless launcher all use:

```python
from termatelier.commands import CommandSession, CommandError

session = CommandSession(document, project_path=None, base_dir=None, config=None)
result = session.execute('pencil 4,5 --color orange')
# result.text, result.changed, result.document_replaced, result.quit_requested
# session.document can be replaced by new/open; session.project_path tracks saves.
```

`execute` raises `CommandError` for a malformed or inapplicable command.
`run_script(text, source="<script>", atomic=True, base_dir=None)` returns a list
of results or raises `ScriptError`, with rollback semantics described above.
`describe()` returns a JSON-compatible state description. Persistent tool
settings use the same `document.settings` keys as the mouse editor.
