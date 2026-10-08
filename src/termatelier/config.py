"""Validated user preferences, independent of editor and image dependencies.

This module can be loaded directly by the bootstrap interpreter before the
virtual environment exists. Only named settings are accepted; no code or
arbitrary environment variables are evaluated.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


def project_root():
    return Path(os.environ.get("SPARKER_PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()


def pictures_directory():
    """Respect an XDG Pictures location without evaluating shell expressions."""
    fallback = Path.home() / "Pictures"
    if os.name != "nt" and sys.platform != "darwin":
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        if not base.is_absolute():
            base = Path.home() / ".config"
        source = base / "user-dirs.dirs"
        try:
            if source.stat().st_size <= 65536:
                for line in source.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("XDG_PICTURES_DIR="):
                        value = json.loads(line.strip().partition("=")[2].strip())
                        if not isinstance(value, str):
                            break
                        if value == "$HOME" or value.startswith("$HOME/"):
                            value = str(Path.home()) + value[5:]
                        candidate = Path(value)
                        if candidate.is_absolute():
                            return candidate.resolve()
        except (OSError, ValueError, TypeError):
            pass
    return fallback.resolve()


def default_export_directory():
    return (pictures_directory() / "SPARKER-iCLI" / "Exports").resolve()


def settings_path():
    override = os.environ.get("SPARKER_CONFIG_FILE")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        legacy = Path.home() / ".config" / "SPARKER-iCLI" / "settings.json"
        if legacy.exists() and not (base / "SPARKER-iCLI" / "settings.json").exists():
            return legacy.resolve()
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        if not base.is_absolute():
            base = Path.home() / ".config"
    return (base / "SPARKER-iCLI" / "settings.json").resolve()


DEFAULTS = {
    "memory.mode": "standard",
    "debug.enabled": True,
    "debug.refresh_ms": 500,
    "debug.font_size": 11,
    "debug.opacity": 1.0,
    "debug.color": "#d0d0d0",
    "debug.detail": "full",
    "debug.topmost": True,
    "startup.minimum_seconds": 2.5,
    "export.directory": str(default_export_directory()),
    "export.lossless": True,
    "export.scale": 1,
    "view.resampling": "nearest",
    "view.zoom_min": 0.025,
    "view.zoom_max": 32.0,
    "history.max_steps": 40,
    "history.max_mb": 96,
    "history.storage": "raw",
    "brush.size": 3,
    "brush.hardness": 0.8,
    "brush.opacity": 1.0,
    "fill.tolerance": 20,
    "library.size": 32,
    "library.angle": 0.0,
    "library.density": 1.0,
    "library.amount": 1.0,
    "library.seed": 0,
    "preview.enabled": True,
    "preview.width": 800,
    "preview.height": 600,
    "preview.topmost": False,
    "preview.resampling": "bilinear",
    "preview.refresh_ms": 150,
}

# Type, allowed range/choices and a user-facing description.
SPECS = {
    "memory.mode": ("choice", ("standard", "low"), "Low uses compressed undo (8 steps / 16 MiB maximum) and disables desktop helpers"),
    "debug.enabled": ("bool", None, "Show the transparent desktop debug overlay"),
    "debug.refresh_ms": ("int", (100, 10000), "Debug refresh interval in milliseconds"),
    "debug.font_size": ("int", (7, 32), "Debug text size in points"),
    "debug.opacity": ("float", (0.1, 1.0), "Debug text opacity; background remains transparent"),
    "debug.color": ("color", None, "Debug text color in #RRGGBB notation"),
    "debug.detail": ("choice", ("full", "compact"), "Debug information density"),
    "debug.topmost": ("bool", None, "Keep the debug overlay above other windows"),
    "startup.minimum_seconds": ("float", (0, 30), "Minimum loading-screen duration in seconds"),
    "export.directory": ("path", None, "Absolute export folder outside the application project"),
    "export.lossless": ("bool", None, "Require lossless export unless --allow-lossy is explicit"),
    "export.scale": ("int", (1, 16), "Image export enlargement; each canvas pixel becomes an exact NxN block"),
    "view.resampling": ("choice", ("nearest", "bilinear", "bicubic"), "Preview resampling; never changes document pixels"),
    "view.zoom_min": ("float", (0.005, 1), "Minimum preview zoom"),
    "view.zoom_max": ("float", (1, 128), "Maximum preview zoom"),
    "history.max_steps": ("int", (1, 200), "Maximum undo and redo checkpoints combined"),
    "history.max_mb": ("int", (8, 512), "Undo and redo snapshot memory budget in MiB"),
    "history.storage": ("choice", ("raw", "compressed"), "Lossless undo storage; compression uses less memory with additional CPU work"),
    "brush.size": ("int", (1, 128), "Default brush diameter in pixels"),
    "brush.hardness": ("float", (0, 1), "Default brush hardness"),
    "brush.opacity": ("float", (0, 1), "Default drawing opacity"),
    "fill.tolerance": ("int", (0, 255), "Default flood fill color tolerance"),
    "library.size": ("int", (1, 512), "Library brush / stamp size and pattern tile size in pixels"),
    "library.angle": ("float", (-360, 360), "Library tip rotation in degrees"),
    "library.density": ("float", (0.1, 4), "Library mark density"),
    "library.amount": ("float", (0, 4), "Library effect strength"),
    "library.seed": ("int", (0, 2147483647), "Deterministic library texture seed"),
    "preview.enabled": ("bool", None, "Open a separate painting preview when entering full-screen CLI"),
    "preview.width": ("int", (240, 3840), "Initial preview window width"),
    "preview.height": ("int", (180, 2160), "Initial preview window height"),
    "preview.topmost": ("bool", None, "Keep the painting preview above other windows"),
    "preview.resampling": ("choice", ("nearest", "bilinear", "bicubic"), "Preview window scaling; never changes image data"),
    "preview.refresh_ms": ("int", (50, 2000), "Preview refresh interval in milliseconds"),
}


class ConfigError(ValueError):
    pass


def validate(name, value):
    if name not in SPECS:
        raise ConfigError(f"Unknown setting {name!r}. Use config list for available names.")
    kind, limits, _ = SPECS[name]
    if kind == "bool":
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.lower() in ("true", "on", "yes", "1", "false", "off", "no", "0"):
            return value.lower() in ("true", "on", "yes", "1")
        raise ConfigError(f"{name} requires true or false.")
    if kind in ("int", "float"):
        if isinstance(value, bool):
            raise ConfigError(f"{name} requires a number.")
        try:
            if kind == "int":
                number = int(value)
                if isinstance(value, float) and value != number:
                    raise ValueError
            else:
                number = float(value)
        except (ValueError, TypeError, OverflowError) as error:
            raise ConfigError(f"{name} requires {'an integer' if kind == 'int' else 'a number'}.") from error
        if not limits[0] <= number <= limits[1] or (kind == "float" and not math.isfinite(number)):
            raise ConfigError(f"{name} must be in {limits[0]}..{limits[1]}.")
        return number
    if kind == "choice":
        if not isinstance(value, str) or value.lower() not in limits:
            raise ConfigError(f"{name} must be one of: {', '.join(limits)}.")
        return value.lower()
    if kind == "color":
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ConfigError(f"{name} requires #RRGGBB.")
        return value.lower()
    if kind == "path":
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            raise ConfigError(f"{name} requires an absolute folder path.")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ConfigError(f"{name} requires an absolute folder path.")
        candidate = candidate.resolve()
        root = project_root()
        roots = [root]
        if (root.parent / "run.cmd").exists() and (root.parent / "run.ps1").exists():
            roots.append(root.parent)
        if any(candidate == item or item in candidate.parents for item in roots):
            raise ConfigError("Exports must use a folder outside the SPARKER iCLI project.")
        if candidate.exists() and not candidate.is_dir():
            raise ConfigError(f"{candidate} is not a folder.")
        return str(candidate)
    raise ConfigError(f"Unsupported setting {name}.")


class RuntimeConfig:
    def __init__(self, values=None, *, path=None, overrides=None, warnings=None):
        self.path = Path(path).expanduser().resolve() if path else settings_path()
        self._values = dict(DEFAULTS)
        self._values.update(values or {})
        self._overrides = dict(overrides or {})
        self.warnings = list(warnings or [])

    @classmethod
    def load(cls, overrides=None, path=None):
        destination = Path(path).expanduser().resolve() if path else settings_path()
        values, warnings = {}, []
        try:
            if destination.exists():
                if destination.stat().st_size > 65536:
                    raise ValueError("settings file exceeds 64 KiB")
                raw = json.loads(destination.read_text(encoding="utf-8"))
                entries = raw.get("settings", raw) if isinstance(raw, dict) else None
                if not isinstance(entries, dict):
                    raise ValueError("settings must be a JSON object")
                for name, value in entries.items():
                    try:
                        values[name] = validate(name, value)
                    except ConfigError as error:
                        warnings.append(str(error))
        except (ValueError, OSError, UnicodeError) as error:
            warnings.append(f"Could not read settings: {error}. Defaults are active.")
        effective_overrides = {}
        encoded = os.environ.get("SPARKER_CONFIG_OVERRIDES")
        if encoded:
            try:
                decoded = json.loads(encoded)
            except (ValueError, TypeError) as error:
                raise ConfigError("SPARKER_CONFIG_OVERRIDES must contain a JSON object.") from error
            if not isinstance(decoded, dict):
                raise ConfigError("SPARKER_CONFIG_OVERRIDES must contain a JSON object.")
            effective_overrides.update(decoded)
        effective_overrides.update(overrides or {})
        effective_overrides = {name: validate(name, value) for name, value in effective_overrides.items()}
        return cls(values, path=destination, overrides=effective_overrides, warnings=warnings)

    def get(self, name, default=None):
        value = self._overrides.get(name, self._values.get(name, default))
        mode = self._overrides.get("memory.mode", self._values.get("memory.mode", "standard"))
        if mode == "low":
            if name in ("debug.enabled", "preview.enabled"):
                return False
            if name == "history.storage":
                return "compressed"
            limits = {"history.max_steps": 8, "history.max_mb": 16}
            if name in limits:
                return min(value, limits[name])
        return value

    def as_dict(self):
        return {name: self.get(name) for name in dict.fromkeys([*self._values, *self._overrides])}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"format": "sparker-settings", "version": 1, "settings": self._values}, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def set(self, name, value, persist=True):
        normalized = validate(name, value)
        if persist:
            # Reload preferences so two instances changing different settings
            # do not replace each other's most recent values.
            updated = RuntimeConfig.load(path=self.path)
            updated._values[name] = normalized
            updated.save()
            self._values = updated._values
            self._overrides.pop(name, None)
        else:
            self._overrides[name] = normalized
        return normalized

    def reset(self, name=None):
        if name is not None and name not in DEFAULTS:
            raise ConfigError(f"Unknown setting {name!r}.")
        updated = RuntimeConfig.load(path=self.path)
        if name is None:
            updated._values = dict(DEFAULTS)
            self._overrides.clear()
        else:
            updated._values[name] = DEFAULTS[name]
            self._overrides.pop(name, None)
        updated.save()
        self._values = updated._values
        return self.get(name) if name else self.as_dict()


def parse_overrides(assignments):
    result = {}
    for assignment in assignments or []:
        name, separator, value = assignment.partition("=")
        if not separator or not name or not value:
            raise ConfigError("--set requires NAME=VALUE; use config list for available names.")
        result[name] = validate(name, value)
    return result
