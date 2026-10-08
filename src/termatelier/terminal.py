"""Small terminal capability checks; the line CLI needs neither ANSI nor Tk."""
from __future__ import annotations

import os
import sys


def ui_unavailable_reason(stdin=None, stdout=None, environ=None):
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    environ = os.environ if environ is None else environ
    try:
        if not stdin.isatty() or not stdout.isatty():
            return "input or output is redirected"
    except (AttributeError, OSError, ValueError):
        return "terminal input/output is unavailable"
    if environ.get("TERM", "").lower() in ("dumb", "unknown"):
        return "this terminal does not support full-screen control"
    encoding = getattr(stdout, "encoding", None) or "utf-8"
    try:
        "▀".encode(encoding)
    except (UnicodeError, LookupError):
        return "this terminal cannot display Unicode canvas characters"
    return None


def configure_output():
    """Make status/help output safe on older console encodings and pipes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass


def print_text(*values, sep=" ", end="\n", file=None):
    stream = sys.stdout if file is None else file
    text = sep.join(str(value) for value in values) + end
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        replacements = {"·": "|", "×": "x", "→": "->", "←": "<-", "↶": "undo", "—": "-"}
        for source, target in replacements.items():
            text = text.replace(source, target)
        stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding))
