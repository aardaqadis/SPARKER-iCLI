"""Native, read-only painting preview for the full-screen command workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from PIL import Image, ImageDraw

from .preview import atomic_json
from .startup import parent_alive


def read_snapshot(path):
    """Read one complete generation; never resample or recolor stored pixels."""
    state = json.loads(path.read_text(encoding="utf-8"))
    name = state["frame"]
    if not isinstance(name, str) or Path(name).name != name or not name.startswith("frame-"):
        raise ValueError("Invalid preview frame")
    with Image.open(path.parent / name) as opened:
        image = opened.convert("RGBA")
    if image.size != (state["width"], state["height"]):
        raise ValueError("Preview dimensions do not match the frame")
    return state, image


def checkerboard(size, cell=12):
    image = Image.new("RGBA", size, "#252525")
    drawing = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                drawing.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#343434")
    return image


class StartupFocusGuard:
    """Veto only this helper thread's activation while Tk creates its frame.

    Tk's first-frame creation can call SetActiveWindow before the frame exists
    for style changes. A short-lived, thread-specific CBT hook covers that gap.
    It is removed before the preview becomes interactive; it is never global.
    """

    def __init__(self):
        self.handle = None
        self._callback = None
        self._user32 = None
        self.blocked_activations = 0

    def install(self):
        import os
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        @callback_type
        def veto_initial_focus(code, window, details):
            if code in (5, 9):  # HCBT_ACTIVATE and HCBT_SETFOCUS, this thread only.
                self.blocked_activations += 1
                return 1
            return user32.CallNextHookEx(self.handle, code, window, details)

        thread_id = kernel32.GetCurrentThreadId()
        if not thread_id:
            raise OSError("The preview GUI thread could not be identified")
        self._user32 = user32
        self._callback = veto_initial_focus  # Keep the native callback alive.
        self.handle = user32.SetWindowsHookExW(5, self._callback, None, thread_id)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "The preview focus guard could not start")

    def close(self):
        if self.handle is not None:
            if not self._user32.UnhookWindowsHookEx(self.handle):
                import ctypes
                raise OSError(ctypes.get_last_error(), "The preview focus guard could not stop")
            self.handle = None


def show_without_activation(root, focus_guard=None):
    """Show our own Windows window without taking the CLI's keyboard focus.

    NOACTIVATE is temporary: after initial display the preview remains a normal
    window that the user can click and operate with its own keyboard shortcuts.
    """
    import os
    if os.name != "nt":
        root.deiconify()
        root.update_idletasks()
        if focus_guard is not None:
            focus_guard.close()
        return {"shown_without_activation": False, "focusable": True}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    get_style = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
    set_style = user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.SetWindowLongW
    get_style.argtypes = (wintypes.HWND, ctypes.c_int)
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    set_style.restype = ctypes.c_ssize_t
    root.update_idletasks()
    handle = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT: our Tk window's frame.
    if not handle:
        root.deiconify()
        root.update_idletasks()
        if focus_guard is not None:
            focus_guard.close()
        return {"shown_without_activation": False, "focusable": True}
    previous = get_style(handle, -20)  # GWL_EXSTYLE
    no_activate = 0x08000000  # WS_EX_NOACTIVATE
    applied = False
    try:
        set_style(handle, -20, previous | no_activate)
        applied = bool(get_style(handle, -20) & no_activate)
        root.deiconify()
        user32.ShowWindow(handle, 4)  # SW_SHOWNOACTIVATE; never manipulate another window.
        root.update_idletasks()
    finally:
        set_style(handle, -20, previous)
        if focus_guard is not None:
            focus_guard.close()
    return {"shown_without_activation": applied,
            "focusable": not bool(get_style(handle, -20) & no_activate),
            "activated_on_show": user32.GetForegroundWindow() == handle,
            "activation_guard_released": focus_guard is None or focus_guard.handle is None,
            "creation_activations_blocked": focus_guard.blocked_activations if focus_guard else 0}


def show_preview(state_path, parent_pid):
    focus_guard = StartupFocusGuard()
    try:
        focus_guard.install()
        return _run_preview(state_path, parent_pid, focus_guard)
    finally:
        focus_guard.close()


def _run_preview(state_path, parent_pid, focus_guard):
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.withdraw()
    root.title("SPARKER iCLI — Preview")
    root.configure(background="#101010")
    root.minsize(240, 180)
    canvas = tk.Canvas(root, background="#101010", borderwidth=0, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    status = tk.Label(root, background="#101010", foreground="#b5b5b5",
                      font=("Consolas", 10), anchor="w", padx=10, pady=6)
    status.pack(fill="x")
    image = None
    photo = None
    generation = -1
    frame_name = None
    scale = None  # None means fit; numeric scales are relative to original pixels.
    pan = [0.0, 0.0]
    drag = None
    options = {}
    metadata = {}
    initial_display = {}
    closed = False

    def render():
        nonlocal photo
        if image is None or closed:
            return
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        factor = scale if scale is not None else min((width - 24) / image.width,
                                                    (height - 24) / image.height)
        factor = max(0.01, factor)
        target = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
        # Crop to the visible painting before scaling. A large actual-pixel zoom
        # must not allocate an enormous image for pixels outside the window.
        origin = ((width - target[0]) / 2 + pan[0], (height - target[1]) / 2 + pan[1])
        left = max(0, int(-origin[0] / factor))
        top = max(0, int(-origin[1] / factor))
        right = min(image.width, int((width - origin[0]) / factor) + 2)
        bottom = min(image.height, int((height - origin[1]) / factor) + 2)
        canvas.delete("painting")
        if right > left and bottom > top:
            crop = image.crop((left, top, right, bottom))
            method = {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR,
                      "bicubic": Image.Resampling.BICUBIC}.get(options.get("resampling"), Image.Resampling.BILINEAR)
            display = crop.resize((max(1, round(crop.width * factor)),
                                   max(1, round(crop.height * factor))), method)
            display = Image.alpha_composite(checkerboard(display.size), display)
            photo = ImageTk.PhotoImage(display)
            canvas.create_image(origin[0] + left * factor, origin[1] + top * factor,
                                image=photo, anchor="nw", tags="painting")
        dirty = " • edited" if metadata.get("dirty") else ""
        status.configure(text=f"{image.width} × {image.height} px  ·  {factor:.0%}  ·  "
                              f"{metadata.get('layers', '?')} layers{dirty}   |   F fit · 1 actual · wheel zoom · drag pan")

    def finish():
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            atomic_json(state_path.parent / "window.json", {"phase": "closed"})
        except OSError:
            pass
        root.destroy()

    def poll():
        nonlocal image, generation, frame_name, options, metadata, initial_display
        if not parent_alive(parent_pid) or (state_path.parent / "closed").exists():
            finish()
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("generation") != generation:
                # Settings/save-status updates can share the last raster frame.
                # Decoding a megapixel PNG again would add latency to a change
                # that never touched the painting.
                if image is None or state.get("frame") != frame_name:
                    state, image = read_snapshot(state_path)
                    frame_name = state["frame"]
                elif image.size != (state["width"], state["height"]):
                    raise ValueError("Preview dimensions do not match the frame")
                first = generation == -1
                previous_size = (options.get("width"), options.get("height"))
                generation = state["generation"]
                options = state.get("options", {})
                metadata = state.get("metadata", {})
                root.title(f"SPARKER iCLI — Preview · {metadata.get('title', 'Untitled')}")
                root.attributes("-topmost", bool(options.get("topmost", False)))
                if first or previous_size != (options.get("width"), options.get("height")):
                    root.geometry(f"{int(options.get('width', 800))}x{int(options.get('height', 600))}")
                if first:
                    initial_display = show_without_activation(root, focus_guard)
                root.update_idletasks()
                render()
                atomic_json(state_path.parent / "window.json", {"phase": "ready", "generation": generation,
                    "size": list(image.size), "viewable": bool(root.winfo_viewable()),
                    "window_size": [root.winfo_width(), root.winfo_height()],
                    "topmost": bool(root.attributes("-topmost")),
                    "borderless": bool(root.overrideredirect()), **initial_display})
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Keep the last good frame if publication overlaps a read.
        root.after(max(50, min(2000, int(options.get("refresh_ms", 150)))), poll)

    def fit(_event=None):
        nonlocal scale
        scale = None
        pan[:] = [0.0, 0.0]
        render()

    def zoom(direction):
        nonlocal scale
        if image is None:
            return
        current = scale if scale is not None else min(max(1, canvas.winfo_width() - 24) / image.width,
                                                     max(1, canvas.winfo_height() - 24) / image.height)
        scale = min(64.0, max(0.01, current * (1.25 if direction > 0 else 0.8)))
        render()

    def actual(_event=None):
        nonlocal scale
        scale = 1.0
        pan[:] = [0.0, 0.0]
        render()

    def begin_drag(event):
        nonlocal drag
        drag = (event.x, event.y)

    def move_drag(event):
        nonlocal drag
        if drag is not None:
            pan[0] += event.x - drag[0]
            pan[1] += event.y - drag[1]
            drag = (event.x, event.y)
            render()

    root.protocol("WM_DELETE_WINDOW", finish)
    canvas.bind("<Configure>", lambda event: render())
    canvas.bind("<MouseWheel>", lambda event: zoom(event.delta))
    canvas.bind("<Button-4>", lambda event: zoom(1))
    canvas.bind("<Button-5>", lambda event: zoom(-1))
    canvas.bind("<ButtonPress-1>", begin_drag)
    canvas.bind("<B1-Motion>", move_drag)
    root.bind("f", fit)
    root.bind("1", actual)
    root.bind("+", lambda event: zoom(1))
    root.bind("-", lambda event: zoom(-1))
    poll()
    root.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only SPARKER iCLI painting preview")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        return show_preview(args.state, args.parent_pid)
    except Exception as error:
        try:
            atomic_json(args.state.parent / "window.json", {"phase": "failed", "error": str(error)})
        except OSError:
            pass
        return 1
    finally:
        # If the editor was forcibly stopped, it cannot clean its temporary IPC.
        # Delete only this helper's uniquely named, verified temporary directory.
        if not parent_alive(args.parent_pid):
            import tempfile
            directory = args.state.parent.resolve()
            if directory.parent == Path(tempfile.gettempdir()).resolve() and directory.name.startswith("sparker-preview-"):
                shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
