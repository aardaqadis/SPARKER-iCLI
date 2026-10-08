"""Compare lossless raw/compressed history in matched isolated model processes.

Run: python tools/benchmark_memory.py --size 1024 --steps 8
This measures two editable RGBA layers and undo history, not terminal UI or
another editor. The noise case intentionally supplies incompressible pixels.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time


def _fingerprint(document):
    digest = hashlib.sha256()
    digest.update(str(document.size).encode("ascii"))
    for layer in document.layers:
        digest.update(layer.image.tobytes())
        if layer.mask is not None:
            digest.update(layer.mask.tobytes())
    if document.selection is not None:
        digest.update(document.selection.tobytes())
    digest.update(document.composite().tobytes())
    return digest.hexdigest()


def _worker(size, steps, case, storage):
    # Use source checkout modules even when an older package is installed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from PIL import Image
    from termatelier.diagnostics import _memory_measurement
    from termatelier.model import Document

    document = Document(size, size)
    document.history_storage = storage
    # Keep the identical number of checkpoints in both workers. This isolates
    # compression from the optional profile's smaller undo-step limit.
    document.history_limit = steps
    document.history_bytes = steps * size * size * 8
    if case == "noise":
        rng = random.Random(20261008)
        for layer in document.layers:
            layer.image = Image.frombytes("RGBA", document.size, rng.randbytes(size * size * 4))
    initial = _fingerprint(document)
    gc.collect()
    baseline_bytes, memory_kind, memory_source = _memory_measurement()
    start = time.perf_counter()
    for step in range(steps):
        y = max(0, min(size - 1, round((step + 1) * size / (steps + 1))))
        points = [(max(0, size // 100), y), (max(0, size - 1 - size // 100), min(size - 1, y + size // 100))]
        with document.edit(f"Stroke {step + 1}"):
            document.paint_mask(document.stroke_mask(points, 3),
                                (step * 25 % 256, 101, 211, 255))
    edit_seconds = time.perf_counter() - start
    gc.collect()
    process_bytes, measured_kind, measured_source = _memory_measurement()
    if (measured_kind, measured_source) != (memory_kind, memory_source):
        raise RuntimeError("Process-memory measurement changed during the run.")
    result = {
        "storage": storage, "case": case, "canvas": [size, size], "layers": 2,
        "steps": len(document.undo_stack), "history_pixel_bytes": document.history_memory_bytes,
        "process_bytes": process_bytes, "baseline_process_bytes": baseline_bytes,
        "memory_kind": memory_kind, "memory_source": memory_source,
        "edit_seconds": round(edit_seconds, 6),
    }
    final = _fingerprint(document)
    restored_states = []
    for _ in range(steps):
        if not document.undo():
            raise RuntimeError("A checkpoint was lost before the benchmark's undo verification.")
        restored_states.append(_fingerprint(document))
    if _fingerprint(document) != initial:
        raise RuntimeError("Undo did not restore the exact initial native pixels.")
    for _ in range(steps):
        if not document.redo():
            raise RuntimeError("A checkpoint was lost before the benchmark's redo verification.")
        restored_states.append(_fingerprint(document))
    if _fingerprint(document) != final:
        raise RuntimeError("Redo did not restore the exact final native pixels.")
    result["pixels_sha256"] = final
    result["undo_redo_sha256"] = hashlib.sha256(json.dumps(restored_states).encode("ascii")).hexdigest()
    return result


def run_benchmark(size, steps, case):
    cases = ["strokes", "noise"] if case == "all" else [case]
    measurements = []
    for workload in cases:
        pair = []
        for storage in ("raw", "compressed"):
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--size", str(size),
                 "--steps", str(steps), "--case", workload, "--_worker", storage],
                capture_output=True, text=True, check=True, timeout=120)
            pair.append(json.loads(completed.stdout))
        raw, compressed = pair
        if raw["pixels_sha256"] != compressed["pixels_sha256"]:
            raise RuntimeError("Matched processes produced different native pixels.")
        if raw["undo_redo_sha256"] != compressed["undo_redo_sha256"]:
            raise RuntimeError("Matched processes restored different checkpoint pixels.")
        measurements.append({
            "case": workload, "raw": raw, "compressed": compressed,
            "history_reduction_percent": round(100 * (1 - compressed["history_pixel_bytes"] / raw["history_pixel_bytes"]), 3),
            "process_reduction_percent": (round(100 * (1 - compressed["process_bytes"] / raw["process_bytes"]), 3)
                if raw["process_bytes"] and compressed["process_bytes"] is not None else None),
            "exact_pixels_and_undo_redo": True,
        })
    return {"measurement": "Matched isolated model workers; excludes terminal UI and desktop helpers",
            "platform": sys.platform, "python": sys.version.split()[0], "workloads": measurements}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1024, help="Square canvas side in pixels (1–2048)")
    parser.add_argument("--steps", type=int, default=8, help="Matched undo checkpoints (1–40)")
    parser.add_argument("--case", choices=("all", "strokes", "noise"), default="all")
    parser.add_argument("--_worker", choices=("raw", "compressed"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.size <= 2048 or not 1 <= args.steps <= 40:
        parser.error("Use a canvas size of 1–2048 and 1–40 checkpoints.")
    try:
        result = (_worker(args.size, args.steps, args.case, args._worker) if args._worker
                  else run_benchmark(args.size, args.steps, args.case))
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        parser.exit(1, f"Benchmark failed: {error}\n")
    print(json.dumps(result, indent=None if args._worker else 2))


if __name__ == "__main__":
    main()
