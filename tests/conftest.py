"""Fixtures and reporting for the cross-distro sensor suite.

Every test here runs against every image in ``images.yml``. The image axis is applied
once, in ``pytest_generate_tests``, so no test repeats it and growing the list needs no
change to any test.
"""

import logging
from collections.abc import Iterator

import pytest
from loguru import logger

from harness.container import (
    DockerError,
    SensorContainer,
    ensure_image,
    remove_leftover_containers,
)
from harness.images import ImageListError, load_images, slug

_IMAGES_OPTION = "--sensor-images"

# Where the resolved list lives between pytest_configure and everything that needs it.
_images_key = pytest.StashKey[list]()


class _PropagateToStdlibHandler(logging.Handler):
    """Sink that hands every loguru record to stdlib logging."""

    def emit(self, record: logging.LogRecord) -> None:
        logging.getLogger(record.name).handle(record)


# loguru binds to the real sys.stderr when it is imported, which is not the stream
# pytest captures, so harness output would bypass log_cli entirely. Route it through
# stdlib logging instead, which pytest owns.
logger.remove()
logger.add(_PropagateToStdlibHandler(), level="DEBUG", format="{message}")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that replaces the image list for a single run."""
    parser.addoption(
        _IMAGES_OPTION,
        action="store",
        default=None,
        metavar="IMAGE[,IMAGE...]",
        help="Comma-separated images to test instead of the images.yml list.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Resolve the image list once, before collection starts.

    Here rather than in ``pytest_generate_tests``, which pytest calls once per test
    function: a list that cannot be read would otherwise be reported once per test.

    Raises:
        pytest.UsageError: If the list cannot be read, or names no image.
    """
    try:
        config.stash[_images_key] = load_images(config.getoption(_IMAGES_OPTION))
    except ImageListError as err:
        raise pytest.UsageError(str(err)) from err


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Run every test that needs a container once per image in the list.

    A decorator per test would let each one pick its own subset, but no test here wants
    a subset: the point of the suite is that one test body meets every image. Applying
    the axis in this hook keeps that true by construction.
    """
    if "image" not in metafunc.fixturenames:
        return

    images = metafunc.config.stash[_images_key]
    # The slug puts the distro in every test id, so a failure line says where it failed.
    metafunc.parametrize("image", images, ids=[slug(image) for image in images])


@pytest.fixture(scope="session", autouse=True)
def _docker_preflight(request: pytest.FixtureRequest) -> Iterator[None]:
    """Prove Docker answers and every image is present, before the first test runs.

    One clear message here beats the same failure repeated once per collected case, and
    it keeps a missing image from being reported as a sensor defect.
    """
    try:
        for image in request.config.stash[_images_key]:
            ensure_image(image)
    except DockerError as err:
        pytest.exit(f"the harness cannot use Docker: {err}", returncode=pytest.ExitCode.USAGE_ERROR)

    yield

    swept = remove_leftover_containers()
    if swept:
        logger.warning(f"swept {swept} container(s) that a test did not remove")


@pytest.fixture
def sensor(image: str) -> Iterator[SensorContainer]:
    """Yield a fresh container of ``image`` with the sensor installed in it.

    Function-scoped on purpose, and this is the most expensive decision in the harness.
    The sensor keeps state inside the container, so ``alerts`` reports what a ``scan``
    earlier in the same container stored. A container per test is what makes "empty
    before any scan" and "reports a prior scan" two honest tests instead of two
    orderings of one, and it needs no reset trick that depends on knowing where the
    sensor keeps that state.
    """
    with SensorContainer(image) as container:
        yield container


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[None]:
    """Stash each phase's report on the item, so the boundary fixture can read the outcome."""
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report


@pytest.fixture(autouse=True)
def _log_test_boundaries(request: pytest.FixtureRequest) -> Iterator[None]:
    """Log a line as each test starts and ends.

    Worth the noise here: the suite contains calls that spend their full 15s timeout,
    and a START line with no END after it says which one is waiting, while it waits.
    """
    logger.info(f"START {request.node.name}")
    yield
    report = getattr(request.node, "rep_call", None)
    outcome = report.outcome if report is not None else "setup-error"
    emit = {"passed": logger.success, "skipped": logger.warning}.get(outcome, logger.error)
    emit(f"END   {request.node.name} - {outcome.upper()}")
