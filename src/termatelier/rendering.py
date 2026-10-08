"""Terminal preview sampling. Native image exports use original pixels."""
from __future__ import annotations

from dataclasses import dataclass
import math

from PIL import Image


_RESAMPLING = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
}


def _zoom(value):
    if isinstance(value, bool):
        raise ValueError("Sampling zoom must be a finite positive number.")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Sampling zoom must be a finite positive number.") from error
    if not math.isfinite(number) or number <= 0 or not math.isfinite(1 / number):
        raise ValueError("Sampling zoom must be a finite positive number.")
    return number


def _size(size):
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(type(value) is not int or value < 1 for value in size)):
        raise ValueError("Image dimensions must be positive integers.")
    return tuple(size)


def _resampling(name):
    try:
        return _RESAMPLING[name]
    except (KeyError, TypeError) as error:
        raise ValueError("Sampling must be nearest, bilinear or bicubic.") from error


@dataclass(frozen=True)
class ExportView:
    """Painting sampling, independent of viewport position and overlays."""

    zoom: float
    resampling: str = "nearest"

    def __post_init__(self):
        object.__setattr__(self, "zoom", _zoom(self.zoom))
        _resampling(self.resampling)


def reduction_factor(zoom):
    """Use the same integer area average as the terminal at reduced zoom."""
    return max(1, math.ceil(1 / _zoom(zoom)))


def sample_rgba(source, size, zoom, factor=1, pan=(0, 0), resampling="nearest"):
    """Sample RGBA pixels using the terminal's exact inverse affine mapping.

    ``source`` may already have been reduced by ``factor``. This function does
    not reduce or composite layers, so the canvas can reuse its cached source.
    ``size`` is in logical pixels; a terminal character holds two vertical ones.
    """
    if source.mode != "RGBA":
        raise ValueError("Sampling requires RGBA pixels.")
    size, zoom = _size(size), _zoom(zoom)
    if type(factor) is not int or factor < 1:
        raise ValueError("Reduction factor must be a positive integer.")
    if not isinstance(pan, (tuple, list)) or len(pan) != 2:
        raise ValueError("Pan must contain two finite coordinates.")
    try:
        pan_x, pan_y = (float(value) for value in pan)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Pan must contain two finite coordinates.") from error
    if not math.isfinite(pan_x) or not math.isfinite(pan_y):
        raise ValueError("Pan must contain two finite coordinates.")
    affine = (1 / zoom / factor, 0, pan_x / factor,
              0, 1 / zoom / factor, pan_y / factor)
    return source.transform(size, Image.Transform.AFFINE, affine, _resampling(resampling))

