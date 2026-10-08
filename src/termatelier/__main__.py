"""SPARKER iCLI: terminal editor, command REPL and headless editing jobs."""
import argparse
import json
from pathlib import Path
import sys

from .commands import CommandError, CommandSession
from .config import RuntimeConfig, parse_overrides
from .diagnostics import publish_diagnostics
from .model import Document
from .storage import export, export_dimensions, import_document, load_project, save_project
from .terminal import configure_output, print_text as print, ui_unavailable_reason


def demo_document():
    doc = Document(160, 100)
    doc.layers.pop(1)
    doc.active = 0
    with doc.edit("Twilight sky"):
        doc.gradient((0, 0), (0, 100), "#15243e", "#ab708f")
    with doc.edit("Sun"):
        doc.add_layer("Sun")
        doc.paint_mask(doc.shape_mask("ellipse", (103, 14), (135, 46), filled=True), "#f4d6a2")
    with doc.edit("Mountains"):
        doc.add_layer("Mountains")
        from PIL import Image, ImageDraw
        mask = Image.new("L", doc.size)
        ImageDraw.Draw(mask).polygon([(0, 78), (35, 32), (65, 74), (95, 47), (160, 90), (160, 100), (0, 100)], fill=255)
        doc.paint_mask(mask, "#253b55")
    with doc.edit("Lake"):
        doc.add_layer("Lake")
        doc.paint_mask(doc.shape_mask("rectangle", (0, 81), (159, 99), filled=True), "#427383")
        for y, x in [(86, 11), (91, 43), (96, 8), (88, 110), (94, 121)]:
            doc.paint_mask(doc.shape_mask("line", (x, y), (min(159, x+27), y)), "#88bbb5", .6)
    with doc.edit("Title"):
        doc.add_layer("Lettering")
        doc.text((7, 6), "SPARKER iCLI", "#f7e7cd", 11)
    doc.metadata["title"] = "Twilight study"
    return doc


class _Operation(argparse.Action):
    """Preserve the order of repeated --command and --script arguments."""
    def __call__(self, parser, namespace, values, option_string=None):
        operations = getattr(namespace, "operations", None)
        if operations is None:
            operations = []
            namespace.operations = operations
        operations.append((self.dest, values))


def _ready():
    try:
        from .startup import signal_ready
    except ImportError:
        return
    signal_ready()


def repl(session):
    print("SPARKER iCLI · command workspace · type help, quit or Ctrl+Z/Ctrl+D to leave")
    _ready()
    while True:
        try:
            line = input("sparker> ")
        except EOFError:
            print()
            return 0
        try:
            result = session.execute(line)
            if result.text:
                print(result.text)
            if result.quit_requested:
                return 0
        except CommandError as error:
            print(f"sparker: {error}", file=sys.stderr)


def main(argv=None):
    configure_output()
    parser = argparse.ArgumentParser(
        prog="sparker", description="SPARKER iCLI — minimalist terminal painting and image editing",
        epilog='Examples: sparker --cli | sparker --new 128x96 -c "fill 0 0 --color orange" --export art.png | sparker --script artwork.sparker\n'
               'With editing commands, the default is headless. Add --ui to open the editor afterward.\n'
               'Command help: sparker -c help or sparker -c "help COMMAND". See README.md for examples.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="Native .tart project or image to open")
    parser.add_argument("--new", metavar="WxH", help="New canvas, e.g. 160x100")
    parser.add_argument("--demo", action="store_true", help="Open the layered twilight demonstration")
    parser.add_argument("--export", metavar="PATH", nargs="?", const="", help="Export outside the project; omitted PATH uses a PNG filename")
    parser.add_argument("--export-scale", metavar="N", type=int, help="Enlarge image exports by an integer 1..16 with crisp pixels; default uses export.scale")
    parser.add_argument("--allow-lossy", action="store_true", help="Allow JPEG/GIF/BMP export; all image dimensions are preserved")
    parser.add_argument("--set", metavar="NAME=VALUE", action="append", help="Override a validated preference for this run; repeatable")
    parser.add_argument("--low-memory", action="store_true", help="Compress undo, limit it to 8 steps / 16 MiB, and disable desktop helpers; original image pixels are preserved")
    debug = parser.add_mutually_exclusive_group()
    debug.add_argument("--debug", action="store_true", help="Show the transparent desktop debug overlay")
    debug.add_argument("--no-debug", action="store_true", help="Start with the debug overlay hidden")
    parser.add_argument("--save-project", metavar="PATH", help="Save .tart and exit without a terminal UI")
    parser.add_argument("--columns", type=int, default=100, help="Text export maximum width")
    parser.add_argument("--inspect", action="store_true", help="Print project metadata as JSON and exit")
    parser.add_argument("--command", "-c", metavar="COMMAND", action=_Operation,
                        help="Apply one editing command; repeatable and ordered with --script")
    parser.add_argument("--script", metavar="PATH", action=_Operation,
                        help="Run a UTF-8 editing script; in-memory rollback on failure")
    parser.add_argument("--nonatomic", action="store_true", help="Keep completed script edits when a later line fails")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cli", action="store_true", help="Full-screen command workspace with a pop-out live preview; piped input remains a line REPL")
    mode.add_argument("--repl", action="store_true", help="Use the plain line REPL, including on an interactive terminal")
    mode.add_argument("--ui", action="store_true", help="Open the mouse-driven editor after any batch edits")
    args = parser.parse_args(argv)
    if args.export_scale is not None and args.export is None:
        parser.error("--export-scale requires --export; within editing commands use export --scale N.")
    if sum(bool(x) for x in (args.file, args.new, args.demo)) > 1:
        parser.error("Choose one input: file, --new or --demo.")
    try:
        overrides = parse_overrides(args.set)
        if args.low_memory:
            overrides["memory.mode"] = "low"
        if args.debug or args.no_debug:
            overrides["debug.enabled"] = args.debug
        config = RuntimeConfig.load(overrides=overrides)
        project_path = None
        if args.file:
            if Path(args.file).suffix.lower() == ".tart":
                doc, project_path = load_project(args.file), args.file
            else: doc = import_document(args.file)
        elif args.demo: doc = demo_document()
        elif args.new:
            w, h = args.new.lower().split("x")
            doc = Document(int(w), int(h))
        else: doc = Document()
        session = CommandSession(doc, project_path, config=config)
        publish_diagnostics(doc, config, stage="ready")
        operations = getattr(args, "operations", None) or []
        has_export = args.export is not None
        if operations or has_export or args.save_project or args.inspect:
            _ready()
        for kind, value in operations:
            if kind == "command":
                result = session.execute(value)
                if result.quit_requested:
                    raise CommandError("Use quit within --cli, rather than a batch command.")
                if result.text: print(result.text)
            else:
                path = Path(value).expanduser().resolve()
                results = session.run_script(path.read_text(encoding="utf-8-sig"), str(path),
                                             atomic=not args.nonatomic, base_dir=path.parent)
                for result in results:
                    if result.text: print(result.text)
        doc = session.document
        if has_export:
            destination = export(doc, args.export or None, args.columns, allow_lossy=args.allow_lossy,
                                 config=config, scale=args.export_scale)
            if destination.suffix.lower() in (".txt", ".ansi"):
                print(f"Exported {destination}.")
            else:
                width, height = export_dimensions(doc.size, config.get("export.scale") if args.export_scale is None
                                                  else args.export_scale, extension=destination.suffix)
                print(f"Exported {destination} · {width}×{height} pixels.")
        if args.save_project:
            save_project(doc, args.save_project)
            session.project_path = Path(args.save_project).expanduser().resolve()
        if args.inspect:
            print(json.dumps({"size": doc.size, "active": doc.active, "metadata": doc.metadata,
                              "layers": [{"name": x.name, "visible": x.visible, "opacity": x.opacity,
                                          "blend": x.blend, "masked": x.mask is not None} for x in doc.layers]}, indent=2))
        unavailable = ui_unavailable_reason()
        if args.cli and unavailable is None:
            from .cli_app import CLIApp
            CLIApp(session).run()
            return 0
        if args.cli or args.repl:
            if args.cli and unavailable and sys.stdin.isatty():
                print(f"Using the line CLI: {unavailable}.", file=sys.stderr)
            return repl(session)
        if not args.ui and (operations or has_export or args.save_project or args.inspect): return 0
        if unavailable:
            if sys.stdin.isatty():
                print(f"Using the line CLI: {unavailable}. Type help for editing commands.", file=sys.stderr)
            return repl(session)
        from .app import Studio
        Studio(doc, session.project_path, config=config).run()
        return 0
    except (ValueError, OSError) as error:
        print(f"sparker: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nSPARKER iCLI interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
