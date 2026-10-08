# Changes

## Repository preparation — 2026-10-08

- Add a GitHub landing page, complete usage guide, contributor instructions and
  publishing guide while preserving the application and native format.
- Configure Windows/Ubuntu test and package builds for Python 3.11 and 3.14;
  include public repository links and MIT package metadata.
- Keep local diagnostics, editor settings and personal artwork out of source
  tracking, without deleting the local files.
- Make repeated explorer clicks in tests wait for the button's animation to
  finish on faster platforms.

## 0.5.2 — 2026-10-07

- Preserve thin brush coverage in zoomed-out terminal previews using an
  area-averaged display cache, eliminating missing strokes and scattered dots.
- Extend captured strokes when a terminal repeats held-button press reports.
  Ignore unrelated button releases and disable text selection on the canvas.
- Retain exact native pixels, lossless exports and one undo step per stroke.
- Add real SGR mouse-report checks and a Canvas benchmark separating first-frame
  preparation from warm rendering and local drawing.

## 0.5.1 — 2026-10-07

- Update local stroke regions, reuse rendered rows and cache smooth composites
  to improve large-canvas drawing and navigation.
- Reuse detached preview frames for informational commands and use faster
  lossless encoding for live painting updates.
- Preserve original dimensions, RGBA export data, layer blends and undo state.
