"""Reproduce large-canvas raster timings against the pre-fix algorithms.

Run with the project's Python environment. Every compared operation checks
exact pixels first. Results are median milliseconds, not total editor FPS.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import math
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

for parent in Path(__file__).resolve().parents:
    for source in (parent / "src", parent / "Termatelier" / "src"):
        if (source / "termatelier" / "model.py").is_file():
            sys.path.insert(0, str(source))
            break
    else:
        continue
    break

from PIL import Image, ImageChops, ImageColor, ImageOps
from termatelier.model import Document, composite_layer


def legacy_composite(doc):
    result = Image.new("RGBA", doc.size)
    for layer in doc.layers:
        if not layer.visible:
            continue
        image = layer.image.copy()
        alpha = image.getchannel("A")
        if layer.mask is not None:
            alpha = ImageChops.multiply(alpha, layer.mask)
        image.putalpha(alpha.point(lambda value: round(value * layer.opacity)))
        result = composite_layer(result, image, layer.blend)
    return result


def legacy_paint(doc, mask, color, opacity, base):
    mask = doc.clipped(mask).point(lambda value: round(value * opacity))
    ink = Image.new("RGBA", doc.size, color)
    ink.putalpha(ImageChops.multiply(ink.getchannel("A"), mask))
    return Image.alpha_composite(base, ink)


def legacy_region(doc, point):
    mask = Image.new("L", doc.size)
    x, y = point
    pixels, out = doc.layer.image.load(), mask.load()
    target = pixels[x, y]
    visited = bytearray(doc.width * doc.height)
    queue = deque([point])
    while queue:
        x, y = queue.popleft()
        index = y * doc.width + x
        if visited[index]:
            continue
        visited[index] = 1
        if max(abs(a-b) for a, b in zip(pixels[x, y], target)) > 0:
            continue
        out[x, y] = 255
        if x:
            queue.append((x-1, y))
        if y:
            queue.append((x, y-1))
        if x+1 < doc.width:
            queue.append((x+1, y))
        if y+1 < doc.height:
            queue.append((x, y+1))
    return mask


def legacy_horizontal_gradient(doc):
    a = ImageColor.getcolor("#e7933540", "RGBA")
    b = ImageColor.getcolor("#5a8deefa", "RGBA")
    dx, length = doc.width - 1, max(1, (doc.width - 1) ** 2)
    pixels = bytearray(doc.width * doc.height * 4)
    offset = 0
    for y in range(doc.height):
        for x in range(doc.width):
            t = max(0, min(1, (x * dx) / length))
            for channel, (v, w) in enumerate(zip(a, b)):
                pixels[offset + channel] = round(v * (1-t) + w * t)
            offset += 4
    ink = Image.frombytes("RGBA", doc.size, bytes(pixels))
    ink.putalpha(ImageChops.multiply(ink.getchannel("A"), Image.new("L", doc.size, round(255 * .73))))
    return Image.alpha_composite(doc.layer.image, ink)


def measured(fn, samples):
    times = []
    for _ in range(samples):
        start = perf_counter()
        fn()
        times.append((perf_counter() - start) * 1000)
    return round(median(times), 3)


def compare(before, after, samples):
    return {"before_ms": measured(before, samples), "after_ms": measured(after, samples)}


def raster_benchmark(side, samples):
    doc = Document(side, side)
    for i in range(4):
        doc.add_layer(str(i), Image.new("RGBA", doc.size, (50+i*30, 60, 180, 125)))
        doc.layer.opacity = .7
        doc.layer.blend = "multiply" if i % 2 else "normal"
        doc.layer.mask = Image.new("L", doc.size, 180)
    doc.active = 1
    points = [(side // 2 + i, side // 2 + i // 2) for i in range(100)]
    base = doc.layer.image.copy()
    mask = doc.stroke_mask(points, 9, .6)
    box = mask.getbbox()
    local = mask.crop(box)
    full = legacy_composite(doc)
    assert full.tobytes() == doc.composite().tobytes()
    view_size = (160, 100)
    pan = (1, 0, side // 2 - 80, 0, 1, side // 2 - 50)
    fit = (side / 160, 0, 0, 0, side / 100, 0)
    for matrix in (pan, fit):
        assert doc.composite_view(view_size, matrix).tobytes() == full.transform(
            view_size, Image.Transform.AFFINE, matrix, Image.Resampling.NEAREST).tobytes()
    expected = legacy_paint(doc, mask, "#e79335", .6, base)
    doc.paint_mask(mask, "#e79335", .6, base=base)
    assert doc.layer.image.tobytes() == expected.tobytes()
    doc.layer.image = base.copy()
    doc.paint_mask(local, "#e79335", .6, base=base, box=box)
    assert doc.layer.image.tobytes() == expected.tobytes()
    return {
        "six_layer_composite": compare(lambda: legacy_composite(doc), doc.composite, samples),
        "six_layer_pan_view": compare(
            lambda: legacy_composite(doc).transform(view_size, Image.Transform.AFFINE, pan, Image.Resampling.NEAREST),
            lambda: doc.composite_view(view_size, pan), samples),
        "six_layer_fit_view": compare(
            lambda: legacy_composite(doc).transform(view_size, Image.Transform.AFFINE, fit, Image.Resampling.NEAREST),
            lambda: doc.composite_view(view_size, fit), samples),
        "full_mask_stroke_paint": compare(lambda: legacy_paint(doc, mask, "#e79335", .6, base),
                                          lambda: doc.paint_mask(mask, "#e79335", .6, base=base), samples),
        "local_stroke_paint": compare(lambda: legacy_paint(doc, mask, "#e79335", .6, base),
                                      lambda: doc.paint_mask(local, "#e79335", .6, base=base, box=box), samples),
        "snapshot_ms": measured(doc.snapshot, samples),
    }


def bulk_benchmark(side, samples):
    doc = Document(side, side)
    point = (side // 2, side // 2)
    assert legacy_region(doc, point).tobytes() == doc.region_mask(point).tobytes()
    base = doc.layer.image.copy()
    expected = legacy_horizontal_gradient(doc)
    doc.gradient((0, 0), (side-1, 0), "#e7933540", "#5a8deefa", .73)
    assert doc.layer.image.tobytes() == expected.tobytes()
    doc.layer.image = base
    def old_gradient():
        doc.layer.image = base
        return legacy_horizontal_gradient(doc)
    def new_gradient():
        doc.layer.image = base
        doc.gradient((0, 0), (side-1, 0), "#e7933540", "#5a8deefa", .73)
    return {"uniform_fill": compare(lambda: legacy_region(doc, point), lambda: doc.region_mask(point), samples),
            "horizontal_gradient": compare(old_gradient, new_gradient, samples)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="512,1024,2048")
    parser.add_argument("--bulk-sizes", default="256,512")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"samples": args.samples, "viewport_size": [160, 100], "exact_pixels_verified": True,
              "raster": {str(side): raster_benchmark(side, args.samples) for side in map(int, args.sizes.split(","))},
              "bulk": {str(side): bulk_benchmark(side, args.samples) for side in map(int, args.bulk_sizes.split(","))}}
    encoded = json.dumps(result, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
