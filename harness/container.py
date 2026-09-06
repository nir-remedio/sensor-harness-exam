"""Run the sensor inside a throwaway container of one image, from the host.

The suite runs on the host and reaches the sensor through ``docker exec``. Nothing is
installed into the images: a container is started from the stock image, asked which
architecture it is, given the matching sensor build with ``docker cp``, and removed
when the test that owns it ends.

Every call out to docker carries a timeout, because the thing under test is known to
include a command that does not return. A sensor call that overruns comes back as a
result with ``timed_out`` set, not as an exception, so the test that made it reports a
hang as a finding instead of erroring out of the run.
"""

import json
import subprocess
import time
import uuid
from enum import Enum
from types import TracebackType
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict

from harness.paths import sensor_binary

# One sensor command. Every healthy command in either image answers in under a tenth
# of a second, measured; the outlier is a command that does not answer at all, and it
# has to be waited out on every run. 15s is far past any real answer, and cheap enough
# that the cases that hit it cost the suite half a minute in total.
SENSOR_CALL_TIMEOUT_SECONDS = 15.0

# Docker's own bookkeeping: run, exec uname, cp, rm. Each measures well under a second
# against a warm daemon. 60s is headroom for a daemon busy tearing down another test,
# not a normal cost.
DOCKER_ADMIN_TIMEOUT_SECONDS = 60.0

# A cold `docker pull`. Only the preflight uses it, and only for an image this host
# does not already hold.
DOCKER_PULL_TIMEOUT_SECONDS = 300.0

# What `uname -m` inside a container calls each architecture the sensor ships a build
# for. Asked of the container rather than the host on purpose: an amd64 image running
# under emulation on an arm64 host answers `x86_64`, and that is the build it can run.
_ARCH_BY_MACHINE = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

# Every container this harness starts is named with this prefix, so a sweep can find
# one that a killed run left behind.
CONTAINER_NAME_PREFIX = "sensor-harness"

_SENSOR_PATH_IN_CONTAINER = "/usr/local/bin/sensor"


class DockerError(Exception):
    """Raised when a docker command the harness depends on fails."""


class DockerTimeout(DockerError):
    """Raised when a docker command does not return inside its timeout.

    Carries whatever the command had already written, so a call that prints something
    and then stalls still leaves that evidence behind.
    """

    def __init__(self, message: str, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class SensorCommand(str, Enum):
    """A subcommand of the sensor under test."""

    SCAN = "scan"
    ALERTS = "alerts"
    LOGS = "logs"

    def __str__(self) -> str:
        return self.value


class CommandResult(BaseModel):
    """One invocation of the sensor: its two streams, its exit code, and how long it took."""

    model_config = ConfigDict(frozen=True)

    args: tuple[str, ...]
    # None when the call was killed at its timeout: there was no exit code to read.
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    @property
    def command(self) -> str:
        """Return the invocation as it would be typed, for use in a failure message."""
        return " ".join(("sensor", *self.args))

    @property
    def stdout_lines(self) -> list[str]:
        """Return the non-blank lines of stdout."""
        return [line for line in self.stdout.splitlines() if line.strip()]

    @property
    def stderr_lines(self) -> list[str]:
        """Return the non-blank lines of stderr."""
        return [line for line in self.stderr.splitlines() if line.strip()]

    def json_lines(self) -> list[dict[str, Any]]:
        """Return stdout parsed as JSON Lines, one object per non-blank line.

        Raises:
            ValueError: If any line is not a JSON object. The message names the line,
                so a malformed answer reads as the finding it is.
        """
        records: list[dict[str, Any]] = []
        for number, line in enumerate(self.stdout_lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as err:
                raise ValueError(
                    f"`{self.command}` stdout line {number} is not JSON: {line!r}"
                ) from err
            if not isinstance(record, dict):
                raise ValueError(
                    f"`{self.command}` stdout line {number} is not a JSON object: {line!r}"
                )
            records.append(record)
        return records


class SensorContainer:
    """A running container of one image, with the sensor installed and ready to call."""

    def __init__(self, image: str) -> None:
        self.image = image
        # Unique per container, so a parallel run cannot collide and a leftover can be
        # traced back to this harness.
        self.name = f"{CONTAINER_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"
        self._arch: str | None = None

    def start(self) -> "SensorContainer":
        """Start the container and install the sensor build matching its architecture.

        Returns:
            This container, so the call can be chained.

        Raises:
            DockerError: If the container will not start, or reports an architecture
                the repository ships no build for.
        """
        # `tail -f /dev/null` holds a base image open with no init system and nothing
        # installed. That keeps the image requirement to a shell, `uname` and `tail`.
        _docker_checked(
            "run", "--detach", "--name", self.name, self.image, "tail", "-f", "/dev/null"
        )
        try:
            machine = _docker_checked("exec", self.name, "uname", "-m").strip()
            if machine not in _ARCH_BY_MACHINE:
                raise DockerError(
                    f"{self.image} reports `uname -m` {machine!r}, "
                    f"which the sensor ships no build for"
                )
            self._arch = _ARCH_BY_MACHINE[machine]
            _docker_checked(
                "cp",
                str(sensor_binary(self._arch)),
                f"{self.name}:{_SENSOR_PATH_IN_CONTAINER}",
            )
        except (DockerError, FileNotFoundError):
            # Do not leave a half-built container behind for the sweep to find.
            self.stop()
            raise

        logger.info(f"{self.name}: {self.image} is {machine}, running sensor-{self._arch}")
        return self

    def run(self, *args: object, timeout: float = SENSOR_CALL_TIMEOUT_SECONDS) -> CommandResult:
        """Run the sensor with ``args`` inside the container and capture what it did.

        stdout and stderr are captured apart and never merged: a command that answers
        correctly on stdout can still complain on stderr, and that difference is one of
        the things the suite exists to notice.

        A call that overruns ``timeout`` returns with ``timed_out`` set instead of
        raising, and is not retried. A sensor that does not return is a finding for the
        test to report, not an error in the harness.

        Args:
            *args: The sensor's own arguments, e.g. ``SensorCommand.SCAN``.
            timeout: Seconds to wait for the call to return.
        """
        argv = tuple(str(arg) for arg in args)
        started = time.monotonic()
        try:
            completed = _docker("exec", self.name, "sensor", *argv, timeout=timeout)
        except DockerTimeout as err:
            # Killing the `docker exec` client does not kill the sensor inside the
            # container. `docker rm --force` in stop() is what actually ends it.
            logger.warning(f"{self.name}: `sensor {' '.join(argv)}` did not return in {timeout:g}s")
            return CommandResult(
                args=argv,
                exit_code=None,
                stdout=err.stdout,
                stderr=err.stderr,
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )

        return CommandResult(
            args=argv,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=False,
        )

    def stop(self) -> None:
        """Remove the container and whatever is still running inside it. Never raises.

        ``--force`` is what ends a sensor still sleeping in there; ``docker stop`` would
        wait out its grace period first. This runs in teardown, where raising would
        replace the test's own failure with an unrelated one, so a removal that fails is
        logged and swallowed.
        """
        try:
            completed = _docker("rm", "--force", "--volumes", self.name)
        except DockerError as err:
            logger.warning(f"{self.name}: could not be removed: {err}")
            return
        if completed.returncode != 0:
            logger.warning(f"{self.name}: could not be removed: {completed.stderr.strip()}")

    def __enter__(self) -> "SensorContainer":
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()


def ensure_image(image: str) -> None:
    """Make sure ``image`` is on this host, pulling it once if it is not.

    Raises:
        DockerError: If the image is neither present nor pullable.
    """
    if _docker("image", "inspect", "--format", "{{.Id}}", image).returncode == 0:
        return
    logger.info(f"pulling {image}, this host does not hold it yet")
    _docker_checked("pull", image, timeout=DOCKER_PULL_TIMEOUT_SECONDS)


def remove_leftover_containers() -> int:
    """Remove any container this harness left behind and return how many. Never raises.

    Insurance for the case the per-test teardown could not run at all, such as the
    pytest-timeout backstop firing or the run being killed from the terminal.
    """
    try:
        listed = _docker("ps", "--all", "--quiet", "--filter", f"name={CONTAINER_NAME_PREFIX}-")
    except DockerError as err:
        logger.warning(f"could not list leftover containers: {err}")
        return 0

    container_ids = listed.stdout.split()
    if not container_ids:
        return 0

    try:
        _docker("rm", "--force", "--volumes", *container_ids)
    except DockerError as err:
        logger.warning(f"could not remove leftover containers: {err}")
        return 0
    return len(container_ids)


def _docker(
    *args: str, timeout: float = DOCKER_ADMIN_TIMEOUT_SECONDS
) -> "subprocess.CompletedProcess[str]":
    """Run one docker command and return it, whatever its exit code.

    Raises:
        DockerTimeout: If the command does not return within ``timeout``.
        DockerError: If the docker client is not on PATH.
    """
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as err:
        raise DockerError("docker is not on PATH") from err
    except subprocess.TimeoutExpired as err:
        raise DockerTimeout(
            f"`docker {' '.join(args)}` did not return in {timeout:g}s",
            stdout=_as_text(err.stdout),
            stderr=_as_text(err.stderr),
        ) from err


def _docker_checked(*args: str, timeout: float = DOCKER_ADMIN_TIMEOUT_SECONDS) -> str:
    """Run one docker command that has to succeed, and return its stdout.

    Raises:
        DockerError: If the command exits non-zero. The message names the command, so
            a bare "no such container" says which call produced it.
    """
    completed = _docker(*args, timeout=timeout)
    if completed.returncode != 0:
        raise DockerError(
            f"`docker {' '.join(args)}` exited {completed.returncode}: "
            f"{completed.stderr.strip() or '<no stderr>'}"
        )
    return completed.stdout


def _as_text(value: str | bytes | None) -> str:
    """Return partial output captured on a timeout as text, whatever form it arrived in."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
