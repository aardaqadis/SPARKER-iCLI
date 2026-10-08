# Contributing to SPARKER iCLI

Use Python 3.11 or later. Application code lives in `src/` and tests in `tests/`.

## Development on Windows

From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-tested.txt -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m sparkericli --demo
```

On Linux/macOS, the corresponding interpreter is `.venv/bin/python`.
Run tests from the repository root so pytest reads `pyproject.toml`.

## Building and checking a package

From the same repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install build
.\.venv\Scripts\python.exe -m build --outdir dist
.\.venv\Scripts\python.exe tools\check_wheel.py dist
```

The wheel check installs the exact built package into a temporary directory,
checks the public module and metadata, then tests CLI drawing, all-tool count
and a pixel-exact native-size PNG export. Preferences and output stay temporary.
Builds, environments and diagnostics are ignored by Git.

## Making changes

Keep command handlers and terminal controls connected to the shared document,
history and export behavior. Preserve .tart v1 compatibility and original RGBA
pixels. Display previews, guides and zoom must never alter exported images.

For painting changes, verify sparse mouse motion, live capture, one undo step,
cancel and redo, plus both Fit and actual-size rendering. For file changes,
verify external export destinations, replacement guards and exact dimensions.
Add focused behavior tests when a change introduces or fixes meaningful behavior.
Keep unrelated user files and settings out of a change.

Explain the problem, resulting behavior and validation in a pull request.
Include a reproducible command or example for an issue. Debug reports should
contain only the settings and behavior needed to reproduce it.

## Automated checks

GitHub Actions is configured to test supported Python versions, build the wheel
and source distribution, and run the installed-wheel check. Native-window cases
skip on platforms without their required display support. Hosted-runner results
appear in the repository's **Actions** tab after the first push.
