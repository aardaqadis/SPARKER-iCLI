"""The detached window keeps full raster frames and owns a bounded lifecycle."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PIL import Image
import pytest

from termatelier.config import RuntimeConfig
from termatelier.model import Document
from termatelier.preview import PreviewController, atomic_json
from termatelier.preview_window import checkerboard, read_snapshot
from termatelier.startup import parent_alive


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.pid = 888888

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def config(tmp_path, **settings):
    values = {"preview.enabled": True, "preview.width": 480, "preview.height": 320,
              "preview.refresh_ms": 50, "preview.resampling": "nearest"}
    values.update(settings)
    return RuntimeConfig(values, path=tmp_path / "preferences.json")


def make_document():
    document = Document(31, 19)
    document.layers[0].visible = False
    document.layer.image = Image.frombytes("RGBA", document.size,
        bytes((i * 37 + i // 3) % 256 for i in range(31 * 19 * 4)))
    return document


def wait_until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate(), "Preview lifecycle did not reach the expected state"


def window_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def native_available():
    return os.name == "nt" or bool(os.environ.get("DISPLAY"))


def test_preview_preserves_full_rgba_and_uses_external_disposable_files(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    document = make_document()
    expected = document.composite().tobytes()
    controller = PreviewController(config(tmp_path))
    try:
        assert controller.start(document, {"source": "test.tart"})
        state_path = controller.state_path
        assert state_path.parent.parent == Path(tempfile.gettempdir()).resolve()
        assert Path.cwd() not in state_path.parents
        state, frame = read_snapshot(state_path)
        assert frame.size == (31, 19)
        assert frame.mode == "RGBA"
        assert frame.tobytes() == expected
        assert state["metadata"]["source"] == "test.tart"
        assert state["options"]["resampling"] == "nearest"
        assert document.composite().tobytes() == expected
    finally:
        controller.close()
    assert not state_path.parent.exists()
    assert not controller.running


def test_preview_update_undo_replacement_and_generation_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    document = make_document()
    controller = PreviewController(config(tmp_path))
    try:
        assert controller.start(document)
        before = document.composite().tobytes()
        document.begin("paint")
        document.layer.image.putpixel((3, 4), (255, 0, 60, 255))
        document.commit()
        assert controller.update(document)
        state, frame = read_snapshot(controller.state_path)
        assert state["generation"] == 2
        assert frame.getpixel((3, 4)) == (255, 0, 60, 255)
        assert state["metadata"]["dirty"]
        document.undo()
        assert controller.update(document)
        assert read_snapshot(controller.state_path)[1].tobytes() == before
        replacement = Document(53, 37)
        assert controller.update(replacement)
        assert read_snapshot(controller.state_path)[1].size == replacement.size
        for _ in range(5):
            assert controller.update(replacement)
        assert len(list(controller.state_path.parent.glob("frame-*.png"))) == 3
    finally:
        controller.close()


def test_read_only_update_reuses_pixels_and_publishes_metadata_and_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    document = make_document()
    controller = PreviewController(config(tmp_path))
    try:
        assert controller.start(document, {"source": "before.tart"})
        before = json.loads(controller.state_path.read_text(encoding="utf-8"))
        before_stamp = controller.state_path.stat().st_mtime_ns
        def unexpected_composite():
            pytest.fail("A read-only update must not composite full-resolution pixels")
        monkeypatch.setattr(document, "composite", unexpected_composite)
        for _ in range(10):
            assert controller.update(document, {"source": "before.tart"}, artwork_changed=False)
        assert controller.state_path.stat().st_mtime_ns == before_stamp
        assert json.loads(controller.state_path.read_text(encoding="utf-8")) == before
        controller.config._values["preview.width"] = 560
        document.saved_revision = document.revision
        assert controller.update(document, {"source": "after.tart"}, artwork_changed=False)
        after, frame = read_snapshot(controller.state_path)
        assert after["generation"] == before["generation"] + 1
        assert after["frame"] == before["frame"]
        assert after["metadata"]["source"] == "after.tart"
        assert after["options"]["width"] == 560
        assert frame.size == document.size
        assert len(controller._frames) == 1
    finally:
        controller.close()


@pytest.mark.parametrize("change", ["revision", "image", "mask", "opacity", "blend", "visible", "order", "replacement"])
def test_artwork_signature_rejects_stale_reuse(tmp_path, monkeypatch, change):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    document = make_document()
    controller = PreviewController(config(tmp_path))
    try:
        assert controller.start(document)
        before = json.loads(controller.state_path.read_text(encoding="utf-8"))
        if change == "revision":
            with document.edit("paint"):
                document.layer.image.putpixel((3, 4), (123, 45, 67, 255))
        elif change == "image":
            document.layer.image = Image.new("RGBA", document.size, (123, 45, 67, 255))
        elif change == "mask":
            document.layer.mask = Image.new("L", document.size, 100)
        elif change == "opacity":
            document.layer.opacity = 0.4
        elif change == "blend":
            document.layer.blend = "multiply"
        elif change == "visible":
            document.layer.visible = False
        elif change == "order":
            document.layers.reverse()
        elif change == "replacement":
            document = Document(43, 27)
        assert controller.update(document, artwork_changed=False)
        after, frame = read_snapshot(controller.state_path)
        assert after["generation"] == before["generation"] + 1
        assert after["frame"] != before["frame"]
        assert frame.tobytes() == document.composite().tobytes()
        assert frame.size == document.size
    finally:
        controller.close()


def test_large_preview_is_exact_rgba_and_read_only_updates_are_constant_work(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    document = Document(1024, 1024)
    document.layers[0].visible = False
    sample = Image.frombytes("RGBA", (256, 256),
                             bytes((i * 37 + i // 3) % 256 for i in range(256 * 256 * 4)))
    for y in range(0, 1024, 256):
        for x in range(0, 1024, 256):
            document.layer.image.paste(sample, (x, y))
    expected = document.composite().tobytes()
    controller = PreviewController(config(tmp_path))
    try:
        assert controller.start(document)
        _, frame = read_snapshot(controller.state_path)
        assert frame.size == document.size
        assert frame.tobytes() == expected
        def unexpected_composite():
            pytest.fail("Canvas dimensions must not affect a read-only update's pixel work")
        monkeypatch.setattr(document, "composite", unexpected_composite)
        assert controller.update(document, artwork_changed=False)
        assert len(controller._frames) == 1
    finally:
        controller.close()


def test_disable_close_and_user_closed_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    settings = config(tmp_path)
    controller = PreviewController(settings)
    document = make_document()
    try:
        assert controller.start(document)
        original = controller.state_path
        controller._process.returncode = 0
        assert not controller.running
        assert controller.start(document)
        assert controller.state_path != original
        assert not original.parent.exists()
        settings._values["preview.enabled"] = False
        assert not controller.update(document)
        assert not controller.running
        assert not controller.start(document)
    finally:
        controller.close()


def test_helper_start_failure_is_nonfatal(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("interpreter unavailable")
    monkeypatch.setattr(subprocess, "Popen", fail)
    controller = PreviewController(config(tmp_path))
    assert not controller.start(make_document())
    assert "interpreter unavailable" in controller.error
    assert not controller.running
    assert controller.state_path is None


def test_snapshot_rejects_paths_and_dimension_mismatch(tmp_path):
    image = Image.new("RGBA", (7, 11), (8, 9, 10, 11))
    image.save(tmp_path / "frame-1.png")
    path = tmp_path / "state.json"
    atomic_json(path, {"frame": "../outside.png", "width": 7, "height": 11})
    with pytest.raises(ValueError, match="Invalid preview"):
        read_snapshot(path)
    atomic_json(path, {"frame": "frame-1.png", "width": 11, "height": 7})
    with pytest.raises(ValueError, match="dimensions"):
        read_snapshot(path)


def test_checkerboard_visible_alpha_is_display_only():
    source = Image.new("RGBA", (27, 15), (15, 20, 25, 0))
    before = source.tobytes()
    display = Image.alpha_composite(checkerboard(source.size), source)
    assert display.getpixel((0, 0)) != display.getpixel((12, 0))
    assert all(alpha == 255 for alpha in display.getchannel("A").tobytes())
    assert source.tobytes() == before


@pytest.mark.skipif(not native_available(), reason="No native window display available")
def test_real_native_preview_updates_closes_and_reopens(tmp_path):
    document = make_document()
    controller = PreviewController(config(tmp_path))
    try:
        assert controller.start(document)
        process = controller._process
        status_path = controller.state_path.parent / "window.json"
        wait_until(lambda: window_state(status_path).get("phase") == "ready" or not controller.running)
        assert controller.running, controller.error
        assert window_state(status_path)["size"] == [31, 19]
        assert window_state(status_path)["viewable"]
        assert window_state(status_path)["window_size"] == [480, 320]
        assert not window_state(status_path)["borderless"]
        assert window_state(status_path)["focusable"]
        if os.name == "nt":
            assert window_state(status_path)["shown_without_activation"]
            assert not window_state(status_path)["activated_on_show"]
            assert window_state(status_path)["activation_guard_released"]
        document.layer.image.putpixel((0, 0), (123, 45, 67, 255))
        assert controller.update(document)
        wait_until(lambda: window_state(status_path).get("generation") == 2)
        assert read_snapshot(controller.state_path)[1].getpixel((0, 0)) == (123, 45, 67, 255)
        controller.config._values.update({"preview.width": 560, "preview.height": 360,
                                          "preview.topmost": True})
        before_frame = json.loads(controller.state_path.read_text(encoding="utf-8"))["frame"]
        assert controller.update(document, artwork_changed=False)
        wait_until(lambda: window_state(status_path).get("generation") == 3)
        assert window_state(status_path)["window_size"] == [560, 360]
        assert window_state(status_path)["topmost"]
        assert json.loads(controller.state_path.read_text(encoding="utf-8"))["frame"] == before_frame
        state_path = controller.state_path
        controller.close()
        assert process.poll() == 0
        assert not state_path.parent.exists()
        assert controller.reopen(Document(47, 29))
        status_path = controller.state_path.parent / "window.json"
        wait_until(lambda: window_state(status_path).get("phase") == "ready" or not controller.running)
        assert controller.running, controller.error
        assert window_state(status_path)["size"] == [47, 29]
        assert window_state(status_path)["focusable"]
        if os.name == "nt":
            assert not window_state(status_path)["activated_on_show"]
    finally:
        controller.close()


@pytest.mark.skipif(not native_available(), reason="No native window display available")
def test_native_helper_exits_when_parent_disappears(tmp_path):
    script = """import json, os, sys\nfrom termatelier.model import Document\nfrom termatelier.preview import PreviewController\ncontroller = PreviewController()\ncontroller.start(Document(19, 23))\nprint(json.dumps({'pid': controller._process.pid, 'state': str(controller.state_path)}), flush=True)\nsys.stdin.readline()\nos._exit(0)\n"""
    host = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        details = json.loads(host.stdout.readline())
        child_pid = details["pid"]
        directory = Path(details["state"]).parent
        wait_until(lambda: window_state(directory / "window.json").get("phase") == "ready")
        host.stdin.write("exit\n")
        host.stdin.flush()
        assert host.wait(timeout=3) == 0
        wait_until(lambda: not parent_alive(child_pid))
        assert not directory.exists()
    finally:
        if host.poll() is None:
            host.kill()
            host.wait(timeout=3)
        if child_pid is not None:
            wait_until(lambda: not parent_alive(child_pid))
