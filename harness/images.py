"""The list of images under test, and the slug that names one of them in a test id."""

import re

import yaml

from harness.paths import repo_root

IMAGES_FILE = "images.yml"

# Everything a pytest id and a `-k` expression handle without quoting.
_UNSAFE_IN_ID = re.compile(r"[^A-Za-z0-9._-]+")


class ImageListError(Exception):
    """Raised when the image list cannot be read, or holds nothing to test."""


def slug(image: str) -> str:
    """Return a test-id-safe name for ``image``: ``ubuntu:22.04`` becomes ``ubuntu-22.04``."""
    return _UNSAFE_IN_ID.sub("-", image).strip("-")


def load_images(override: str | None = None) -> list[str]:
    """Return the images to test: ``override`` when given, else the images.yml list.

    Args:
        override: Comma-separated image names from ``--sensor-images``, or None to
            read the file.

    Returns:
        The image names, in file order, with repeats dropped.

    Raises:
        ImageListError: If the file is missing or malformed, or the list is empty.
    """
    if override is not None:
        source = "--sensor-images"
        names = override.split(",")
    else:
        source = str(repo_root() / IMAGES_FILE)
        names = _read_images_file()

    return _deduplicated(names, source)


def _read_images_file() -> list[str]:
    """Return the raw entries of the ``images:`` list in images.yml.

    Raises:
        ImageListError: If the file cannot be read or is not shaped as expected.
    """
    path = repo_root() / IMAGES_FILE
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ImageListError(f"cannot read {path}: {err}") from err
    except yaml.YAMLError as err:
        raise ImageListError(f"{path} is not valid YAML: {err}") from err

    if not isinstance(document, dict) or not isinstance(document.get("images"), list):
        raise ImageListError(f"{path} must hold a top-level `images:` list")
    return [str(entry) for entry in document["images"]]


def _deduplicated(names: list[str], source: str) -> list[str]:
    """Return the non-blank names in order, without repeats.

    A repeated image would collect twice under ids ``ubuntu-22.04`` and
    ``ubuntu-22.041``, so ``-k ubuntu-22.04`` would select only one of them.

    Raises:
        ImageListError: If nothing is left after blanks are dropped. An empty
            parametrize collects no cases at all, and a run of no cases is green --
            it would read as coverage the suite does not have.
    """
    unique: list[str] = []
    for name in names:
        stripped = name.strip()
        if stripped and stripped not in unique:
            unique.append(stripped)

    if not unique:
        raise ImageListError(f"{source} names no image, so the suite would collect nothing")
    return unique
