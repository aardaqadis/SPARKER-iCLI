"""True-color half-block terminal canvas and captured mouse gestures."""
from __future__ import annotations

import math
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFilter
from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget


@lru_cache(maxsize=8192)
def _pixel_style(top, bottom):
    return Style(color=Color.from_rgb(*top), bgcolor=Color.from_rgb(*bottom))


class Canvas(Widget):
    can_focus = True
    ALLOW_SELECT = False
    DEFAULT_CSS = "Canvas { width: 1fr; height: 1fr; background: #090909; }"
    BINDINGS = [("escape", "cancel_gesture", "Cancel stroke")]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.cache = None
        self.preview = None
        self.dragging = False
        self.panning = False
        self.points = []
        self.origin = None
        self.base = self.base_mask = None
        self.grid = False
        self.guides = True
        self.cursor = None
        self.resampling = "nearest"
        self._row_cache = {}
        self._stroke_mask = None
        self._stroke_count = self._stroke_dabs = 0
        self._previous_roi = None
        self._preview_base = None
        self._stroke_options = self._last_gesture_key = None
        self._library_tip_key = self._library_tip = None
        self._smooth_composite = self._smooth_signature = None
        self._minified = None
        self._minified_factor = 1
        self._preview_bounds = None
        self._gesture_button = 0

    def set_resampling(self, name):
        """Change display interpolation without resizing the editable pixels."""
        if name not in ("nearest", "bilinear", "bicubic"):
            raise ValueError("View resampling must be nearest, bilinear or bicubic.")
        self.resampling = name
        self.invalidate(artwork=False)

    @property
    def doc(self):
        return self.app.doc

    def invalidate(self, artwork=True):
        self.cache = None
        self._row_cache.clear()
        if artwork:
            self._smooth_composite = self._smooth_signature = None
            self._minified = None
        self.refresh()

    def zoom_limits(self):
        config = getattr(self.app, "config", None)
        if config is None:
            return .025, 32.0
        return config.get("view.zoom_min", .025), config.get("view.zoom_max", 32.0)

    def fit(self):
        minimum, maximum = self.zoom_limits()
        self.zoom = max(minimum, min(maximum, self.size.width/self.doc.width, self.size.height*2/self.doc.height))
        self.pan_x = self.pan_y = 0
        self.invalidate(artwork=False)

    def zoom_at(self, factor, cell=None):
        cell = cell or (self.size.width/2, self.size.height/2)
        world_x = self.pan_x + cell[0]/self.zoom
        world_y = self.pan_y + cell[1]*2/self.zoom
        minimum, maximum = self.zoom_limits()
        self.zoom = max(minimum, min(maximum, self.zoom*factor))
        self.pan_x = world_x - cell[0]/self.zoom
        self.pan_y = world_y - cell[1]*2/self.zoom
        self.invalidate(artwork=False)
        self.app.update_status()

    def image_point(self, event):
        offset = event.get_content_offset_capture(self)
        return math.floor(self.pan_x + offset.x/self.zoom), math.floor(self.pan_y + offset.y*2/self.zoom)

    def inside(self, point):
        return 0 <= point[0] < self.doc.width and 0 <= point[1] < self.doc.height

    def _composite_signature(self):
        return (id(self.doc), self.doc.size, self.doc.revision, tuple((id(layer), id(layer.image), id(layer.mask),
                layer.visible, layer.opacity, layer.blend) for layer in self.doc.layers))

    def _smooth_source(self):
        signature = self._composite_signature()
        if self._smooth_composite is None or self._smooth_signature != signature:
            self._smooth_composite = self.doc.composite()
            self._smooth_signature = signature
            self._minified = None
        return self._smooth_composite

    def _minified_source(self):
        source = self._smooth_source()
        # Each source pixel contributes to an alpha-aware area average before
        # viewport sampling. Upsampling this reduced image cannot skip a whole
        # covered cell, as nearest decimation did for thin zoomed-out strokes.
        factor = max(1, math.ceil(1/self.zoom))
        if self._minified is None or self._minified_factor != factor:
            self._minified = source.reduce(factor)
            self._minified_factor = factor
        return self._minified

    def _reduction_box(self, box, factor):
        return (max(0, math.floor(box[0]/factor)*factor),
                max(0, math.floor(box[1]/factor)*factor),
                min(self.doc.width, math.ceil(box[2]/factor)*factor),
                min(self.doc.height, math.ceil(box[3]/factor)*factor))

    def _patch_composite(self, box):
        if self._smooth_composite is not None:
            if box is None:
                self._smooth_composite = self._smooth_signature = None
                self._minified = None
            else:
                self._smooth_composite.paste(self.doc.composite(box), box)
                self._smooth_signature = self._composite_signature()
                if self._minified is not None:
                    factor = self._minified_factor
                    aligned = self._reduction_box(box, factor)
                    patch = self._smooth_composite.crop(aligned).reduce(factor)
                    self._minified.paste(patch, (aligned[0]//factor, aligned[1]//factor))

    def screen_image(self):
        width, height = max(1, self.size.width), max(1, self.size.height*2)
        config = getattr(self.app, "config", None)
        mode = config.get("view.resampling", self.resampling) if config is not None else self.resampling
        resampling = {"nearest": Image.Resampling.NEAREST,
                      "bilinear": Image.Resampling.BILINEAR,
                      "bicubic": Image.Resampling.BICUBIC}[mode]
        affine = (1/self.zoom, 0, self.pan_x, 0, 1/self.zoom, self.pan_y)
        if self.zoom < 1:
            factor = max(1, math.ceil(1/self.zoom))
            if self.preview is not None and self.preview.mode == "RGBA":
                source = self.preview.reduce(factor)
            else:
                source = self._minified_source()
                if self.preview is not None and self._preview_bounds is not None:
                    # Reduce the contour together with its original pixels,
                    # retaining the same area average as a complete preview.
                    box = self._reduction_box(self._preview_bounds, factor)
                    if box[0] < box[2] and box[1] < box[3]:
                        patch = self._smooth_source().crop(box)
                        patch.paste((244, 226, 133, 255), (0, 0), self.preview.crop(box))
                        source = source.copy()
                        source.paste(patch.reduce(factor), (box[0]//factor, box[1]//factor))
            affine = tuple(value/factor for value in affine)
            result = source.transform((width, height), Image.Transform.AFFINE, affine, resampling)
        elif self.preview is not None and self.preview.mode == "RGBA":
            result = self.preview.transform((width, height), Image.Transform.AFFINE, affine, resampling)
        elif self.preview is not None and resampling != Image.Resampling.NEAREST:
            # Interpolation must sample the contour together with the original
            # pixels; retaining one immutable composite avoids rebuilding layers.
            if self._preview_base is None:
                self._preview_base = self._smooth_source()
            source = self._preview_base.copy()
            source.paste((244, 226, 133, 255), (0, 0), self.preview)
            result = source.transform((width, height), Image.Transform.AFFINE, affine, resampling)
        else:
            if resampling == Image.Resampling.NEAREST:
                result = self.doc.composite_view((width, height), affine, resampling)
            else:
                result = self._smooth_source().transform((width, height), Image.Transform.AFFINE, affine, resampling)
            if self.preview is not None:
                contour = self.preview.transform((width, height), Image.Transform.AFFINE,
                                                 affine, Image.Resampling.NEAREST)
                result.paste((244, 226, 133, 255), (0, 0), contour)
        # Composite transparency against a checkerboard; overlays never enter exports.
        pixels = result.load()
        selection = self.doc.selection
        sel = selection.load() if selection is not None else None
        spacing = self.doc.metadata["grid_spacing"]
        gx, gy = set(self.doc.metadata["guides_x"]), set(self.doc.metadata["guides_y"])
        for y in range(height):
            wy = math.floor(self.pan_y + y/self.zoom)
            for x in range(width):
                wx = math.floor(self.pan_x + x/self.zoom)
                if not (0 <= wx < self.doc.width and 0 <= wy < self.doc.height):
                    pixels[x, y] = (9, 9, 9, 255)
                    continue
                r, g, b, a = pixels[x, y]
                if a == 255:
                    rgb = (r, g, b)
                else:
                    checker = 54 if (wx//4 + wy//4) % 2 else 72
                    rgb = tuple(round(c*a/255 + checker*(1-a/255)) for c in (r, g, b))
                if self.grid and self.zoom >= 1 and (wx % spacing == 0 or wy % spacing == 0):
                    rgb = tuple(round(c*.6 + 120*.4) for c in rgb)
                if self.guides and (wx in gx or wy in gy): rgb = (70, 200, 235)
                if sel is not None and sel[wx, wy] > 0:
                    edge = wx == 0 or wy == 0 or wx == self.doc.width-1 or wy == self.doc.height-1
                    if not edge:
                        edge = any(sel[nx, ny] == 0 for nx, ny in ((wx-1, wy), (wx+1, wy), (wx, wy-1), (wx, wy+1)))
                    if edge: rgb = (245, 215, 80) if (wx+wy) % 2 else (25, 25, 25)
                pixels[x, y] = rgb + (255,)
        return result

    def render_line(self, y):
        size = (max(1, self.size.width), max(1, self.size.height*2))
        if self.cache is None or self.cache.size != size:
            self.cache = self.screen_image()
            self._row_cache.clear()
        if y in self._row_cache:
            return self._row_cache[y]
        if y < 0 or y*2+1 >= self.cache.height:
            return Strip.blank(self.size.width, Style(color="#d7d7d7", bgcolor="#090909"))
        pixels = self.cache.load()
        segments = []
        for x in range(self.cache.width):
            top, bottom = pixels[x, y*2][:3], pixels[x, y*2+1][:3]
            style = _pixel_style(top, bottom)
            if segments and segments[-1].style == style:
                previous = segments[-1]
                segments[-1] = Segment(previous.text + "▀", style)
            else:
                segments.append(Segment("▀", style))
        strip = Strip(segments, self.cache.width)
        self._row_cache[y] = strip
        return strip

    def on_resize(self):
        self.invalidate(artwork=False)

    def on_mouse_down(self, event: events.MouseDown):
        event.stop()
        if self.dragging:
            # Some terminal input streams repeat the held-button down report
            # with new coordinates. They are motion within the same capture.
            if event.button == self._gesture_button: self.on_mouse_move(event)
            return
        self.focus()
        point = self.image_point(event)
        if event.button in (2, 3) or self.app.tool == "hand":
            self.dragging = self.panning = True
            self._gesture_button = event.button
            self.origin = (event.screen_x, event.screen_y, self.pan_x, self.pan_y)
            self.capture_mouse()
            return
        if event.button != 1 or not self.inside(point): return
        app, doc = self.app, self.doc
        try:
            if app.tool == "picker":
                pixel = doc.composite((*point, point[0]+1, point[1]+1)).getpixel((0, 0))
                app.foreground = "#%02x%02x%02x" % pixel[:3]
                app.sync_ui()
                return
            if app.tool == "text":
                app.text_dialog(point)
                return
            if app.tool == "fill":
                with doc.edit("Flood fill"):
                    doc.paint_mask(doc.region_mask(point, app.tolerance), app.foreground, app.opacity)
                app.sync_ui()
                return
            if app.tool == "wand":
                with doc.edit("Wand selection"):
                    doc.set_selection(doc.region_mask(point, app.tolerance, True), app.selection_mode)
                app.sync_ui()
                return
            if app.tool == "library":
                from .tool_library import get_tool
                spec = get_tool(app.library_tool)
                if spec.kind == "effect":
                    app.action_apply_library()
                    return
                app.store_tool_settings()
            if not app.tool.startswith("select") and app.tool != "lasso": doc.ensure_editable()
            doc.begin(spec.name if app.tool == "library" else app.tool.replace("_", " ").title())
            self.dragging = True
            self._gesture_button = event.button
            self.origin = point
            self.points = [point]
            # The transaction already owns an immutable copy for undo. Reuse it
            # as the stroke's original pixels instead of duplicating every layer.
            original = doc.pending[1][1][doc.active]
            self.base, self.base_mask = original.image, original.mask
            self._stroke_mask = None
            self._stroke_count = self._stroke_dabs = 0
            self._previous_roi = self._preview_base = None
            self._stroke_options = self._last_gesture_key = None
            self._library_tip_key = self._library_tip = None
            self._preview_bounds = None
            self.capture_mouse()
            self.update_gesture(point)
        except (ValueError, OSError) as error:
            doc.cancel()
            self.release_mouse()
            self.dragging = False
            self._gesture_button = 0
            self.preview = self.base = self.base_mask = None
            self._stroke_mask = self._preview_base = self._previous_roi = None
            self._stroke_options = self._last_gesture_key = None
            self._library_tip_key = self._library_tip = None
            self._preview_bounds = None
            self._stroke_count = self._stroke_dabs = 0
            self.invalidate()
            app.notify(str(error), severity="warning")

    def _clip_box(self, box):
        left, top = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
        right, bottom = min(self.doc.width, math.ceil(box[2])), min(self.doc.height, math.ceil(box[3]))
        return (left, top, right, bottom) if left < right and top < bottom else None

    @staticmethod
    def _union_box(a, b):
        if a is None: return b
        if b is None: return a
        return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])

    def _gesture_key(self):
        app = self.app
        return tuple(getattr(app, key, None) for key in (
            "tool", "brush_size", "hardness", "foreground", "opacity", "filled",
            "library_tool", "library_size", "library_seed", "library_angle", "library_density"))

    def _prepare_stroke(self, options):
        if self._stroke_options is not None and self._stroke_options != options:
            # Changing a setting during capture rebuilds the existing stroke
            # once, matching the prior full-mask behavior without stale edges.
            self.doc.layer.image = self.base.copy()
            self._stroke_mask = self._previous_roi = None
            self._stroke_count = self._stroke_dabs = 0
            self._smooth_composite = self._smooth_signature = None
            self._minified = None
        self._stroke_options = options

    def _paint_stroke(self, tool):
        """Append native-resolution geometry and repaint only its changed pixels.

        A local blur includes the complete filter halo twice: the output halo
        covers every affected pixel, while the input halo preserves exact blur
        values at its boundary. Opacity is applied against the original stroke,
        so repeated overlapping dabs do not build up extra paint.
        """
        app, doc = self.app, self.doc
        size = 1 if tool == "pencil" else app.brush_size
        hardness = 1 if tool == "pencil" else app.hardness
        self._prepare_stroke((tool, size, hardness, app.foreground, app.opacity))
        if self._stroke_mask is None:
            self._stroke_mask = Image.new("L", doc.size)
        if self._stroke_count >= len(self.points): return
        start = max(0, self._stroke_count-1)
        recent = self.points[start:]
        draw = ImageDraw.Draw(self._stroke_mask)
        radius, width = max(0, (size-1)/2), max(1, round(size))
        if len(recent) > 1:
            draw.line(recent, fill=255, width=width)
        for x, y in self.points[self._stroke_count:]:
            if radius == 0: draw.point((x, y), fill=255)
            else: draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=255)
        self._stroke_count = len(self.points)
        margin = max(radius, width/2)+2
        changed = (min(p[0] for p in recent)-margin, min(p[1] for p in recent)-margin,
                   max(p[0] for p in recent)+margin+1, max(p[1] for p in recent)+margin+1)
        blur = size*(1-hardness)/3 if hardness < 1 and size > 1 else 0
        halo = math.ceil(blur*3)+3 if blur else 0
        output_box = self._clip_box((changed[0]-halo, changed[1]-halo,
                                     changed[2]+halo, changed[3]+halo))
        if output_box is None: return
        if blur:
            input_box = self._clip_box((output_box[0]-halo, output_box[1]-halo,
                                       output_box[2]+halo, output_box[3]+halo))
            mask = self._stroke_mask.crop(input_box).filter(ImageFilter.GaussianBlur(blur))
            mask = mask.crop((output_box[0]-input_box[0], output_box[1]-input_box[1],
                              output_box[2]-input_box[0], output_box[3]-input_box[1]))
        else:
            mask = self._stroke_mask.crop(output_box)
        doc.paint_mask(mask, app.foreground, app.opacity, tool == "eraser", self.base, box=output_box)
        self._patch_composite(output_box)

    def _paint_shape(self, tool, point):
        app, doc = self.app, self.doc
        self._prepare_stroke((tool, app.brush_size, app.filled, app.foreground, app.opacity))
        # Pillow's wide rectangle outline may extend past a very small box.
        margin = max(1, app.brush_size)+2
        new_box = self._clip_box((min(self.origin[0], point[0])-margin,
                                  min(self.origin[1], point[1])-margin,
                                  max(self.origin[0], point[0])+margin+1,
                                  max(self.origin[1], point[1])+margin+1))
        box = self._union_box(self._previous_roi, new_box)
        self._previous_roi = new_box
        if box is None: return
        mask = Image.new("L", (box[2]-box[0], box[3]-box[1]))
        draw = ImageDraw.Draw(mask)
        start = (self.origin[0]-box[0], self.origin[1]-box[1])
        end = (point[0]-box[0], point[1]-box[1])
        if tool == "line":
            draw.line((start, end), fill=255, width=max(1, int(app.brush_size)))
        else:
            bounds = (min(start[0], end[0]), min(start[1], end[1]),
                      max(start[0], end[0]), max(start[1], end[1]))
            fn = draw.ellipse if tool == "ellipse" else draw.rectangle
            fn(bounds, fill=255 if app.filled else None, outline=255, width=max(1, int(app.brush_size)))
        # Including the old bounds restores pixels when the shape shrinks.
        doc.paint_mask(mask, app.foreground, app.opacity, base=self.base, box=box)
        self._patch_composite(box)

    def _paint_library(self, point):
        from .tool_library import get_tool, render_tip, _paste_union, _repeat_tile
        app, doc = self.app, self.doc
        spec = get_tool(app.library_tool)
        geometry = (spec.id, app.library_size, app.library_seed, app.library_angle, app.library_density)
        self._prepare_stroke((*geometry, app.foreground, app.opacity))
        if self._library_tip_key != geometry:
            self._library_tip = render_tip(spec, *geometry[1:])
            self._library_tip_key = geometry
        tip = self._library_tip
        if spec.kind == "pattern":
            x0, x1 = sorted((self.origin[0], point[0]))
            y0, y1 = sorted((self.origin[1], point[1]))
            new_box = self._clip_box((x0, y0, x1+1, y1+1))
            box = self._union_box(self._previous_roi, new_box)
            self._previous_roi = new_box
            if box is None: return
            mask = Image.new("L", (box[2]-box[0], box[3]-box[1]))
            if new_box is not None:
                tile = _repeat_tile(tip, (new_box[2]-new_box[0], new_box[3]-new_box[1]),
                                    ((new_box[0]-x0)%tip.width, (new_box[1]-y0)%tip.height))
                mask.paste(tile, (new_box[0]-box[0], new_box[1]-box[1]))
            doc.paint_mask(mask, app.foreground, app.opacity, base=self.base, box=box)
            self._patch_composite(box)
            return
        if self._stroke_count >= len(self.points): return
        if self._stroke_mask is None: self._stroke_mask = Image.new("L", doc.size)
        if len(self.points) > 10000: raise ValueError("Provide between 1 and 10,000 points.")
        if any(abs(value) > 1_000_000 for point in self.points[self._stroke_count:] for value in point):
            raise ValueError("Point coordinate must be between -1e+06 and 1e+06.")
        dabs = self.points[self._stroke_count:] if spec.kind != "brush" else []
        if spec.kind == "brush":
            if self._stroke_count == 0: dabs.append(self.points[0])
            for a, b in zip(self.points[max(0, self._stroke_count-1):], self.points[max(1, self._stroke_count):]):
                steps = max(1, math.ceil(math.dist(a, b)/max(1, tip.width*.18)))
                if self._stroke_dabs+len(dabs)+steps > 50000:
                    raise ValueError("Stroke exceeds 50,000 brush dabs; split the gesture.")
                dabs.extend((a[0]+(b[0]-a[0])*i/steps, a[1]+(b[1]-a[1])*i/steps) for i in range(1, steps+1))
        self._stroke_count = len(self.points)
        self._stroke_dabs += len(dabs)
        box = None
        for x, y in dabs:
            left, top = round(x-tip.width/2), round(y-tip.height/2)
            _paste_union(self._stroke_mask, tip, left, top)
            box = self._union_box(box, self._clip_box((left, top, left+tip.width, top+tip.height)))
        if box is not None:
            doc.paint_mask(self._stroke_mask.crop(box), app.foreground, app.opacity, base=self.base, box=box)
            self._patch_composite(box)

    def update_gesture(self, point):
        app, doc = self.app, self.doc
        tool = app.tool
        self._last_gesture_key = self._gesture_key()
        if tool == "library":
            self._paint_library(point)
        elif tool in ("brush", "pencil", "eraser"):
            self._paint_stroke(tool)
        elif tool == "move":
            doc.move(point[0]-self.origin[0], point[1]-self.origin[1], self.base, self.base_mask)
            self._patch_composite(None)
        elif tool in ("line", "rectangle", "ellipse"):
            self._paint_shape(tool, point)
        # Selection and gradient previews use a shape contour, without changing selection.
        elif tool.startswith("select") or tool == "lasso" or tool == "gradient":
            if self.preview is None: self.preview = Image.new("L", doc.size)
            else: self.preview.paste(0, (0, 0, doc.width, doc.height))
            draw = ImageDraw.Draw(self.preview)
            if tool == "lasso" and len(self.points) > 1: draw.line(self.points, fill=255, width=1)
            elif tool == "gradient": draw.line((self.origin, point), fill=255, width=1)
            else:
                x0, x1 = sorted((self.origin[0], point[0]))
                y0, y1 = sorted((self.origin[1], point[1]))
                fn = draw.ellipse if tool == "select_ellipse" else draw.rectangle
                fn((x0, y0, x1, y1), outline=255)
            vertices = self.points if tool == "lasso" else (self.origin, point)
            self._preview_bounds = self._clip_box((min(p[0] for p in vertices)-1,
                min(p[1] for p in vertices)-1, max(p[0] for p in vertices)+2,
                max(p[1] for p in vertices)+2))
        self.invalidate(artwork=False)

    def on_mouse_move(self, event: events.MouseMove):
        point = self.image_point(event)
        if point != self.cursor:
            self.cursor = point
            self.app.update_status(point)
        if not self.dragging: return
        event.stop()
        if self.panning:
            sx, sy, px, py = self.origin
            self.pan_x = px - (event.screen_x-sx)/self.zoom
            self.pan_y = py - (event.screen_y-sy)*2/self.zoom
            self.invalidate(artwork=False)
        else:
            if point != self.points[-1] or self._gesture_key() != self._last_gesture_key:
                if point != self.points[-1]: self.points.append(point)
                try:
                    self.update_gesture(point)
                except (ValueError, OSError) as error:
                    self.action_cancel_gesture()
                    self.app.notify(str(error), severity="warning")

    def on_mouse_up(self, event: events.MouseUp):
        if not self.dragging: return
        event.stop()
        if event.button not in (0, self._gesture_button): return
        try:
            if not self.panning:
                point = self.image_point(event)
                if point != self.points[-1] or self._gesture_key() != self._last_gesture_key:
                    if point != self.points[-1]: self.points.append(point)
                    self.update_gesture(point)
                tool, app = self.app.tool, self.app
                if tool.startswith("select") or tool == "lasso":
                    kind = "ellipse" if tool == "select_ellipse" else ("lasso" if tool == "lasso" else "rectangle")
                    self.doc.select(kind, self.origin, point, app.selection_mode, self.points)
                elif tool == "gradient":
                    self.doc.gradient(self.origin, point, app.foreground, app.background, app.opacity, app.radial)
                cache_is_current = (self._smooth_composite is not None and
                                    self._smooth_signature == self._composite_signature())
                self.doc.commit()
                if cache_is_current:
                    self._smooth_signature = self._composite_signature()
        except (ValueError, OSError) as error:
            self.doc.cancel()
            self.app.notify(str(error), severity="error")
        finally:
            self.release_mouse()
            self.dragging = self.panning = False
            self._gesture_button = 0
            self.preview = self.base = self.base_mask = None
            self._stroke_mask = self._preview_base = self._previous_roi = None
            self._stroke_count = self._stroke_dabs = 0
            self._stroke_options = self._last_gesture_key = None
            self._library_tip_key = self._library_tip = None
            self._preview_bounds = None
            self.app.sync_ui(artwork=False)

    def action_cancel_gesture(self):
        if self.dragging:
            self.doc.cancel()
            self.release_mouse()
            self.dragging = self.panning = False
            self._gesture_button = 0
            self.preview = self.base = self.base_mask = None
            self._stroke_mask = self._preview_base = self._previous_roi = None
            self._stroke_count = self._stroke_dabs = 0
            self._stroke_options = self._last_gesture_key = None
            self._library_tip_key = self._library_tip = None
            self._preview_bounds = None
            self.app.sync_ui()

    def on_mouse_scroll_up(self, event):
        self.zoom_at(1.25, (event.x, event.y))
        event.stop()

    def on_mouse_scroll_down(self, event):
        self.zoom_at(.8, (event.x, event.y))
        event.stop()
