"""Raster document, compositing, selections, transactions and editing operations.

Pixels are straight-alpha RGBA; layer order is bottom to top. All pixel mutations
are committed through one bounded, whole-document undo history.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import copy
from functools import lru_cache
import math
from collections import deque

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

MAX_PIXELS = 4_194_304
MAX_LAYERS = 64
MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
BLENDS = ("normal", "multiply", "screen", "overlay", "darken", "lighten", "difference", "add")
PALETTE = ["#151b29", "#ffffff", "#e85d75", "#f6ae2d", "#f4e285", "#65c18c",
           "#2cb9c5", "#5a8dee", "#ad7be9", "#e48ac5", "#855d42", "#a0a9b8"]


def valid_size(width, height):
    if type(width) is not int or type(height) is not int or not (
            1 <= width <= 4096 and 1 <= height <= 4096 and width * height <= MAX_PIXELS):
        raise ValueError("Canvas must be 1–4096 pixels per side, at most 4,194,304 pixels.")


def _opacity(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Opacity must be a finite number.")
    return max(0.0, min(1.0, value))


@lru_cache(maxsize=256)
def _opacity_table(opacity):
    """Keep the existing integer alpha rounding without rebuilding each lookup."""
    return tuple(round(value * opacity) for value in range(256))


@dataclass
class Layer:
    name: str
    image: Image.Image
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    blend: str = "normal"
    mask: Image.Image | None = None

    def clone(self):
        return Layer(self.name, self.image.copy(), self.visible, self.locked,
                     self.opacity, self.blend, self.mask.copy() if self.mask else None)

    def rendered(self, box=None, *, copy=True):
        """Render a layer region; callers may borrow unmodified pixels internally.

        The public default still returns an independent image. Compositing can
        avoid a full RGBA copy when there is no mask or opacity adjustment.
        """
        result = self.image.crop(box) if box is not None else self.image
        if self.mask is None and self.opacity == 1:
            return result.copy() if copy and box is None else result
        if box is None:
            result = result.copy()
        alpha = result.getchannel("A")
        if self.mask is not None:
            mask = self.mask.crop(box) if box is not None else self.mask
            alpha = ImageChops.multiply(alpha, mask)
        if self.opacity != 1:
            alpha = alpha.point(_opacity_table(self.opacity))
        result.putalpha(alpha)
        return result


def composite_layer(back, front, blend="normal"):
    """Blend RGB in the overlap, then source-over with correct transparent areas."""
    if blend == "normal":
        return Image.alpha_composite(back, front)
    b, s = back.convert("RGB"), front.convert("RGB")
    functions = {"multiply": ImageChops.multiply, "screen": ImageChops.screen,
                 "overlay": ImageChops.overlay, "darken": ImageChops.darker,
                 "lighten": ImageChops.lighter, "difference": ImageChops.difference,
                 "add": ImageChops.add}
    mixed = functions[blend](b, s)
    # An absent backdrop must not affect source RGB.
    rgb = Image.composite(mixed, s, back.getchannel("A"))
    colored = rgb.convert("RGBA")
    colored.putalpha(front.getchannel("A"))
    return Image.alpha_composite(back, colored)


class Document:
    def __init__(self, width=96, height=64):
        valid_size(width, height)
        self.width, self.height = width, height
        self.layers = [Layer("Background", Image.new("RGBA", self.size, "white")),
                       Layer("Paint", Image.new("RGBA", self.size))]
        self.active = 1
        self.selection = None
        self.metadata = {"title": "Untitled", "guides_x": [], "guides_y": [], "grid_spacing": 8}
        self.settings = {"palette": PALETTE.copy()}
        self.undo_stack = []
        self.redo_stack = []
        self.pending = None
        self.revision = 0
        self.saved_revision = 0
        self.serial = 0
        self.clipboard = None
        self.history_limit = 40
        self.history_bytes = 96 * 1024 * 1024

    @property
    def size(self):
        return self.width, self.height

    @property
    def layer(self):
        return self.layers[self.active]

    @property
    def dirty(self):
        return self.revision != self.saved_revision

    def snapshot(self):
        return (self.size, [layer.clone() for layer in self.layers], self.active,
                self.selection.copy() if self.selection else None,
                copy.deepcopy(self.metadata), copy.deepcopy(self.settings), self.revision)

    def restore(self, state):
        size, layers, self.active, selection, metadata, settings, self.revision = state
        self.width, self.height = size
        self.layers = [layer.clone() for layer in layers]
        self.selection = selection.copy() if selection else None
        self.metadata, self.settings = copy.deepcopy(metadata), copy.deepcopy(settings)

    def begin(self, label):
        if self.pending is not None:
            raise RuntimeError("An edit is already in progress")
        self.pending = (label, self.snapshot())

    def commit(self):
        if self.pending is None:
            return
        self.undo_stack.append(self.pending)
        self.pending = None
        self.redo_stack.clear()
        self.serial += 1
        self.revision = self.serial
        self._trim()

    def _trim(self):
        def cost(entry):
            size, layers, _, selection, *_ = entry[1]
            return size[0] * size[1] * (sum(4 + (x.mask is not None) for x in layers) + (selection is not None))
        # Redo snapshots consume the same memory as undo snapshots. Discard the
        # farthest checkpoints first, retaining the next undo/redo when possible.
        while (len(self.undo_stack) + len(self.redo_stack) > self.history_limit or
               sum(map(cost, self.undo_stack + self.redo_stack)) > self.history_bytes):
            if self.undo_stack:
                self.undo_stack.pop(0)
            elif self.redo_stack:
                self.redo_stack.pop(0)
            else:
                break

    def cancel(self):
        if self.pending:
            self.restore(self.pending[1])
            self.pending = None

    @contextmanager
    def edit(self, label):
        self.begin(label)
        try:
            yield
        except Exception:
            self.cancel()
            raise
        else:
            self.commit()

    def undo(self):
        if self.pending is not None:
            raise RuntimeError("Finish or cancel the current edit before undoing.")
        if not self.undo_stack:
            return False
        label, state = self.undo_stack.pop()
        self.redo_stack.append((label, self.snapshot()))
        self.restore(state)
        self._trim()
        return True

    def redo(self):
        if self.pending is not None:
            raise RuntimeError("Finish or cancel the current edit before redoing.")
        if not self.redo_stack:
            return False
        label, state = self.redo_stack.pop()
        self.undo_stack.append((label, self.snapshot()))
        self.restore(state)
        self._trim()
        return True

    def ensure_editable(self):
        if self.layer.locked:
            raise ValueError("This layer is locked. Unlock it in the Layers menu.")

    def composite(self, box=None):
        """Composite exact document pixels, optionally only a rectangular region."""
        size = self.size if box is None else (box[2] - box[0], box[3] - box[1])
        result = Image.new("RGBA", size)
        for layer in self.layers:
            if layer.visible:
                result = composite_layer(result, layer.rendered(box, copy=False), layer.blend)
        return result

    def composite_view(self, size, affine, resampling=Image.Resampling.NEAREST):
        """Render a viewport without visiting every pixel of a large canvas.

        Nearest sampling commutes exactly with pointwise alpha and blend
        operations. Sampling each layer first therefore produces the same RGBA
        pixels as sampling a full composite, using only viewport-sized buffers.
        Other sampling modes interpolate neighboring pixels and use the full
        composite to retain the original blend and transparency behavior.
        """
        if resampling != Image.Resampling.NEAREST:
            return self.composite().transform(size, Image.Transform.AFFINE, affine, resampling)
        result = Image.new("RGBA", size)
        for layer in self.layers:
            if not layer.visible:
                continue
            front = layer.image.transform(size, Image.Transform.AFFINE, affine, resampling)
            if layer.mask is not None or layer.opacity != 1:
                alpha = front.getchannel("A")
                if layer.mask is not None:
                    mask = layer.mask.transform(size, Image.Transform.AFFINE, affine, resampling)
                    alpha = ImageChops.multiply(alpha, mask)
                if layer.opacity != 1:
                    alpha = alpha.point(_opacity_table(layer.opacity))
                front.putalpha(alpha)
            result = composite_layer(result, front, layer.blend)
        return result

    def clipped(self, mask):
        return ImageChops.multiply(mask, self.selection) if self.selection is not None else mask

    def paint_mask(self, mask, color, opacity=1.0, erase=False, base=None, *, box=None):
        """Paint a mask, blending against ``base`` once for a complete stroke.

        A canvas-sized mask retains the original replacement semantics: the
        entire result is based on ``base``. An explicit box accepts a local mask
        and updates only that region of the current layer, so mouse gestures can
        update their changed pixels without copying a multi-megapixel image.
        """
        self.ensure_editable()
        opacity = _opacity(opacity)
        base = self.layer.image if base is None else base
        local = box is not None
        complete = False
        if mask.mode != "L":
            raise ValueError("Paint masks must be grayscale images.")
        if local:
            if mask.size != (box[2] - box[0], box[3] - box[1]):
                raise ValueError("Paint mask size differs from its region.")
            region = box
            result = self.layer.image
        else:
            if mask.size != self.size:
                raise ValueError("Paint mask size differs from canvas.")
            region = mask.getbbox()
            complete = region == (0, 0, self.width, self.height)
            result = None if complete else base.copy()
            if region is not None and not complete:
                mask = mask.crop(region)
        # Validate the color even for an empty mask, as the full-frame path did.
        if region is None:
            if not erase:
                Image.new("RGBA", (1, 1), color)
            self.layer.image = result
            return
        ink = None if erase else Image.new("RGBA", mask.size, color)
        if self.selection is not None:
            mask = ImageChops.multiply(mask, self.selection.crop(region))
        if opacity != 1:
            mask = mask.point(_opacity_table(opacity))
        source = base if complete else base.crop(region)
        if erase:
            if complete:
                source = source.copy()
            source.putalpha(ImageChops.multiply(source.getchannel("A"), ImageOps.invert(mask)))
            painted = source
        else:
            ink.putalpha(ImageChops.multiply(ink.getchannel("A"), mask))
            painted = Image.alpha_composite(source, ink)
        if complete:
            result = painted
        else:
            result.paste(painted, region[:2])
        self.layer.image = result

    def stroke_mask(self, points, size=1, hardness=1.0):
        mask = Image.new("L", self.size)
        draw = ImageDraw.Draw(mask)
        radius = max(0, (size - 1) / 2)
        if len(points) > 1:
            draw.line(points, fill=255, width=max(1, round(size)))
        for x, y in points:
            if radius == 0:
                draw.point((x, y), fill=255)
            else:
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=255)
        if hardness < 1 and size > 1:
            mask = mask.filter(ImageFilter.GaussianBlur(size * (1 - hardness) / 3))
        return mask

    def shape_mask(self, kind, start, end, size=1, filled=False):
        if kind not in ("line", "rectangle", "ellipse"):
            raise ValueError("Unknown shape. Use line, rectangle or ellipse.")
        mask = Image.new("L", self.size)
        draw = ImageDraw.Draw(mask)
        x0, x1 = sorted((start[0], end[0]))
        y0, y1 = sorted((start[1], end[1]))
        if kind == "line":
            draw.line((start, end), fill=255, width=max(1, int(size)))
        else:
            fn = draw.ellipse if kind == "ellipse" else draw.rectangle
            fn((x0, y0, x1, y1), fill=255 if filled else None, outline=255, width=max(1, int(size)))
        return mask

    def select(self, kind, start, end, combine="replace", points=None):
        if kind == "lasso":
            mask = Image.new("L", self.size)
            if points and len(points) >= 3:
                ImageDraw.Draw(mask).polygon(points, fill=255)
        else:
            mask = self.shape_mask(kind, start, end, filled=True)
        self.set_selection(mask, combine)

    def set_selection(self, mask, combine="replace"):
        if combine not in ("replace", "add", "subtract", "intersect"):
            raise ValueError("Unknown selection combination.")
        if mask.mode != "L" or mask.size != self.size:
            raise ValueError("Selection must be a canvas-sized grayscale mask.")
        if self.selection is None or combine == "replace":
            self.selection = mask
        elif combine == "add":
            self.selection = ImageChops.lighter(self.selection, mask)
        elif combine == "subtract":
            self.selection = ImageChops.subtract(self.selection, mask)
        else:
            self.selection = ImageChops.multiply(self.selection, mask)

    def region_mask(self, point, tolerance=0, sample_merged=False):
        x, y = point
        if not (0 <= x < self.width and 0 <= y < self.height):
            return Image.new("L", self.size)
        source = self.composite() if sample_merged else self.layer.image
        target = source.getpixel((x, y))
        # Compare RGBA channels in Pillow rather than constructing a Python
        # tuple and four differences for every visited pixel. The tolerance is
        # still the largest channel difference, including transparent RGB.
        channels = ImageChops.difference(source, Image.new(source.mode, self.size, target)).split()
        distance = channels[0]
        for channel in channels[1:]:
            distance = ImageChops.lighter(distance, channel)
        eligible = distance.point(lambda value: 255 if value <= tolerance else 0)
        if self.selection is not None:
            selected = self.selection.point(lambda value: 255 if value else 0)
            eligible = ImageChops.multiply(eligible, selected)
        remaining = bytearray(eligible.tobytes())
        output = bytearray(self.width * self.height)
        queue = deque([y * self.width + x])
        zero, filled = b"\x00", b"\xff"
        while queue:
            index = queue.popleft()
            if not remaining[index]:
                continue
            row = index // self.width
            row_start, row_end = row * self.width, (row + 1) * self.width
            left = remaining.rfind(zero, row_start, index) + 1
            if left == 0:
                left = row_start
            right = remaining.find(zero, index, row_end)
            if right < 0:
                right = row_end
            count = right - left
            remaining[left:right] = zero * count
            output[left:right] = filled * count
            # Queue one seed per neighboring horizontal span. bytearray.find
            # scans in C and previously filled spans are already zeroed.
            for offset in (-self.width, self.width):
                if not 0 <= row + (-1 if offset < 0 else 1) < self.height:
                    continue
                cursor, end = left + offset, right + offset
                while cursor < end:
                    seed = remaining.find(filled, cursor, end)
                    if seed < 0:
                        break
                    queue.append(seed)
                    stop = remaining.find(zero, seed, end)
                    cursor = end if stop < 0 else stop + 1
        return Image.frombytes("L", self.size, bytes(output))

    def gradient(self, start, end, foreground, background, opacity=1, radial=False):
        self.ensure_editable()
        from PIL import ImageColor
        a, b = ImageColor.getcolor(foreground, "RGBA"), ImageColor.getcolor(background, "RGBA")
        opacity = _opacity(opacity)
        dx, dy = end[0]-start[0], end[1]-start[1]
        length = max(1, dx*dx + dy*dy)
        # A compact buffer avoids millions of Python tuples on large canvases.
        if not radial and dy == 0:
            row = bytearray(self.width * 4)
            for x in range(self.width):
                t = max(0, min(1, ((x-start[0])*dx) / length))
                for channel, (v, w) in enumerate(zip(a, b)):
                    row[x * 4 + channel] = round(v*(1-t) + w*t)
            pixels = row * self.height
        elif not radial and dx == 0:
            pixels = bytearray()
            for y in range(self.height):
                t = max(0, min(1, ((y-start[1])*dy) / length))
                pixel = bytes(round(v*(1-t) + w*t) for v, w in zip(a, b))
                pixels.extend(pixel * self.width)
        else:
            pixels = bytearray(self.width * self.height * 4)
            offset = 0
            radius = math.sqrt(length)
            for y in range(self.height):
                for x in range(self.width):
                    t = math.hypot(x-start[0], y-start[1]) / radius if radial else ((x-start[0])*dx + (y-start[1])*dy) / length
                    t = max(0, min(1, t))
                    for channel, (v, w) in enumerate(zip(a, b)):
                        pixels[offset + channel] = round(v*(1-t) + w*t)
                    offset += 4
        ink = Image.frombytes("RGBA", self.size, bytes(pixels))
        mask = self.clipped(Image.new("L", self.size, round(255 * opacity)))
        ink.putalpha(ImageChops.multiply(ink.getchannel("A"), mask))
        self.layer.image = Image.alpha_composite(self.layer.image, ink)

    def text(self, point, text, color, size=12, font_path="", opacity=1):
        self.ensure_editable()
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)
        mask = Image.new("L", self.size)
        ImageDraw.Draw(mask).multiline_text(point, text, font=font, fill=255, spacing=2)
        self.paint_mask(mask, color, opacity)

    def add_layer(self, name="Layer", image=None):
        if not isinstance(name, str) or len(name) > 256:
            raise ValueError("Layer names must contain at most 256 characters.")
        if len(self.layers) >= MAX_LAYERS:
            raise ValueError("At most 64 layers are supported.")
        if self.width * self.height * (5*(len(self.layers)+1)+1) > MAX_DOCUMENT_BYTES:
            raise ValueError("Adding a layer would exceed the 128 MiB project pixel budget.")
        if image is not None and image.size != self.size:
            raise ValueError("Layer size differs from canvas.")
        self.active += 1
        self.layers.insert(self.active, Layer(name, image.convert("RGBA") if image is not None else Image.new("RGBA", self.size)))

    def duplicate(self):
        source = self.layer.clone()
        self.add_layer(source.name + " copy", source.image)
        source.name += " copy"
        self.layers[self.active] = source

    def delete_layer(self):
        if len(self.layers) == 1:
            raise ValueError("Keep at least one layer.")
        self.layers.pop(self.active)
        self.active = min(self.active, len(self.layers)-1)

    def merge_down(self):
        if self.active == 0:
            raise ValueError("The bottom layer has no layer beneath it.")
        lower, upper = self.layers[self.active-1:self.active+1]
        if lower.locked or upper.locked or not lower.visible or not upper.visible:
            raise ValueError("Both layers must be visible and unlocked to merge.")
        if lower.blend != "normal":
            raise ValueError("Set the lower layer to normal blend before merging.")
        merged = composite_layer(lower.rendered(), upper.rendered(), upper.blend)
        self.layers[self.active-1:self.active+1] = [Layer(lower.name, merged)]
        self.active -= 1

    def move(self, dx, dy, base=None, mask_base=None):
        self.ensure_editable()
        source = base if base is not None else self.layer.image
        result = Image.new("RGBA", self.size)
        result.paste(source, (int(dx), int(dy)))
        self.layer.image = result
        source_mask = mask_base if mask_base is not None else self.layer.mask
        if source_mask is not None:
            mask = Image.new("L", self.size)
            mask.paste(source_mask, (int(dx), int(dy)))
            self.layer.mask = mask

    def copy_selection(self, cut=False):
        if cut: self.ensure_editable()
        mask = self.selection if self.selection is not None else Image.new("L", self.size, 255)
        box = mask.getbbox()
        if box is None:
            raise ValueError("The selection is empty.")
        image = self.layer.image.copy()
        image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
        self.clipboard = (image.crop(box), box[:2])
        if cut:
            self.paint_mask(Image.new("L", self.size, 255), "black", erase=True)

    def paste(self):
        if self.clipboard is None:
            raise ValueError("Copy a selection first.")
        image, position = self.clipboard
        full = Image.new("RGBA", self.size)
        full.paste(image, position)
        self.add_layer("Pasted", full)

    def apply_filter(self, name, amount=1.0):
        self.ensure_editable()
        source = self.layer.image
        rgb, alpha = source.convert("RGB"), source.getchannel("A")
        if name == "invert": result = ImageOps.invert(rgb)
        elif name == "grayscale": result = ImageOps.grayscale(rgb).convert("RGB")
        elif name == "sepia": result = ImageOps.colorize(ImageOps.grayscale(rgb), "#22150d", "#f4dda5")
        elif name == "blur": result = rgb.filter(ImageFilter.GaussianBlur(max(0, min(100, amount))))
        elif name == "sharpen": result = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=round(150*max(0, min(10, amount)))))
        elif name == "edges": result = rgb.filter(ImageFilter.FIND_EDGES)
        elif name == "emboss": result = rgb.filter(ImageFilter.EMBOSS)
        elif name == "posterize": result = ImageOps.posterize(rgb, max(1, min(8, round(amount))))
        elif name == "threshold": result = ImageOps.grayscale(rgb).point(lambda v: 255 if v >= amount else 0).convert("RGB")
        elif name in ("brightness", "contrast", "saturation"):
            enhancer = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast, "saturation": ImageEnhance.Color}[name]
            result = enhancer(rgb).enhance(max(0, min(10, amount)))
        elif name == "autocontrast": result = ImageOps.autocontrast(rgb)
        else: raise ValueError("Unknown filter")
        result = result.convert("RGBA")
        result.putalpha(alpha)
        self.layer.image = Image.composite(result, source, self.selection) if self.selection is not None else result

    def transform_layer(self, kind):
        self.ensure_editable()
        methods = {"flip_h": Image.Transpose.FLIP_LEFT_RIGHT, "flip_v": Image.Transpose.FLIP_TOP_BOTTOM,
                   "rotate_cw": Image.Transpose.ROTATE_270, "rotate_ccw": Image.Transpose.ROTATE_90}
        def transform(image):
            rotated = image.transpose(methods[kind])
            result = Image.new(image.mode, self.size)
            result.paste(rotated, ((self.width-rotated.width)//2, (self.height-rotated.height)//2))
            return result
        self.layer.image = transform(self.layer.image)
        if self.layer.mask is not None: self.layer.mask = transform(self.layer.mask)

    def resize(self, width, height, resample=True):
        valid_size(width, height)
        if width * height * (5*len(self.layers)+1) > MAX_DOCUMENT_BYTES:
            raise ValueError("This size and layer count exceed the 128 MiB project pixel budget.")
        old = self.size
        for layer in self.layers:
            if resample:
                layer.image = layer.image.resize((width, height), Image.Resampling.LANCZOS)
                if layer.mask is not None: layer.mask = layer.mask.resize((width, height), Image.Resampling.LANCZOS)
            else:
                image = Image.new("RGBA", (width, height))
                image.paste(layer.image, (0, 0))
                layer.image = image
                if layer.mask is not None:
                    mask = Image.new("L", (width, height), 255)
                    mask.paste(layer.mask, (0, 0))
                    layer.mask = mask
        if self.selection is not None:
            if resample: self.selection = self.selection.resize((width, height), Image.Resampling.NEAREST)
            else:
                selection = Image.new("L", (width, height))
                selection.paste(self.selection, (0, 0))
                self.selection = selection
        if resample:
            self.metadata["guides_x"] = [round(x * width/old[0]) for x in self.metadata["guides_x"]]
            self.metadata["guides_y"] = [round(y * height/old[1]) for y in self.metadata["guides_y"]]
        self.width, self.height = width, height

    def crop_selection(self):
        if self.selection is None or self.selection.getbbox() is None:
            raise ValueError("Select an area first.")
        box = self.selection.getbbox()
        for layer in self.layers:
            layer.image = layer.image.crop(box)
            if layer.mask is not None: layer.mask = layer.mask.crop(box)
        self.width, self.height = box[2]-box[0], box[3]-box[1]
        self.selection = None
        self.metadata["guides_x"] = [x-box[0] for x in self.metadata["guides_x"] if box[0] <= x < box[2]]
        self.metadata["guides_y"] = [y-box[1] for y in self.metadata["guides_y"] if box[1] <= y < box[3]]
