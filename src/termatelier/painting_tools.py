"""Native raster retouching, expressive painting and image-assisted selections.

These are deterministic pixel tools. Foreground selection is seeded color
segmentation, and natural-media tips use the built-in procedural brush engine.
The functions never start transactions: command or mouse callers own undo.
"""
from __future__ import annotations

from functools import lru_cache
import heapq
import math

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

MOUSE_TOOLS = frozenset({"clone", "heal", "perspective-clone", "smudge", "blur", "sharpen",
                         "dodge", "burn", "airbrush", "ink", "natural"})
NATURAL_PRESETS = {
    "chalk": "natural-media.chalk.centered.coarse",
    "charcoal": "natural-media.charcoal.slash.flecked",
    "dry": "natural-media.dry.centered.striated",
    "bristle": "natural-media.bristle.slash.striated",
    "sponge": "natural-media.sponge.centered.flecked",
    "fan": "natural-media.fan.centered.fine",
    "rake": "natural-media.rake.centered.striated",
    "splatter": "natural-media.splatter.centered.flecked",
}
MAX_DABS = 32768
MAX_DAB_PIXELS = 67_108_864


def _number(value, name, low, high):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}.")
    return value


def checked_points(points, minimum=1, maximum=2048):
    points = list(points)
    if not minimum <= len(points) <= maximum:
        raise ValueError(f"Provide {minimum}..{maximum} points.")
    result = []
    for point in points:
        if len(point) != 2:
            raise ValueError("Each point needs x,y.")
        result.append(tuple(_number(v, "Point coordinate", -32768, 32768) for v in point))
    return result


def _bounds(doc, points, padding):
    box = (max(0, math.floor(min(p[0] for p in points) - padding)),
           max(0, math.floor(min(p[1] for p in points) - padding)),
           min(doc.width, math.ceil(max(p[0] for p in points) + padding + 1)),
           min(doc.height, math.ceil(max(p[1] for p in points) + padding + 1)))
    return box if box[0] < box[2] and box[1] < box[3] else None


def _dabs(points, size):
    result = [points[0]]
    spacing = max(.75, size * .2)
    for a, b in zip(points, points[1:]):
        steps = max(1, math.ceil(math.dist(a, b) / spacing))
        if len(result) + steps > MAX_DABS or (len(result) + steps) * size * size > MAX_DAB_PIXELS:
            raise ValueError("Stroke is too long for this brush size; split it into shorter strokes.")
        result.extend((a[0] + (b[0] - a[0]) * i / steps,
                       a[1] + (b[1] - a[1]) * i / steps) for i in range(1, steps + 1))
    return result


@lru_cache(maxsize=64)
def _tip(size, hardness, aspect=1., angle=0.):
    radius = (size - 1) / 2
    ys, xs = np.mgrid[:size, :size].astype(np.float32)
    xs -= radius
    ys -= radius
    theta = math.radians(angle)
    u, v = xs * math.cos(theta) + ys * math.sin(theta), -xs * math.sin(theta) + ys * math.cos(theta)
    distance = np.hypot(u, v / aspect) / max(.5, size / 2)
    if hardness == 1:
        alpha = (distance <= 1).astype(np.float32)
    else:
        alpha = np.clip((1 - distance) / max(.001, 1 - hardness), 0, 1)
    # A one-pixel tip is always a real dab, regardless of angle and aspect.
    if size == 1:
        alpha[:] = 1
    return Image.fromarray(np.rint(alpha * 255).astype(np.uint8))


def _local_mask(doc, points, size, hardness, *, aspect=1., angle=0., tip=None):
    box = _bounds(doc, points, size / 2 + 2)
    if box is None:
        return None, None
    mask = Image.new("L", (box[2] - box[0], box[3] - box[1]))
    tip = tip if tip is not None else _tip(size, hardness, aspect, angle)
    radius = (size - 1) / 2
    for x, y in _dabs(points, size):
        left, top = round(x - radius) - box[0], round(y - radius) - box[1]
        region = (max(0, left), max(0, top), min(mask.width, left + size), min(mask.height, top + size))
        if region[0] >= region[2] or region[1] >= region[3]:
            continue
        footprint = tip.crop((region[0] - left, region[1] - top, region[2] - left, region[3] - top))
        mask.paste(ImageChops.lighter(mask.crop(region), footprint), region[:2])
    return box, mask


def _coverage(doc, mask, box, opacity):
    if doc.selection is not None:
        mask = ImageChops.multiply(mask, doc.selection.crop(box))
    if opacity != 1:
        mask = mask.point([round(v * opacity) for v in range(256)])
    return mask


def _replace(doc, box, replacement, mask, opacity=1., *, preserve_alpha=False):
    mask = _coverage(doc, mask, box, opacity)
    current = doc.layer.image.crop(box)
    if preserve_alpha:
        replacement = replacement.copy()
        replacement.putalpha(current.getchannel("A"))
        blended = Image.composite(replacement, current, mask)
    else:
        # Interpolate premultiplied pixels; hidden RGB never fringes a smear.
        a = np.asarray(current, dtype=np.float32) / 255
        b = np.asarray(replacement, dtype=np.float32) / 255
        weight = np.asarray(mask, dtype=np.float32)[..., None] / 255
        alpha = a[..., 3:] * (1 - weight) + b[..., 3:] * weight
        premult = a[..., :3] * a[..., 3:] * (1 - weight) + b[..., :3] * b[..., 3:] * weight
        rgb = np.divide(premult, alpha, out=np.zeros_like(premult), where=alpha > 0)
        mixed = np.concatenate((rgb, alpha), axis=-1)
        blended = Image.fromarray(np.rint(np.clip(mixed, 0, 1) * 255).astype(np.uint8), "RGBA")
        # Zero coverage must leave even invisible stored RGB byte-identical.
        blended = Image.composite(blended, current, mask.point(lambda v: 255 if v else 0))
    doc.layer.image.paste(blended, box[:2])


def _over(doc, box, source, mask, opacity):
    source = source.copy()
    source.putalpha(ImageChops.multiply(source.getchannel("A"), _coverage(doc, mask, box, opacity)))
    doc.layer.image.paste(Image.alpha_composite(doc.layer.image.crop(box), source), box[:2])


def _blur_rgb(image, radius):
    """Blur premultiplied color while preserving each original pixel's alpha."""
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    premult = Image.merge("RGB", tuple(ImageChops.multiply(c, alpha) for c in rgb.split()))
    blurred = np.asarray(premult.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    blurred_alpha = np.asarray(alpha.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)[..., None]
    color = np.divide(blurred * 255, blurred_alpha, out=np.zeros_like(blurred), where=blurred_alpha > 0)
    result = Image.fromarray(np.rint(np.clip(color, 0, 255)).astype(np.uint8), "RGB").convert("RGBA")
    result.putalpha(alpha)
    return result


def perspective_coefficients(source_quad, dest_quad):
    """Inverse projective mapping for Pillow, validated before touching pixels."""
    source_quad, dest_quad = checked_points(source_quad, 4, 4), checked_points(dest_quad, 4, 4)
    for points in (source_quad, dest_quad):
        area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2
        signs = []
        for i in range(4):
            a, b, c = points[i], points[(i + 1) % 4], points[(i + 2) % 4]
            signs.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
        if area < 1 or not (all(v > 1e-6 for v in signs) or all(v < -1e-6 for v in signs)):
            raise ValueError("Perspective quadrilaterals must be convex, non-crossing and nondegenerate.")
    matrix, values = [], []
    for (x, y), (u, v) in zip(dest_quad, source_quad):
        matrix.extend(((x, y, 1, 0, 0, 0, -u * x, -u * y), (0, 0, 0, x, y, 1, -v * x, -v * y)))
        values.extend((u, v))
    try:
        coefficients = np.linalg.solve(np.array(matrix), np.array(values))
    except np.linalg.LinAlgError as error:
        raise ValueError("Perspective quadrilaterals cannot be mapped.") from error
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("Invalid perspective mapping.")
    return tuple(coefficients)


def _projective_crop(source, box, coefficients):
    a, b, c, d, e, f, g, h = coefficients
    x, y = box[:2]
    denominator = 1 + g * x + h * y
    if abs(denominator) < 1e-9:
        raise ValueError("Perspective mapping crosses an infinite horizon.")
    local = (a / denominator, b / denominator, (a * x + b * y + c) / denominator,
             d / denominator, e / denominator, (d * x + e * y + f) / denominator,
             g / denominator, h / denominator)
    return source.transform((box[2] - box[0], box[3] - box[1]), Image.Transform.PERSPECTIVE,
                            local, Image.Resampling.NEAREST)


def apply_stroke(doc, tool, points, *, size=12, hardness=.8, opacity=1., color="#e79335",
                 strength=.5, source=None, merged=False, source_quad=None, dest_quad=None,
                 radius=2., tonal_range="midtones", exposure=.5, duration=1., rate=12.,
                 angle=30., aspect=.25, preset="chalk", seed=0, density=1., pressure=1.,
                 state=None):
    """Apply one mouse segment or complete CLI stroke and return a dirty region.

    For mouse use, begin one document transaction and reuse ``state`` throughout
    the drag. Clone state freezes the source and anchor at gesture start; smudge
    state carries picked-up pigment from one segment to the next. Opacity is a
    fraction. All coordinates and sizes describe original canvas pixels.
    """
    if tool not in MOUSE_TOOLS:
        raise ValueError(f"Unknown native painting tool: {tool}.")
    points = checked_points(points)
    size = int(_number(size, "Brush size", 1, 512))
    hardness = _number(hardness, "Hardness", 0, 1)
    opacity = _number(opacity, "Opacity", 0, 1)
    strength = _number(strength, "Strength", 0, 1)
    radius = _number(radius, "Radius", .1, 64)
    exposure = _number(exposure, "Exposure", 0, 4)
    duration = _number(duration, "Duration", .01, 60)
    rate = _number(rate, "Rate", .1, 120)
    angle = _number(angle, "Angle", -360, 360)
    aspect = _number(aspect, "Nib aspect", .05, 1)
    pressure = _number(pressure, "Pressure", .05, 1)
    density = _number(density, "Density", .1, 4)
    if tonal_range not in ("shadows", "midtones", "highlights", "all"):
        raise ValueError("Tonal range must be shadows, midtones, highlights or all.")
    rgba = ImageColor.getcolor(color, "RGBA")
    coefficients = None
    if tool in ("clone", "heal"):
        source = source if source is not None else doc.settings.get("clone_source")
        if source is None:
            raise ValueError("Set a source point with --source X,Y (or the mouse source modifier).")
        source = checked_points([source])[0]
        if not 0 <= source[0] < doc.width or not 0 <= source[1] < doc.height:
            raise ValueError("Source point must be inside the canvas.")
    if tool == "perspective-clone":
        coefficients = perspective_coefficients(source_quad or (), dest_quad or ())
    tip = None
    if tool == "natural":
        from .tool_library import get_tool, render_tip
        identifier = NATURAL_PRESETS.get(preset, preset)
        spec = get_tool(identifier)
        if spec.category != "natural-media" or spec.kind != "brush":
            raise ValueError("Use a natural-media brush ID or one of: " + ", ".join(NATURAL_PRESETS))
        tip = render_tip(spec, size, int(seed), angle, density)
    size = max(1, round(size * pressure)) if tool == "ink" else size
    doc.ensure_editable()
    state = state if state is not None else {}
    # Canvas callers may provide the entire growing gesture while other
    # callers provide only [previous, current]. Do not replay old smudge or
    # retouch segments when the full path grows.
    original_points = tuple(points)
    previous = state.get("path", ())
    if previous and original_points[:len(previous)] == previous:
        if len(previous) == len(original_points):
            return None
        points = [previous[-1], *original_points[len(previous):]]
    # Validate the entire dab budget before the first modification.
    dabs = _dabs(points, size)
    state["path"] = original_points
    box, mask = _local_mask(doc, points, size, hardness,
                            aspect=aspect if tool == "ink" else 1., angle=angle, tip=tip)
    if box is None or mask.getbbox() is None or opacity == 0:
        return box
    if tool in ("ink", "natural"):
        doc.paint_mask(mask, rgba, opacity * pressure, box=box)
        return box
    if tool == "airbrush":
        # Timed spray has accumulating flow, including at a stationary point.
        accumulation = Image.new("L", mask.size)
        tip = _tip(size, hardness)
        per_dab = 1 - math.exp(-rate * duration / len(dabs))
        radius_px = (size - 1) / 2
        for x, y in dabs:
            left, top = round(x - radius_px) - box[0], round(y - radius_px) - box[1]
            region = (max(0, left), max(0, top), min(mask.width, left + size), min(mask.height, top + size))
            if region[0] >= region[2] or region[1] >= region[3]:
                continue
            dab = tip.crop((region[0] - left, region[1] - top, region[2] - left, region[3] - top))
            dab = dab.point([round(v * per_dab) for v in range(256)])
            existing = accumulation.crop(region)
            combined = ImageChops.invert(ImageChops.multiply(ImageChops.invert(existing), ImageChops.invert(dab)))
            accumulation.paste(combined, region[:2])
        doc.paint_mask(accumulation, rgba, opacity, box=box)
        return box
    if tool in ("clone", "heal", "perspective-clone"):
        if "source_image" not in state:
            state["source_image"] = doc.composite() if merged else doc.layer.rendered()
            state["anchor"] = points[0]
        frozen = state["source_image"]
        if tool == "perspective-clone":
            sampled = _projective_crop(frozen, box, coefficients)
        else:
            dx, dy = source[0] - state["anchor"][0], source[1] - state["anchor"][1]
            sample_box = tuple(round(v + (dx if i % 2 == 0 else dy)) for i, v in enumerate(box))
            sampled = frozen.crop(sample_box)
        if tool == "heal":
            target = doc.layer.image.crop(box)
            source_mean = np.asarray(_blur_rgb(sampled, radius), dtype=np.float32)[..., :3]
            target_mean = np.asarray(_blur_rgb(target, radius), dtype=np.float32)[..., :3]
            texture = np.asarray(sampled, dtype=np.float32)[..., :3]
            corrected = np.rint(np.clip(texture + target_mean - source_mean, 0, 255)).astype(np.uint8)
            healed = Image.fromarray(corrected, "RGB").convert("RGBA")
            healed.putalpha(target.getchannel("A"))
            valid = ImageChops.multiply(mask, sampled.getchannel("A"))
            _replace(doc, box, healed, valid, opacity, preserve_alpha=True)
        else:
            _over(doc, box, sampled, mask, opacity)
        return box
    if tool == "smudge":
        tip = _tip(size, hardness)
        brush_radius = (size - 1) / 2
        for point in dabs:
            origin = (round(point[0] - brush_radius), round(point[1] - brush_radius))
            dab_box = (origin[0], origin[1], origin[0] + size, origin[1] + size)
            target = doc.layer.image.crop(dab_box)
            if "pickup" not in state:
                state["pickup"] = target.copy()
                continue
            pickup = state["pickup"]
            if pickup.size != target.size:
                raise ValueError("Smudge brush size cannot change during one gesture.")
            clip = (max(0, dab_box[0]), max(0, dab_box[1]), min(doc.width, dab_box[2]), min(doc.height, dab_box[3]))
            if clip[0] < clip[2] and clip[1] < clip[3]:
                offset = (clip[0] - dab_box[0], clip[1] - dab_box[1], clip[2] - dab_box[0], clip[3] - dab_box[1])
                _replace(doc, clip, pickup.crop(offset), tip.crop(offset), opacity * strength)
            # Pick up current pigment after placing the previous sample.
            state["pickup"] = doc.layer.image.crop(dab_box)
        return box
    # Local filters require a halo; their scope remains the circular stroke.
    halo = max(2, math.ceil(radius * 3)) if tool in ("blur", "sharpen") else 0
    expanded = (max(0, box[0] - halo), max(0, box[1] - halo),
                min(doc.width, box[2] + halo), min(doc.height, box[3] + halo))
    original = doc.layer.image.crop(expanded)
    if tool in ("blur", "sharpen"):
        blurred = _blur_rgb(original, radius)
        if tool == "blur":
            processed = blurred
        else:
            a, b = np.asarray(original, dtype=np.float32), np.asarray(blurred, dtype=np.float32)
            a[..., :3] = np.clip(a[..., :3] + (a[..., :3] - b[..., :3]) * 2, 0, 255)
            processed = Image.fromarray(np.rint(a).astype(np.uint8), "RGBA")
    else:
        pixels = np.asarray(original, dtype=np.float32).copy()
        rgb = pixels[..., :3] / 255
        luminance = rgb[..., 0] * .2126 + rgb[..., 1] * .7152 + rgb[..., 2] * .0722
        if tonal_range == "shadows": weight = np.clip((.65 - luminance) / .65, 0, 1)
        elif tonal_range == "highlights": weight = np.clip((luminance - .35) / .65, 0, 1)
        elif tonal_range == "midtones": weight = np.clip(1 - abs(luminance - .5) * 2, 0, 1)
        else: weight = np.ones_like(luminance)
        factor = exposure * weight[..., None]
        result = 1 - (1 - rgb) * np.exp(-factor) if tool == "dodge" else rgb * np.exp(-factor)
        pixels[..., :3] = np.rint(result * 255)
        processed = Image.fromarray(pixels.astype(np.uint8), "RGBA")
    crop = (box[0] - expanded[0], box[1] - expanded[1], box[2] - expanded[0], box[3] - expanded[1])
    _replace(doc, box, processed.crop(crop), mask, opacity * strength, preserve_alpha=True)
    return box


def color_selection(doc, point=None, *, color=None, tolerance=20, merged=False, feather=0.):
    """Select matching RGBA throughout the image, including disconnected islands."""
    tolerance = _number(tolerance, "Tolerance", 0, 255)
    feather = _number(feather, "Feather", 0, 64)
    source = doc.composite() if merged else doc.layer.rendered()
    if color is None:
        x, y = checked_points([point])[0]
        x, y = round(x), round(y)
        if not 0 <= x < doc.width or not 0 <= y < doc.height:
            raise ValueError("Sample point must be inside the canvas.")
        color = source.getpixel((x, y))
    else:
        color = ImageColor.getcolor(color, "RGBA")
    diff = ImageChops.difference(source, Image.new("RGBA", source.size, color))
    channels = diff.split()
    distance = channels[0]
    for channel in channels[1:]:
        distance = ImageChops.lighter(distance, channel)
    mask = distance.point([255 if value <= tolerance else 0 for value in range(256)])
    return mask.filter(ImageFilter.GaussianBlur(feather)) if feather else mask


def _edge_path(cost, start, end):
    """Eight-neighbor A* inside a bounded image; edge cost is always positive."""
    height, width = cost.shape
    margin = max(12, round(math.dist(start, end) * .35))
    left, right = max(0, min(start[0], end[0]) - margin), min(width - 1, max(start[0], end[0]) + margin)
    top, bottom = max(0, min(start[1], end[1]) - margin), min(height - 1, max(start[1], end[1]) + margin)
    initial = start[1] * width + start[0]
    target = end[1] * width + end[0]
    best, parent = {initial: 0.}, {}
    queue = [(math.dist(start, end), 0., initial)]
    while queue:
        _, distance, index = heapq.heappop(queue)
        if distance != best.get(index):
            continue
        if index == target:
            path = [end]
            while index != initial:
                index = parent[index]
                path.append((index % width, index // width))
            return path[::-1]
        x, y = index % width, index // width
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = x + dx, y + dy
            if not left <= nx <= right or not top <= ny <= bottom:
                continue
            following = ny * width + nx
            step = (float(cost[y, x]) + float(cost[ny, nx])) * .5 * (math.sqrt(2) if dx and dy else 1)
            candidate = distance + step
            if candidate < best.get(following, math.inf):
                best[following], parent[following] = candidate, index
                heapq.heappush(queue, (candidate + math.hypot(end[0] - nx, end[1] - ny), candidate, following))
    return [start, end]


def scissors_selection(doc, points, *, merged=False, resolution=256, feather=0.):
    """Edge-following polygon, solved at a bounded resolution then rasterized."""
    points = checked_points(points, 3, 32)
    resolution = int(_number(resolution, "Working resolution", 32, 512))
    feather = _number(feather, "Feather", 0, 64)
    for x, y in points:
        if not 0 <= x < doc.width or not 0 <= y < doc.height:
            raise ValueError("Scissors anchors must be inside the canvas.")
    factor = min(1, resolution / max(doc.size), math.sqrt(65536 / (doc.width * doc.height)))
    size = (max(1, round(doc.width * factor)), max(1, round(doc.height * factor)))
    image = (doc.composite() if merged else doc.layer.rendered()).resize(size, Image.Resampling.BOX)
    pixels = np.asarray(image, dtype=np.float32)
    pixels[..., :3] *= pixels[..., 3:] / 255
    padded = np.pad(pixels, ((1, 1), (1, 1), (0, 0)), mode="edge")
    gx = padded[1:-1, 2:] - padded[1:-1, :-2]
    gy = padded[2:, 1:-1] - padded[:-2, 1:-1]
    magnitude = np.sqrt(np.sum(gx * gx + gy * gy, axis=-1))
    edge = magnitude / max(1, float(magnitude.max()))
    cost = 1 + 12 * (1 - edge) ** 2
    sx, sy = size[0] / doc.width, size[1] / doc.height
    anchors = [(min(size[0] - 1, round(x * sx)), min(size[1] - 1, round(y * sy))) for x, y in points]
    path = []
    for start, end in zip(anchors, anchors[1:] + anchors[:1]):
        path.extend(_edge_path(cost, start, end))
    mask = Image.new("L", doc.size)
    original_path = [(min(doc.width - 1, round(x / sx)), min(doc.height - 1, round(y / sy))) for x, y in path]
    ImageDraw.Draw(mask).polygon(original_path, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(feather)) if feather else mask


def foreground_selection(doc, outline, marks, *, merged=False, tolerance=80, smooth=0.):
    """Seeded color segmentation inside a rough outline, without an AI model.

    Foreground marks define real RGBA prototypes. Background prototypes are a
    deterministic sample outside the polygon; empty backgrounds need only the
    foreground tolerance. Classification runs in 64-row blocks at native size.
    """
    outline, marks = checked_points(outline, 3, 64), checked_points(marks, 1, 32)
    tolerance = _number(tolerance, "Tolerance", 0, 255)
    smooth = _number(smooth, "Smooth", 0, 8)
    source = doc.composite() if merged else doc.layer.rendered()
    polygon = Image.new("L", doc.size)
    ImageDraw.Draw(polygon).polygon(outline, fill=255)
    foreground = []
    for x, y in marks:
        point = round(x), round(y)
        if not 0 <= point[0] < doc.width or not 0 <= point[1] < doc.height or not polygon.getpixel(point):
            raise ValueError("Foreground marks must be inside the outline and canvas.")
        pixel = source.getpixel(point)
        if pixel[3] == 0:
            raise ValueError("Foreground marks must contain visible pixels.")
        foreground.append(pixel[:3])
    # Work with at most 24 distinct outside colors, not an NxK canvas tensor.
    small = source.resize((min(64, doc.width), min(64, doc.height)), Image.Resampling.NEAREST)
    outside = polygon.resize(small.size, Image.Resampling.NEAREST)
    counts = {}
    for pixel, coverage in zip(np.asarray(small).reshape(-1, 4), np.asarray(outside).flat):
        if not coverage and pixel[3]:
            quantized = tuple((v // 16) * 16 + 8 for v in pixel[:3])
            counts[quantized] = counts.get(quantized, 0) + 1
    background = sorted(counts, key=lambda value: (-counts[value], value))[:24]
    result = Image.new("L", doc.size)
    threshold = tolerance * tolerance * 3
    for top in range(0, doc.height, 64):
        box = (0, top, doc.width, min(doc.height, top + 64))
        pixels = np.asarray(source.crop(box), dtype=np.float32)
        rgb = pixels[..., :3]
        distance_fg = np.full(rgb.shape[:2], np.inf, dtype=np.float32)
        for prototype in foreground:
            distance_fg = np.minimum(distance_fg, np.sum((rgb - prototype) ** 2, axis=-1))
        eligible = distance_fg <= threshold
        if background:
            distance_bg = np.full_like(distance_fg, np.inf)
            for prototype in background:
                distance_bg = np.minimum(distance_bg, np.sum((rgb - prototype) ** 2, axis=-1))
            eligible &= distance_fg < distance_bg
        eligible &= pixels[..., 3] > 0
        eligible &= np.asarray(polygon.crop(box)) > 0
        result.paste(Image.fromarray((eligible * 255).astype(np.uint8)), box[:2])
    if smooth:
        result = ImageChops.multiply(result.filter(ImageFilter.GaussianBlur(smooth)), polygon)
    return result
