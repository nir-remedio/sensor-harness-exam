# Sensor Test Harness

A pytest suite that runs the same tests against the sensor inside a container of every
image in [`images.yml`](images.yml), and reports what each one did.

**The findings are the result of this exercise. They are in [FINDINGS.md](FINDINGS.md).**
The assignment this answers is in [ASSIGNMENT.md](ASSIGNMENT.md).

| Area | Stack |
| --- | --- |
| Language | Python 3.10+ |
| Dependency manager | `uv` |
| Test framework | `pytest`, with `pytest-timeout` as a backstop |
| Containers | Docker CLI, driven from the host |
| Lint and format | `ruff` |

## Quick start

```bash
# 1. Pull the images (not needed if you already have them; the suite pulls a missing
#    one itself, but a cold pull inside the run is slow and easy to misread).
docker pull ubuntu:22.04
docker pull alpine:3.20

# 2. Run the whole suite. This is the single command.
uv run pytest
# → 5 failed, 13 passed in ~37s
```

> **Five failures are the expected result, not a broken harness.** Each one is a sensor
> defect, named with its distro and command in [FINDINGS.md](FINDINGS.md). Do not read a
> green run as success here — a suite where everything passed would mean the tests
> stopped looking.

`uv` builds the virtual environment on first run. Without `uv`:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

## What is in here

| Path | Purpose |
| --- | --- |
| [`images.yml`](images.yml) | **The image list.** The only file to change to add an image. |
| [`sensor/`](sensor/) | The two binaries, exactly as given. Never modified. |
| [`harness/container.py`](harness/container.py) | Starts a container, installs the sensor, runs one command, captures the result. |
| [`harness/images.py`](harness/images.py) | Reads the list and builds the slug that names an image in a test id. |
| [`harness/paths.py`](harness/paths.py) | Finds the repo root and the right sensor binary. |
| [`tests/conftest.py`](tests/conftest.py) | Applies the image axis, the preflight, and the container fixture. |
| [`tests/test_sensor.py`](tests/test_sensor.py) | The seven tests. |
| [FINDINGS.md](FINDINGS.md) | What the run found. |

## Where the image list lives

[`images.yml`](images.yml), at the repo root.

```yaml
images:
  - ubuntu:22.04
  - alpine:3.20
```

Adding an image adds a full set of cases for it. Nothing else moves: no new test code,
no second copy of a test file, no branch inside a test. Six images tomorrow is six lines
in this file.

The image axis is applied once, in `pytest_generate_tests`, so no test declares it. Each
image's slug goes into every test id, which is why a failure line reads
`test_scan_writes_nothing_to_stderr[alpine-3.20]` and says where it failed.

Prove it without editing anything:

```bash
uv run pytest --collect-only -q --sensor-images "ubuntu:22.04,alpine:3.20,debian:12"
# → 27 tests collected
```

An image needs a shell, `uname` and `tail`. Nothing else. Tags are pinned, because
`ubuntu:latest` would quietly change what the findings describe.

## How the sensor gets into the container

`docker cp`, into a container started from the stock image. No Dockerfile, no build, no
registry.

1. `docker run --detach <image> tail -f /dev/null` holds a bare image open.
2. `docker exec <container> uname -m` asks the container which architecture it is.
3. `docker cp sensor/sensor-<arch> <container>:/usr/local/bin/sensor` installs it.
4. Tests call `docker exec <container> sensor <command>`.

**The architecture comes from inside the container, never from the host.** An amd64
image running under emulation on an arm64 host answers `x86_64`, and `sensor-amd64` is
the build it can actually execute. Asking the host would get that backwards. `uname -m`
answers are mapped in `_ARCH_BY_MACHINE`; a machine name with no build raises, naming
the image.

The binaries are only ever read. `git status sensor/` stays clean across a run.

**A container per test**, not per image. This is the most expensive decision in the
harness, at about 0.2 s of setup per test. It is worth it: the sensor keeps state inside
the container, so `alerts` reports what a `scan` earlier in the same container stored.
A fresh container is what makes "empty before any scan" and "reports a prior scan" two
honest tests rather than two orderings of one, and it needs no reset trick.

## Harness design choice

**Approach B: pytest runs on the host and drives the containers from outside.**

`alpine:3.20` ships no Python and `ubuntu:22.04` ships no pip, so approach A means
building a Python toolchain into every image. That is setup cost that grows with the
image list, and it changes the thing under test — the sensor would no longer be running
on a stock image.

Driving from outside also keeps the reporter alive when the sensor does not come back.
The host process owns the timeout and can force-remove the container. A pytest running
inside a wedged container has nobody left to report. Since one of the three commands
turns out never to return, that is not a hypothetical.

## How it is kept from hanging

The suite must always produce a report. Four things guarantee it:

- Every call out to `docker` carries a timeout. A sensor command gets 15 s; docker's own
  bookkeeping gets 60 s; a cold pull gets 300 s. The numbers and their reasoning are at
  the top of [`harness/container.py`](harness/container.py).
- A sensor call that overruns returns a `CommandResult` with `timed_out` set, rather than
  raising. A hang is a finding for a test to report, not an error that ends the run. It
  is never retried.
- Teardown uses `docker rm --force`. Killing the `docker exec` client does **not** kill
  the sensor inside the container, so the force removal is what actually ends a command
  still sleeping in there. `docker stop` would wait out its grace period first.
- `pytest-timeout` sits underneath at 120 s per test, and should never fire. It is
  deliberately left on the default method rather than `--timeout-method=thread`: the
  thread method kills through `os._exit` and skips teardown, which would leak a container
  every time it fired. A session finalizer sweeps any `sensor-harness-*` container that
  survives anyway.

Wall clock is about 37 s for 18 cases. 30 s of that is the two cases that wait out the
15 s timeout on the command that never returns.

## Why there are no xfail markers

Every failure in this suite is a real sensor defect that reproduces on every run. Marking
them `xfail` would turn the report green and move the findings into marker strings. The
assignment allows the marker; it is not the right tool when the defects **are** the
deliverable. The five failures and [FINDINGS.md](FINDINGS.md) say the same thing, and the
suite output is the part nobody can skip reading.

## Common commands

```bash
uv run pytest                                # the whole suite, both images
uv run pytest -k alpine-3.20                 # one image
uv run pytest -k TestLogs                    # one behaviour, every image
uv run pytest --sensor-images ubuntu:24.04   # an image that is not in the list
uv run ruff check . && uv run ruff format .  # lint and format
docker ps -a --filter name=sensor-harness    # should be empty after any run
```
