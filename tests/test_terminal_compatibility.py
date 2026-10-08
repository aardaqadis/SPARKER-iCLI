import io
import json
import sys

from PIL import Image
import pytest

from termatelier import __main__ as entry
from termatelier.terminal import print_text, ui_unavailable_reason


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ASCIIOutput(Terminal):
    encoding = "ascii"

    def write(self, value):
        value.encode(self.encoding)
        return super().write(value)


@pytest.mark.parametrize("environment,reason", [
    ({"TERM": "dumb"}, "full-screen"), ({"TERM": "unknown"}, "full-screen"),
    ({"TERM": "xterm-256color"}, None), ({}, None),
])
def test_terminal_capabilities_without_os_or_shell_assumptions(environment, reason):
    result = ui_unavailable_reason(Terminal(), Terminal(), environment)
    assert result is None if reason is None else reason in result
    assert "Unicode" in ui_unavailable_reason(Terminal(), ASCIIOutput(), {"TERM": "xterm"})


def test_ascii_status_output_is_readable_and_does_not_crash():
    output = ASCIIOutput()
    print_text("SPARKER · 31×19 — saved → artwork", file=output)
    assert output.getvalue() == "SPARKER | 31x19 - saved -> artwork\n"


@pytest.mark.parametrize("arguments", [[], ["--cli"], ["--ui"]])
def test_dumb_terminal_uses_editing_commands_and_lossless_export(arguments, monkeypatch, tmp_path):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(sys, "stdin", Terminal('pencil 2,3 --color red\nexport fallback.png\nquit\n'))
    output = ASCIIOutput()
    monkeypatch.setattr(sys, "stdout", output)
    assert entry.main(["--new", "31x19", *arguments]) == 0
    image = Image.open(tmp_path / "exports" / "fallback.png")
    assert image.size == (31, 19)
    assert image.getpixel((2, 3)) == (255, 0, 0, 255)
    assert "sparker>" in output.getvalue()


def test_default_redirected_cli_never_starts_fullscreen(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO('pencil 1,1 --color blue\nexport piped.png\nquit\n'))
    assert entry.main(["--new", "19x13"]) == 0
    assert Image.open(tmp_path / "exports" / "piped.png").getpixel((1, 1)) == (0, 0, 255, 255)
    assert "\x1b[" not in capsys.readouterr().out


def test_headless_batch_commands_work_on_ascii_output(monkeypatch):
    monkeypatch.setattr(sys, "stdout", ASCIIOutput())
    assert entry.main(["--new", "31x19", "--low-memory", "-c", "info --json"]) == 0
    assert json.loads(sys.stdout.getvalue())["size"] == [31, 19]
