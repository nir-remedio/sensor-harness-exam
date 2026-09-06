"""Filesystem locations the harness needs, resolved from the repository root."""

from functools import lru_cache
from pathlib import Path

# What marks the repository root. Counting `parents[n]` instead would silently pick
# the wrong directory the moment a module moves.
_ROOT_MARKER = "pyproject.toml"

SENSOR_DIR_NAME = "sensor"


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Return the repository root, found by walking up to the marker file.

    Raises:
        RuntimeError: If no directory above this module holds the marker.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / _ROOT_MARKER).is_file():
            return candidate
    raise RuntimeError(f"no {_ROOT_MARKER} in any directory above {here}")


def sensor_binary(arch: str) -> Path:
    """Return the sensor executable built for ``arch``, e.g. ``arm64``.

    Raises:
        FileNotFoundError: If the repository ships no binary for ``arch``.
    """
    path = repo_root() / SENSOR_DIR_NAME / f"sensor-{arch}"
    if not path.is_file():
        raise FileNotFoundError(f"no sensor build for {arch}: {path} does not exist")
    return path
