"""Measure the real terminal Canvas cold fit and warmed drawing/view paths."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from types import SimpleNamespace

from PIL import Image
from textual.geometry import Size

from termatelier.canvas import Canvas
from termatelier.model import Document, Layer


class BenchmarkCanvas(Canvas):
    @property
    def app(self): return self._test_app

    @property
    def size(self): return Size(100, 35)


def median_ms(function, samples):
    values = []
    for _ in range(samples):
        start = time.perf_counter()
        function()
        values.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(values), 3)


def benchmark(size, samples):
    doc = Document(size, size)
    doc.layers += [Layer(str(n), Image.new("RGBA", doc.size, (n*31, 123, 99, 30)))
                   for n in range(1, 5)]
    doc.active = 1
    canvas = BenchmarkCanvas()
    canvas._test_app = SimpleNamespace(doc=doc, config=None, tool="brush",
        brush_size=30, hardness=.5, opacity=.5, foreground="#e79335",
        filled=False, update_status=lambda *args: None)
    canvas.zoom = 70/size

    def cold_fit():
        canvas.invalidate()
        canvas.screen_image()

    report = {"canvas_pixels": doc.size, "layers": len(doc.layers),
              "display_pixels": (100, 70), "zoom": canvas.zoom}
    report["cold_fit_ms"] = median_ms(cold_fit, samples)
    report["warm_fit_ms"] = median_ms(canvas.screen_image, samples)
    for mode in ("nearest", "bilinear", "bicubic"):
        canvas.set_resampling(mode)
        def pan():
            canvas.pan_x += .75
            canvas.invalidate(artwork=False)
            canvas.screen_image()
        report[f"warm_{mode}_pan_ms"] = median_ms(pan, samples)

    canvas.set_resampling("nearest")
    doc.begin("Benchmark stroke")
    canvas.base = doc.pending[1][1][doc.active].image
    canvas.origin = (size//4, size//4)
    update, frame = [], []
    for index in range(40):
        point = (canvas.origin[0]+index*13, canvas.origin[1]+index*7)
        canvas.points.append(point)
        start = time.perf_counter()
        canvas.update_gesture(point)
        update.append((time.perf_counter()-start)*1000)
        start = time.perf_counter()
        canvas.screen_image()
        frame.append((time.perf_counter()-start)*1000)
    report["warm_brush_edit_ms"] = round(statistics.median(update), 3)
    report["warm_brush_frame_ms"] = round(statistics.median(frame), 3)
    report["brush_40_edits_and_frames_ms"] = round(sum(update)+sum(frame), 3)
    doc.cancel()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=2048)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if args.size < 640 or args.samples < 1:
        parser.error("size must be at least 640 and samples at least 1")
    print(json.dumps(benchmark(args.size, args.samples), indent=2))
