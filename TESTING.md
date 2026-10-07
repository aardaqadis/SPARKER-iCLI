# SPARKER iCLI 0.5.2 verification

Verified on **7 October 2026** with Windows, Python **3.14.6**, Textual
**8.2.8**, Pillow **12.3.0**, pytest **9.1.1**, and pytest-asyncio **1.4.0**.

**582 tests passed, 1 skipped** across model/storage, CLI commands, preferences,
terminal UI, file explorer, tool library, detached preview and startup lifecycle tests. The one skipped test
requires directory symlink creation, unavailable under this Windows account.
The complete final suite passed after all code changes in 154.55 seconds.

Coverage includes pixel alpha and blend modes, selections, painting, masks,
filters, transforms, clipboard, layer management, undo/redo, bounded history,
transaction rollback, native-file round trips, every export type, malformed
files, Windows command quoting, script rollback/nesting, failure line numbers,
REPL recovery, CLI exit codes, mouse events, dialogs and first-frame readiness.
The full-screen command workspace tests verify that typed edits update the mouse document,
share undo/redo, synchronize tool settings, recall/complete commands, toggle
minimal panels and protect unsaved document replacement.

## Large-canvas regression checks

New checks compare local brush, pencil, eraser, shape and library updates with
the earlier whole-image algorithms, including soft edges, overlapping strokes,
selection alpha, clipped bounds, changing tool options and shrinking previews.
All blend modes and masked layers are exercised in exact viewport sampling.
Zoomed-out views preserve thin-stroke coverage using area averages. Cached
minification, fractional pan, changing zoom factors, odd image sizes and local
updates are compared with a complete reference image. Smooth views reuse their
composite during pan/zoom and update only changed regions; read-only CLI commands avoid rebuilding painting pixels and preview
frames. Flood regions preserve four-way connectivity, tolerance and selection
barriers. Axis gradients preserve the earlier channel values exactly.

Actual Textual mouse/keyboard workflows passed at 1024×768 and 2048×2048:
selected soft strokes, final mouse-up endpoints, immutable undo snapshots,
undo/redo, right-drag pan, zoom/fit, cancellation and file-explorer PNG export.
Exports kept exact dimensions and RGBA bytes. A real root PowerShell terminal
launch at 1024×768 displayed the editor, accepted navigation and exited with
Ctrl+Q, code 0. Operation timings and a repeatable raster benchmark are in
[PERFORMANCE.md](PERFORMANCE.md).

New drag checks replay real SGR terminal reports through Textual's parser and
driver, covering brush/pencil/eraser strokes on default and fitted large canvases.
Sparse movement produces continuous native strokes, hover never paints, and
right-drag only pans. Repeated down reports extend the live path within one undo
transaction; a different button's release leaves the initiating stroke captured.
Canvas text selection is disabled so terminal text selection cannot interfere
with painting. Preview tests verify 1/3-pixel stroke visibility from 2.5% to 50%
zoom and exact full-size PNG exports after painting.

## File explorer

The shared terminal browser is tested at 90×30 and 120×40, with navigation,
back/forward history, typed addresses, places, sorting, type/name filters, hidden
files, inaccessible folders and explicit safe folder creation. Browser previews
are bounded, literal and read-only; image thumbnails preserve aspect ratio and
fit the available pane. Merely browsing does not create missing export folders.

All eight operation-hub paths run through actual painter callbacks: open,
import, native save, export, scripts, diagnostics, fonts and export-folder
selection. Tests preserve layer masks and selections in saved projects, verify
import/font undo, pixel-exact 31×19 RGBA PNG and text exports, reject project
destinations, and exercise cancelled/confirmed overwrites and unsaved replacement.
Script failures restore the in-memory painting. Nested font browsing preserves
the text draft on cancel and renders actual chosen fonts when submitted.

Full-screen CLI tests cover the same explorer operations, shared preview and
document state, quoted filenames, persistent export-folder changes, modal
shortcut isolation, strict/explicitly lossy formats, and plain-mode recovery.
Additional regressions verify that hidden export options do not block other
operations and that filename/format synchronization preserves TIFF/JPEG aliases,
capitalization and the latest edits through queued UI events.

A real root `run.ps1 --cli` pseudoterminal session opened the explorer with
`files`, rendered folder navigation and file selection, returned with Escape,
and exited with code 0. Its own preview processes and temporary directory were
gone afterward. The packaged wheel also passed an installed-module F6 explorer
workflow that exported an exact 23×17 PNG to an external folder.

## Art library and command workspace

- All **1,200 drawable recipes** produce nonempty, distinct 72×72 footprints.
  Every drawable tool executes and undoes its pixels. The **48 image effects**
  execute with distinct fixture outputs and preserve original dimensions and
  alpha. The count is 1,248 procedural art recipes and operations, excluding
  aliases, colors, sizes, angles and strength settings.
- Tests cover seeded texture, stroke interpolation, tiling, pattern bounds,
  rotations, selections, opacity, layer locks, invalid options and large-canvas
  pattern application. Command search, pagination, JSON inspection, library
  options, atomic script rollback and native-file tool settings are verified.
- Mouse UI checks exercise browser search and visual thumbnails, stamp drawing,
  brush cancellation, dragged patterns, effect application and undo. The painter
  and full-screen CLI retain one document/history across transitions.
- CLI checks cover output/prompt sizing, command recall, completion for classic
  and catalog IDs, help, shared save/undo/redo shortcuts, replacement guards,
  history, preview reopen and synchronization of retained nonatomic script edits.
  Piped `--cli` and explicit `--repl` keep the plain prompt without a native viewer.
- The generated TOOLS.md and TOOLS.json were checked against all registry IDs,
  metadata records, category totals and type totals.

## Detached painting preview

Native tests verified visible, resizable framed windows, full-resolution RGBA
snapshots, live updates, replacement documents, undo, size/topmost changes,
reopen, normal shutdown and cleanup after parent-process disappearance. Temporary
frames live outside the project and only the latest three generations are kept.
Display checkerboard, smoothing and zoom never change document pixels.

A real Windows pseudoterminal launch through root `run.ps1 --cli` opened the
full-screen workspace and native viewer. A stamp command changed the published
80×64 painting. A later clean 31×19 launch verified a visible 480×320 preview,
no initial activation, a released creation-time focus guard and normal focusable
window style. Both sessions exited with code 0; their own helper processes and
temporary preview directories were gone afterward.

New coverage verifies validated/persisted preferences and session overrides,
live debug toggles, diagnostics without unrelated environment data, history and
brush settings, configurable zoom bounds and interpolation, and editor telemetry
while modal dialogs are open. Preferences and exports are isolated in temporary
folders for automated tests.

PNG, TIFF and WebP round trips preserve exact RGBA bytes at odd 31×19 dimensions,
including invisible RGB in fully transparent pixels. View zoom, pan, grid,
selection outlines and smoothing never enter exported image data. Export tests
cover lazy external folders, relative filenames, absolute destinations, strict
lossy-format opt-in and rejected project paths. Invalid export overrides leave
the editor usable.

## Startup and launchers

- The exact supplied logo is copied unchanged into package assets.
- Native Tk tests verified borderless windows, actual Windows color-key
  transparency, alpha settings, desktop origin (0,0), and click-through / no-focus
  styles. The logo remains until readiness and its minimum duration; the debug
  overlay persists afterward, responds to live off/on changes including an
  initial `--no-debug`, and closes with the launcher.
- A real Windows pseudoterminal launch through the root `run.ps1` rendered the
  editor, reached startup phase `ready`, published 31×19 document telemetry with
  debug enabled, kept its own helper alive during editing, and exited cleanly
  with Ctrl+Q (exit code 0). That helper and both temporary state files were gone
  afterward.
- Both launchers passed CLI/help/error checks. PowerShell command forwarding
  was tested with quoted text and spaced paths. Cached launches skipped pip.
- The legacy `.tart` format remains compatible with prior project files.
- Default loading time is at least 2.5 seconds; setup time counts toward it.
  These checks do not claim a fixed startup duration on other machines.
- Root CMD REPL and PowerShell batch checks exercised debug/settings commands,
  external PNG/JSON exports, original-image lossless WebP, rejected project
  export paths and strict JPEG refusal. The user's attached source image kept
  exactly the same dimensions and RGBA pixels after import/export.

## Build and examples

The package builds as `sparker_icli-0.5.2-py3-none-any.whl`, including the logo
asset and public `sparkericli` namespace. `poster.sparker` ran through the real
CLI in the prior release; its ordinary relative image/text outputs now resolve
to the external export folder. The new `library-study.sparker` ran through the
public CLI and produced a six-layer native project, an exact 320×200 composite
PNG, and an external text representation. Its native project is included.
Studio/menu/CLI/tool-browser/file-explorer
previews are actual Textual screen renders converted from SVG for viewing.
The built wheel was installed into an isolated temporary location and its actual
packaged modules passed public-entry-point, saved-preference, all-tool count and
external 31×19 PNG / 67×43 library-tool WebP smoke tests using the verified
dependency libraries. Root CMD/piped-CLI and PowerShell batch checks passed again
for this release.
The isolated installed wheel also passed a 1024×768 soft stroke, undo/redo,
native save and exact full-size PNG export. A real SGR drag in its packaged
Canvas preserved continuous 1-pixel brush coverage at Fit and one undo step.

The repository-root `run.cmd` and `run.ps1` forward into the `Termatelier/`
Python project; the application and public commands use SPARKER iCLI.

Linux/macOS, Python 3.11 and physical mouse input in every terminal were not
tested here. Headless tests exercise terminal mouse events; the native terminal
smoke test checks startup, rendering and clean exit. Color accuracy on different
monitors is not claimed. Large-canvas performance
measurements and their practical scope are recorded in [PERFORMANCE.md](PERFORMANCE.md).

## Repeat

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip wheel --no-deps --wheel-dir dist .
```
