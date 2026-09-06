# Sensor Test Harness

- **Time:** 90 minutes
- **Stack:** Python 3.10+, pytest, Docker

## Background

We ship an agent that runs on many Linux distributions. It behaves differently on each one. Today we learn about those differences from customers, not from CI.

Your task is to build the first version of a cross-distro test harness. It runs the same tests against the agent inside several containers. It reports what happened on each.

We give you the agent. You build everything around it.

## Before the clock starts

Pull these two images first:

- `ubuntu:22.04`
- `alpine:3.20`

Download time is not part of the 90 minutes.

## What we give you

In `./sensor/` you get two files: `sensor-amd64` and `sensor-arm64`. Nothing else.

The sensor supports three commands:

| Command | Purpose |
| --- | --- |
| `sensor scan` | Runs a scan and prints a result |
| `sensor alerts` | Prints current alerts |
| `sensor logs` | Prints the agent log |

That is all we will tell you about it. Part of the task is to find out how it actually behaves. Run it. Read the exit codes. Read stdout and stderr, separately. Do not assume it is well behaved.

**The two files are the same program, built for two CPU architectures.** Pick the one that matches the container, and make that choice repeatable. Some hosts run the wrong file anyway, through emulation. Others refuse it. If the sensor does not start at all, look at your setup first. That failure is not a sensor defect, and it is not a defect in your harness.

**The output content changes on every run.** Two runs of the same command in the same container do not print the same lines. A test that expects a payload you saw once is the wrong test. Assert on the shape of the output, and on the rules that must hold every time.

## What "works" means here

The sensor misbehaves. It misbehaves on both distros, and not in the same way on each. For one distro, the same command misbehaves the same way every time. To find out what, where and how is the exam.

Not every command works on every image. One might work on neither.

So read this part carefully.

**A test that fails because the sensor is broken is a correct result.** Do not edit the test until it goes green. Do not delete it. Do not weaken the assertion. Report it.

We grade two things:

1. **The harness works.** One command runs the whole suite. The run finishes. It never hangs. It always gives a report, for both distros.
2. **The findings are right.** Each defect you found is named, with the distro, the command and the evidence.

We do not score the number of green tests. A suite where everything passes tells us that you did not look.

You may mark a known sensor defect as an expected failure. If you do, name the defect in the marker and in your findings. A marker with no reason reads as a hidden failure.

## What to build

### 1. The sensor, inside the containers

The sensor must run inside a container of each distro:

- `ubuntu:22.04`
- `alpine:3.20`

Every command must be exercised inside every container. Not on your host.

How the binary gets into the container is your decision. We do not tell you. More than one method works. Choose one, make it repeatable, and write down what you did.

Both distros must work. The list must also be cheap to grow. Assume we give your harness a list of six images tomorrow. Only the list may change: no new test code, no second copy of a test file, no new branch inside a test. Tell us in your README where the list lives.

### 2. The harness

A pytest suite that runs every test against every distro.

Requirements:

- Tests are written once and run against every image. Two copies of the same test file is not acceptable.
- Use a fixture to provide the container or the distro under test.
- Use `pytest.mark.parametrize` where it fits. Do not force it.
- The distro must appear in the test ID, so a failure report tells us where it failed.
- Every call to the sensor has a timeout. A test that waits forever never reports.
- Running the full suite takes one command. Document it in your README.

The whole suite should finish in a couple of minutes. If it takes much longer, something is waiting.

**Harness design choice.** There are two reasonable approaches:

- **A:** Install `pytest` inside each image and run the tests there.
- **B:** Run `pytest` on the host and drive the containers from outside.

Pick one. Write two or three sentences in your README on why. We care about the reasoning, not the choice. There is no reference image, so count the setup cost of your choice before you commit to it.

### 3. The tests

5 to 8 tests in total. Quality over volume.

Cover the behaviour that is worth checking. Some things to think about:

- What is a real success for each command, beyond exit code 0?
- What happens when a command produces no output?
- What happens when a command does not finish?
- What does the sensor write to stderr, and does your test notice?
- Does one command need another command to run first?
- What would still pass if the sensor were completely broken?

Each test needs a clear name that says which behaviour it verifies.

## Constraints

- Python 3.10 or later, pytest, Docker.
- Do not modify the sensor executable.
- No authentication, no CI config, no production concerns.

Keep the last 10 minutes for the write-up. The findings are what we read first.

## What to submit

1. The code. The harness, the tests, and whatever you use to put the sensor in a container. Any sensible structure.

2. A README with:

   - How to prepare the containers.
   - The single command that runs the suite.
   - Where the list of images lives.
   - Your harness design choice, and why.

3. A findings section, in the README or in a separate file. This is the main result of the exam. Give us one row for each difference you found:

   | Distro | Command | Expected | Observed | Test that reports it |
   | --- | --- | --- | --- | --- |

   Then add:

   - Which tests fail, where, and what each failure means.
   - Anything about the sensor that surprised you.

   Short entries are fine. Be exact about the distro and the command.

4. A short reflection (4 to 6 sentences):

   - What you built first, second and third, and why in that order.
   - One thing you learned about the sensor by running it, not by reading about it.
   - One behaviour you chose to test, and one you chose not to test. Why.
   - One moment where you corrected or overrode what an AI tool produced.
