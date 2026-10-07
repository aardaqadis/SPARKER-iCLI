# Canvas rendering and performance in SPARKER iCLI 0.5.2

Painting updates the pixels affected by each mouse movement instead of
rebuilding the entire stroke and image. Terminal rows and brush tips are reused
when possible.

Zoomed-out views prepare an exact full-resolution composite once, then average
pixel coverage into a cached smaller image. Thin strokes stay continuous even
at Fit, where point sampling previously hid them or showed isolated squares.
RGBA coverage is averaged before the selected nearest/bilinear/bicubic display
interpolation. This changes the display only. Local strokes update their region
of the composite and the corresponding aligned area-average tiles, including
layer masks, opacity and blend modes. Pan reuses both images; zoom changes reuse
the original composite and rebuild the smaller image when its factor changes.

At actual size or higher, nearest rendering samples the visible layer pixels
into terminal-sized buffers. Bilinear/bicubic rendering reuses the exact
composite for pan and zoom, with local edits patched into it.

Soft brushes include the full blur margin around each updated region. Every
stroke blends against its original pixels, preserving the same opacity through
overlaps. Shapes restore their previous bounds when resized during a drag.
The document, native files and exported images retain their original resolution.
Undo, redo and cancellation preserve the full editable state and existing
history limits.

The detached CLI preview reuses its full-resolution image for informational
commands. Changed paintings use faster lossless PNG encoding for temporary
communication with the viewer. Export compression preferences remain separate.

## Measured results

These are median operation timings on this Windows machine, Python 3.14.6,
Pillow 12.3.0 and Textual 8.2.8. They measure the operations listed, rather than
total terminal frame rate. Canvas content, terminal size, hardware and terminal
output speed affect the result.

Actual Canvas widget in **0.5.2**, 2048×2048 pixels, six normal layers,
100×70 display pixels at 3.4% zoom:

| Operation | Median |
| --- | ---: |
| First fitted frame, composite and coverage preparation | 137 ms |
| Repeated fitted frame, nearest | 4.3 ms |
| Warm nearest pan | 5.9 ms |
| Warm bilinear pan | 3.5 ms |
| Warm bicubic pan | 3.9 ms |
| Soft brush movement, editing and cache updates | 0.60 ms |
| Rendering after that brush movement | 3.6 ms |
| 40 brush movements plus rendered frames | 189 ms total |

The brush measurement uses size 30, hardness 0.5 and opacity 0.5. These timings
exclude terminal output, mouse event dispatch and the initial undo snapshot.

Earlier **0.5.1** optimizations, using the same canvas and viewport sizes:

| Operation | Before | After |
| --- | ---: | ---: |
| Actual-size view rendering | 133 ms | 4.6 ms |
| Soft brush movement, size 30 / hardness 0.5 | 50 ms | 0.21 ms |
| 40 soft brush movements, editing work only | 2,058 ms | 10.4 ms |

Earlier 0.5.1 raster and detached-preview measurements:

| Operation | Before | After |
| --- | ---: | ---: |
| 2048×2048 six-layer mixed-blend viewport composition | 345 ms | 1.4 ms |
| 2048×2048 six-layer bilinear pan, warmed view | 152 ms | 12 ms |
| 2048×2048 six-layer bicubic pan, warmed view | 154 ms | 13 ms |
| 512×512 uniform flood selection | 389 ms | 3.0 ms |
| 512×512 horizontal gradient | 286 ms | 2.8 ms |
| 2048×2048 noisy RGBA CLI preview publication | 869 ms | 162 ms |
| Unchanged CLI preview update | full publication | 0.01 ms |

Regression checks compare exact RGBA bytes for painting, erasing, selections,
layer masks, every blend mode, area-average viewport rendering and native-size
PNG export. Real SGR mouse reports pass through Textual's parser and driver to
the painter. They cover sparse movement, repeated presses, unrelated button
releases, one-step undo, hover and right-drag pan. Thin 1/3-pixel strokes are
checked across zoom levels, odd dimensions and fractional pan offsets.

## Reproduce Canvas measurements

```powershell
.\.venv\Scripts\python.exe tools\benchmark_canvas.py --size 2048 --samples 5
```

This measures the actual Canvas implementation separately for cold Fit, warmed
views and local brush editing plus rendering.

## Reproduce raster measurements

From the project folder after installing dependencies:

```powershell
.\.venv\Scripts\python.exe tools\benchmark_performance.py
```

The script compares the preserved earlier raster algorithms with the current
ones, checks exact pixel equivalence, and prints median timings. A quick run:

```powershell
.\.venv\Scripts\python.exe tools\benchmark_performance.py --sizes 512 --bulk-sizes 256 --samples 3
```

The first zoomed-out frame, smooth view or full-document change may prepare a
full-resolution composite. Subsequent local drawing and view navigation reuse
that cache. Whole-image filters, general angled/radial gradients, native saves and
exports still process the required original pixels and can take longer than
small brush movements. Terminal color display remains limited by character
resolution; that does not change the editable image dimensions.
