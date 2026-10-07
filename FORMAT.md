# SPARKER iCLI native project format (.tart), version 1

SPARKER iCLI preserves the original `org.termatelier.project` identifier and
version so existing editable projects remain compatible.

The extension is `.tart`. A project is a ZIP archive, never an executable or
pickled Python object. Archive members are read in memory, without extracting
paths to disk. Writers replace the destination atomically using a temporary
file in the same directory. The manifest uses UTF-8 JSON.

## Example manifest.json

```json
{
  "format": "org.termatelier.project",
  "version": 1,
  "width": 160,
  "height": 100,
  "active": 1,
  "metadata": {
    "title": "Study",
    "guides_x": [80],
    "guides_y": [50],
    "grid_spacing": 8
  },
  "settings": {
    "palette": ["#151b29", "#ffffff", "#5a8dee"],
    "foreground": "#5a8dee",
    "background": "#ffffff",
    "brush_size": 3,
    "hardness": 0.8,
    "opacity": 1.0,
    "tolerance": 20
  },
  "layers": [
    {
      "name": "Background",
      "image": "layers/0.png",
      "visible": true,
      "locked": false,
      "opacity": 1.0,
      "blend": "normal",
      "mask": null
    },
    {
      "name": "Paint",
      "image": "layers/1.png",
      "visible": true,
      "locked": false,
      "opacity": 0.7,
      "blend": "multiply",
      "mask": "masks/1.png"
    }
  ],
  "selection": "selection.png"
}
```

Layers are ordered **bottom to top**. `active` is a zero-based index. Each layer
image is a canvas-sized PNG, decoded as straight-alpha RGBA. A layer's optional
mask is a canvas-sized grayscale PNG: 0 hides pixels, 255 shows them. Mask and
layer opacity multiply the alpha channel, without destroying stored pixels.
Blend modes operate on RGB in the overlapping region, followed by source-over
alpha compositing. A fully transparent backdrop cannot affect source color.

Supported modes: `normal`, `multiply`, `screen`, `overlay`, `darken`, `lighten`,
`difference`, `add`. Opacity is a finite value in [0,1]. `selection` is null for
no selection, or a grayscale PNG with 0 unselected / 255 fully selected and
intermediate feather coverage. An all-zero mask is an empty selection; it is
different from having no selection.

Metadata and unrecognized settings keys are retained as JSON data. Guide
positions and grid spacing use canvas pixels. The loader validates known
fields and refuses unsupported format versions. It rejects duplicate entries,
archives larger than 128 MiB uncompressed, manifests larger than 1 MiB,
invalid layer counts/indices/blends/opacities, non-canvas-sized images and
projects exceeding the decoded pixel budget. There is no automatic future
version migration in v1.

The art library uses the same settings dictionary: `tool: "library"`, the
stable `library_tool` identifier, and `library_size`, `library_angle`,
`library_seed`, `library_density` and `library_amount` preserve the chosen
recipe and options. Its painted result remains editable raster pixels on the
original layers. These fields require no format-version change. Window and
debug preferences are stored separately in the user configuration.

Session history, clipboard, view zoom/pan and live tool gestures are not stored.
Raster text and shapes are stored as pixels. Save after changing tool settings
to persist those settings; selecting a color alone does not mark the document
pixels as changed.
