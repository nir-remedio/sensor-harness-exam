"""Behaviour of the sensor, run identically inside every image in ``images.yml``.

The sensor prints different content on every run: which features a scan picks, which
findings it reports, and the wording of its own error messages all change between two
runs of the same command in the same container. So nothing here asserts on a payload.
Every assertion is about shape -- did the call return, what exit code, which stream
carried the output, which JSON keys each record has -- or about a rule that has to hold
whatever the sensor decided to do this time, such as "alerts may only name a feature
the scan actually checked".
"""

from datetime import datetime
from typing import Any

import pytest

from harness.container import CommandResult, SensorCommand, SensorContainer

# The exact key set each command's records must carry. Exact rather than "at least":
# a record that grew a key is a change worth reporting too.
SCAN_KEYS = {"logged_time", "scanned_feature"}
ALERT_KEYS = {"logged_time", "scanned_feature", "invalid_feature"}
LOG_KEYS = {"logged_time", "event_message"}


def assert_jsonl(result: CommandResult, expected_keys: set[str]) -> list[dict[str, Any]]:
    """Assert ``result`` is a successful JSON Lines answer, and return its records.

    A plain function rather than a fixture: it needs nothing from pytest's lifecycle,
    and reading it at the call site is the point.

    Args:
        result: The invocation to check.
        expected_keys: The keys every record must carry, and only those.

    Returns:
        The parsed records, so the caller can assert on the rules between them.
    """
    assert not result.timed_out, (
        f"`{result.command}` never returned; it was killed after {result.duration_seconds:.1f}s"
    )
    assert result.succeeded, (
        f"`{result.command}` exited {result.exit_code}, expected 0. "
        f"stderr: {result.stderr.strip() or '<empty>'}"
    )
    assert result.stdout_lines, (
        f"`{result.command}` exited 0 but wrote nothing to stdout. "
        f"stderr: {result.stderr.strip() or '<empty>'}"
    )

    records = result.json_lines()
    for number, record in enumerate(records, start=1):
        assert set(record) == expected_keys, (
            f"`{result.command}` line {number} carries {sorted(record)}, "
            f"expected exactly {sorted(expected_keys)}"
        )
        for key, value in record.items():
            assert isinstance(value, str) and value.strip(), (
                f"`{result.command}` line {number}: {key} is {value!r}, expected a non-empty string"
            )
        _assert_rfc3339(record["logged_time"], f"`{result.command}` line {number}")
    return records


def _assert_rfc3339(value: str, where: str) -> None:
    """Assert ``value`` is an RFC 3339 timestamp, the way the sensor writes one."""
    # `Z` is valid RFC 3339 but fromisoformat only accepts it from Python 3.11.
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        pytest.fail(f"{where}: logged_time {value!r} is not an RFC 3339 timestamp ({err})")


class TestScan:
    """`sensor scan` -- what the sensor checked on this run."""

    def test_scan_reports_every_checked_feature_as_a_json_line(
        self, sensor: SensorContainer
    ) -> None:
        """Scan exits 0 and prints one well-formed record per feature it checked."""
        result = sensor.run(SensorCommand.SCAN)

        records = assert_jsonl(result, SCAN_KEYS)

        features = [record["scanned_feature"] for record in records]
        assert len(set(features)) == len(features), (
            f"`{result.command}` reported the same feature more than once: {sorted(features)}"
        )

    def test_scan_writes_nothing_to_stderr(self, sensor: SensorContainer) -> None:
        """Scan leaves stderr empty, so nothing is wrong that the exit code did not say.

        Deliberately split from the test above. A command can print a correct answer on
        stdout and exit 0 while still complaining on stderr, and only a test that reads
        the two streams apart sees it. Merging the streams -- or checking the exit code
        alone -- would call that a pass.
        """
        result = sensor.run(SensorCommand.SCAN)

        assert result.stderr_lines == [], (
            f"`{result.command}` exited {result.exit_code} with correct stdout, but wrote "
            f"{len(result.stderr_lines)} line(s) to stderr: {result.stderr.strip()}"
        )


class TestAlerts:
    """`sensor alerts` -- what scans in this container have found so far."""

    def test_alerts_reports_nothing_before_any_scan_has_run(self, sensor: SensorContainer) -> None:
        """Alerts exits 0 and says nothing in a container where no scan has run yet.

        This is the test that would still pass if the sensor were completely broken:
        "reports nothing because nothing was found" and "reports nothing, ever" look
        identical from here. It is kept because a fresh container really should have no
        alerts, and it is paired with the test below, which is what tells the two apart.
        Read a green result here as meaningful only when that one is green too.
        """
        result = sensor.run(SensorCommand.ALERTS)

        assert not result.timed_out, (
            f"`{result.command}` never returned; it was killed after {result.duration_seconds:.1f}s"
        )
        assert result.succeeded, (
            f"`{result.command}` exited {result.exit_code}, expected 0. "
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
        assert result.stdout_lines == [], (
            f"`{result.command}` reported {len(result.stdout_lines)} alert(s) in a container "
            f"where nothing has been scanned"
        )

    def test_alerts_reports_the_findings_of_a_prior_scan(self, sensor: SensorContainer) -> None:
        """Alerts reports what the scan before it found, and names no feature it skipped.

        The pairing is the real assertion. Which features get scanned is random, so the
        only thing that must hold on every run is that alerts is a subset of what the
        scan in the same container actually looked at.
        """
        scan = sensor.run(SensorCommand.SCAN)
        scanned = {record["scanned_feature"] for record in assert_jsonl(scan, SCAN_KEYS)}

        result = sensor.run(SensorCommand.ALERTS)

        records = assert_jsonl(result, ALERT_KEYS)
        reported = {record["scanned_feature"] for record in records}
        assert reported <= scanned, (
            f"`{result.command}` raised alerts for features the scan never checked: "
            f"{sorted(reported - scanned)}"
        )


class TestLogs:
    """`sensor logs` -- the sensor's own account of what it did."""

    def test_logs_reports_the_events_of_this_container(self, sensor: SensorContainer) -> None:
        """Logs exits 0 and prints the container's events, including the scan just run.

        A scan runs first so there is something that must appear. Without it, a log that
        is merely empty would be indistinguishable from a log that works.
        """
        assert_jsonl(sensor.run(SensorCommand.SCAN), SCAN_KEYS)

        result = sensor.run(SensorCommand.LOGS)

        records = assert_jsonl(result, LOG_KEYS)
        assert any("scan" in record["event_message"] for record in records), (
            f"`{result.command}` logged {len(records)} event(s) and none mentions the scan "
            f"that just ran: {[record['event_message'] for record in records]}"
        )


class TestLiveness:
    """Whether the sensor comes back at all, separately from what it says."""

    @pytest.mark.parametrize("command", list(SensorCommand), ids=str)
    def test_every_command_returns_within_the_timeout(
        self, sensor: SensorContainer, command: SensorCommand
    ) -> None:
        """Every command returns, whatever it decides to say.

        Kept apart from the correctness tests so a command that never returns is
        reported as its own failure. A suite that only checked output would record a
        hang as "wrong output" and lose the distinction between a sensor that answers
        badly and one that does not answer.

        This is also the test that exercises all three commands in every image, so no
        pair goes unvisited even where no other test looks at it.
        """
        result = sensor.run(command)

        assert not result.timed_out, (
            f"`{result.command}` never returned; it was killed after {result.duration_seconds:.1f}s"
        )


class TestCommandLine:
    """The sensor's front door: what it does with an argument it does not know."""

    def test_an_unknown_command_is_refused_with_usage_on_stderr(
        self, sensor: SensorContainer
    ) -> None:
        """An unknown subcommand exits non-zero and explains itself on stderr, not stdout.

        A control. It shares no code path with the three real commands, so a green
        result here says the harness reports non-zero exits and reads stderr correctly,
        and that a failure elsewhere is the sensor's and not the harness's.
        """
        result = sensor.run("bogus")

        assert not result.timed_out, f"`{result.command}` never returned"
        assert not result.succeeded, (
            f"`{result.command}` exited 0; an unknown subcommand must not look like success"
        )
        assert result.stdout_lines == [], (
            f"`{result.command}` wrote a diagnostic to stdout: {result.stdout.strip()}"
        )
        assert any("usage" in line.lower() for line in result.stderr_lines), (
            f"`{result.command}` exited {result.exit_code} without printing usage. "
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
