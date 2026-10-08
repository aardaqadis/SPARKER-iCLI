#!/usr/bin/env python3
"""Portable, standard-library bootstrap for SPARKER iCLI.

The editor gets its own local environment. POSIX replaces the bootstrap with
the editor process; Windows waits without starting any desktop helpers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


BRAND = "SPARKER iCLI 0.5"


def environment_python(environment: Path, platform: str | None = None) -> Path:
    """Use the native venv layout rather than requiring Windows Scripts/."""
    return environment / ("Scripts/python.exe" if (platform or os.name) == "nt" else "bin/python")


def project_signature(root: Path) -> dict[str, str]:
    return {
        "root": str(root.resolve()),
        "project_hash": hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest().upper(),
        "brand": BRAND,
    }


def _run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=False, **kwargs)


def environment_works(python: Path, environment: Path) -> bool:
    if not python.is_file():
        return False
    try:
        checked = _run([
            str(python), "-c",
            "import pathlib,sys; assert sys.version_info >= (3,11); "
            "assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve(); "
            "assert sys.prefix!=sys.base_prefix",
            str(environment),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return checked.returncode == 0
    except OSError:
        return False


def installation_works(python: Path, root: Path) -> bool:
    """Repair missing dependencies and editable installs copied from elsewhere."""
    try:
        checked = _run([
            str(python), "-c",
            "import pathlib,sys,PIL,textual,numpy,termatelier; "
            "expected=(pathlib.Path(sys.argv[1])/'src'/'termatelier').resolve(); "
            "assert pathlib.Path(termatelier.__file__).resolve().parent==expected",
            str(root),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return checked.returncode == 0
    except OSError:
        return False


def prepare_environment(root: Path) -> Path:
    environment = root / ".venv"
    python = environment_python(environment)
    if not environment_works(python, environment):
        print("SPARKER iCLI: preparing the local Python environment...", file=sys.stderr)
        created = _run([sys.executable, "-m", "venv", str(environment)],
                       stdout=sys.stderr, stderr=sys.stderr)
        if created.returncode != 0 or not environment_works(python, environment):
            raise RuntimeError(
                "Could not prepare .venv. Python 3.11 or newer with venv/ensurepip support is required. "
                "On Linux, install your distribution's Python venv package and retry."
            )

    signature = project_signature(root)
    stamp = environment / "sparker-installed.json"
    try:
        current = json.loads(stamp.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        current = None
    if current != signature or not installation_works(python, root):
        print("SPARKER iCLI: installing editor dependencies...", file=sys.stderr)
        installed = _run([str(python), "-m", "pip", "install", "-e", str(root)],
                         stdout=sys.stderr, stderr=sys.stderr)
        if installed.returncode != 0 or not installation_works(python, root):
            raise RuntimeError("Dependency installation failed. Check the installation details above and retry.")
        # Only publish a freshness stamp after the installed source is verified.
        stamp.write_text(json.dumps(signature, indent=2) + "\n", encoding="utf-8")
    return python


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    if sys.version_info < (3, 11):
        print("SPARKER iCLI requires Python 3.11 or newer.", file=sys.stderr)
        return 2
    root = (root or Path(__file__).resolve().parent).resolve()
    arguments = list(sys.argv[1:] if argv is None else argv)
    # A portable terminal launcher never opens a splash/overlay window.
    # Accept the Windows launcher option so command lines can be shared.
    arguments = [argument for argument in arguments if argument != "--no-splash"]
    try:
        python = prepare_environment(root)
        environment = os.environ.copy()
        environment.pop("SPARKER_STARTUP_STATE", None)
        environment.pop("SPARKER_DEBUG_STATE", None)
        environment["SPARKER_PROJECT_ROOT"] = str(root)
        # Keep the caller's working directory: relative artwork/script paths
        # should mean the same thing as invoking the installed sparker command.
        command = [str(python), "-m", "termatelier", *arguments]
        if os.name == "nt":
            # Native Windows execve is not POSIX process replacement and can
            # fail for Python venv executables; subprocess handles quoting and
            # propagates the editor's status reliably.
            return _run(command, env=environment).returncode
        os.execve(str(python), command, environment)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"SPARKER iCLI: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSPARKER iCLI startup interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
