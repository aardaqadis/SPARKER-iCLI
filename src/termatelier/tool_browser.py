"""Searchable, paginated visual access to the registered art tools."""
from __future__ import annotations

from functools import lru_cache
from PIL import Image
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from .tool_library import TOOLS, apply_tool, get_tool, list_tools, render_tip


@lru_cache(maxsize=96)
def tool_thumbnail(identifier):
    spec = get_tool(identifier)
    if spec.kind == "effect":
        from .model import Document, Layer
        image = Image.new("RGBA", (64, 64))
        image.putdata([(x*4, y*4, ((x//8+y//8) % 2)*160+70, 255) for y in range(64) for x in range(64)])
        doc = Document(64, 64)
        doc.layers, doc.active = [Layer("Preview", image)], 0
        apply_tool(doc, identifier)
        image = doc.composite().convert("RGB")
    else:
        mask = render_tip(spec, 64)
        image = Image.composite(Image.new("RGB", mask.size, "#e79335"), Image.new("RGB", mask.size, "#101010"), mask)
    image = image.resize((28, 24), Image.Resampling.LANCZOS)
    text = Text()
    for y in range(0, image.height, 2):
        for x in range(image.width):
            a, b = image.getpixel((x, y)), image.getpixel((x, y+1))
            text.append("▀", style=f"rgb({a[0]},{a[1]},{a[2]}) on rgb({b[0]},{b[1]},{b[2]})")
        if y+2 < image.height: text.append("\n")
    return text


class ToolLibraryBrowser(ModalScreen):
    DEFAULT_CSS = """
    ToolLibraryBrowser { align: center middle; background: #090909 60%; }
    ToolLibraryBrowser > Vertical { width: 95%; height: 90%; background: #121212; padding: 1 2; }
    #library-title { color: #e79335; height: 1; margin-bottom: 1; }
    #library-filters { height: 3; }
    #library-search { width: 1fr; margin-right: 1; }
    #library-category { width: 25; margin-right: 1; }
    #library-kind { width: 17; }
    #library-content { height: 1fr; }
    #library-results { width: 1fr; border: none; background: #121212; }
    #library-detail-column { width: 34; padding-left: 2; }
    #library-thumbnail { height: 12; margin-bottom: 1; }
    #library-description { height: 1fr; overflow-y: auto; color: #aaaaaa; }
    #library-page { height: 1; color: #888888; margin-top: 1; }
    #library-buttons { height: 3; margin-top: 1; }
    #library-buttons Button { min-width: 10; width: auto; margin-right: 1; }
    """
    BINDINGS = [("escape", "close", "Close")]
    PAGE_SIZE = 80

    def __init__(self):
        super().__init__()
        self.page = 0
        self.matches = []
        self.selected = None

    def compose(self):
        with Vertical():
            yield Label(f"Tool library · {len(TOOLS):,} distinct art tools", id="library-title")
            with Horizontal(id="library-filters"):
                yield Input(placeholder="Search tools, motifs or materials…", id="library-search")
                yield Select([("All categories", "__all__")] + [(x.title(), x) for x in sorted({spec.category for spec in TOOLS.values()})],
                             value="__all__", allow_blank=False, id="library-category")
                yield Select([("All types", "__all__")] + [(x.title(), x) for x in sorted({spec.kind for spec in TOOLS.values()})],
                             value="__all__", allow_blank=False, id="library-kind")
            with Horizontal(id="library-content"):
                yield OptionList(id="library-results")
                with Vertical(id="library-detail-column"):
                    yield Static(id="library-thumbnail")
                    yield Static("Choose a tool to see its preview and description.", id="library-description", markup=False)
            yield Static(id="library-page", markup=False)
            with Horizontal(id="library-buttons"):
                yield Button("Previous", id="library-previous")
                yield Button("Next", id="library-next")
                yield Button("Use tool", variant="primary", id="library-use")
                yield Button("Apply now", id="library-apply")
                yield Button("Close", id="library-close")

    def on_mount(self):
        self.filter_tools()
        self.query_one("#library-search", Input).focus()

    def filter_tools(self):
        query = self.query_one("#library-search", Input).value
        category = self.query_one("#library-category", Select).value
        kind = self.query_one("#library-kind", Select).value
        self.matches = list_tools(query=query, category=None if category == "__all__" else category,
                                  kind=None if kind == "__all__" else kind)
        self.page = 0
        self.refresh_page()

    def refresh_page(self):
        results = self.query_one("#library-results", OptionList)
        results.clear_options()
        subset = self.matches[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
        for spec in subset:
            results.add_option(Option(f"{spec.name}  ·  {spec.kind}", id=spec.id))
        results.highlighted = 0 if subset else None
        self.selected = subset[0].id if subset else None
        count = len(self.matches)
        self.query_one("#library-page", Static).update(
            f"{count:,} matching tools · page {self.page+1}/{max(1, (count+self.PAGE_SIZE-1)//self.PAGE_SIZE)} · Enter selects a tool")
        self.query_one("#library-previous", Button).disabled = self.page == 0
        self.query_one("#library-next", Button).disabled = (self.page+1)*self.PAGE_SIZE >= count
        self.query_one("#library-use", Button).disabled = not subset
        self.query_one("#library-apply", Button).disabled = not subset
        self.update_detail()

    def update_detail(self):
        if self.selected is None:
            self.query_one("#library-thumbnail", Static).update("")
            self.query_one("#library-description", Static).update("No tools match. Try a broader search or category.")
            return
        spec = get_tool(self.selected)
        self.query_one("#library-thumbnail", Static).update(tool_thumbnail(spec.id))
        self.query_one("#library-description", Static).update(
            f"{spec.name}\n\n{spec.category.title()} · {spec.kind}\n\n{spec.description}\n\nCommand: tool {spec.id}\n\n"
            "Use tool: select for drawing with the mouse.\nApply now: use the canvas center or current selection.\nTool options control size, rotation, density, seed and strength.")

    def on_input_changed(self, event):
        if event.input.id == "library-search" and self.is_mounted:
            self.filter_tools()

    def on_select_changed(self, event):
        if event.select.id in ("library-category", "library-kind") and self.is_mounted:
            self.filter_tools()

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == "library-results":
            self.selected = event.option.id
            self.update_detail()

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "library-results":
            event.stop()
            self.dismiss(("use", event.option.id))

    def on_button_pressed(self, event):
        event.stop()
        key = event.button.id
        if key == "library-previous":
            self.page -= 1
            self.refresh_page()
        elif key == "library-next":
            self.page += 1
            self.refresh_page()
        elif key in ("library-use", "library-apply") and self.selected:
            self.dismiss(("apply" if key == "library-apply" else "use", self.selected))
        elif key == "library-close": self.dismiss(None)

    def action_close(self):
        self.dismiss(None)
