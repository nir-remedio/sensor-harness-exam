# Findings

Four differences between `ubuntu:22.04` and `alpine:3.20`. Every one reproduces on every
run: the same distro and the same command misbehave the same way each time. Nothing here
is intermittent.

Two of the six (distro, command) pairs are clean.

## The table

| Distro | Command | Expected | Observed | Test that reports it |
| --- | --- | --- | --- | --- |
| `alpine:3.20` | `scan` | Exit 0, JSON Lines on stdout, stderr empty | Exit 0 and correct stdout, but one line on **stderr**: `sensor: warning: baseline database is 41 days old, results may be stale` | `TestScan::test_scan_writes_nothing_to_stderr[alpine-3.20]` |
| `ubuntu:22.04` | `alerts` | After a scan, one JSON record per stored finding | **Nothing at all.** Exit 0, stdout 0 bytes, stderr 0 bytes — even though the scan before it stored 3 alerts | `TestAlerts::test_alerts_reports_the_findings_of_a_prior_scan[ubuntu-22.04]` |
| `ubuntu:22.04` | `logs` | Exit 0, JSON Lines of events on stdout | **Exit 1**, stdout empty, one line on stderr: `sensor: logs failed: <reason>`. The reason is different on every run | `TestLogs::test_logs_reports_the_events_of_this_container[ubuntu-22.04]` |
| `alpine:3.20` | `logs` | Exit 0 promptly, JSON Lines of events on stdout | **Stalls for 90 s, then exits 7 with both streams empty.** Killed at the suite's 15 s timeout, four times out of four | `TestLiveness::test_every_command_returns_within_the_timeout[alpine-3.20-logs]` and `TestLogs::test_logs_reports_the_events_of_this_container[alpine-3.20]` |

Clean, and worth stating because it makes each defect a property of the pair and not of
the command: `ubuntu:22.04 scan` and `alpine:3.20 alerts` both behave correctly. So does
an unknown subcommand on both images — exit 2, usage on stderr, stdout empty.

## Which tests fail, and what each failure means

Five of 18 cases fail. All five are the sensor.

**`test_scan_writes_nothing_to_stderr[alpine-3.20]`**
Alpine's `scan` gets the answer right. It exits 0 and prints correct JSON Lines. It also
writes a warning to stderr that Ubuntu's never writes. A harness that merged the two
streams, or checked only the exit code, would call this a pass. This is the failure that
exists only because the two streams are captured apart.

**`test_alerts_reports_the_findings_of_a_prior_scan[ubuntu-22.04]`**
The worst of the four, because it is the quietest. `alerts` exits 0 and says nothing.
Nothing on stderr either. From the command alone it is indistinguishable from a correct
`alerts` with nothing to report.

It is not empty. It is silent. Evidence, black box, via `docker diff` on a container
where only a scan has run:

```
$ docker diff <container>
A /var/lib/sensor/alerts.jsonl
A /var/lib/sensor/events.jsonl
$ docker exec <container> wc -l /var/lib/sensor/alerts.jsonl
3 /var/lib/sensor/alerts.jsonl
$ docker exec <container> sensor alerts
   → exit 0, stdout 0 bytes, stderr 0 bytes
```

The scan stored three findings. `alerts` reported none of them. The same sequence on
`alpine:3.20` reports them correctly, so the sequence is not the problem.

**`test_logs_reports_the_events_of_this_container[ubuntu-22.04]`**
Honest failure — it exits 1 and says so on stderr. The only thing that surprised me is
the message.

**`test_logs_reports_the_events_of_this_container[alpine-3.20]`** and
**`test_every_command_returns_within_the_timeout[alpine-3.20-logs]`**
Both report the same defect, on purpose. The first says `logs` did not produce a log; the
second says it did not come back at all. Keeping them apart means a hang is never filed
as "wrong output". Both cost 15 s, and that is 30 s of the suite's 37 s.

The suite never learns how the stall ends, because 15 s is all it will spend. I found out
once, by hand and outside the suite: it blocks for **90.1 s**, then exits **7** with
stdout and stderr both empty. That belongs in a findings document and not in a test. A
test that waited 90 s to confirm it would cost the suite three minutes per run to learn
something that never changes, and a test that waits long enough to be sure is a test that
does not report.

## What surprised me

**The Ubuntu `logs` error message is different on every single run.** Five runs, five
sentences:

```
sensor: logs failed: cannot open /proc/net/tcp, permission denied
sensor: logs failed: check module cis_audit returned no data
sensor: logs failed: unsupported init system, cannot enumerate services
sensor: logs failed: timed out reading /etc/shadow
sensor: logs failed: the baseline database is locked by another process
```

Only the prefix and the exit code hold. Reading the first one, I would have written a
test that matched `permission denied` and chased a phantom regression on the next run.
The randomness reaches the error text, not just the payload.

**A silent success is worse than a loud failure.** Alpine's `logs` hang is obvious the
first time you run it. Ubuntu's `alerts` looks perfect: exit 0, clean streams, no
diagnostics. It took a second command in the same container to see anything wrong.

**Exit codes are informative and the sensor uses several of them.** 0, 1, 2 and 7 all
appeared, and they mean different things. A harness that only asked "was it non-zero"
would have thrown that away.

**The 90 s stall ends in the least useful way possible.** Exit 7, nothing on stdout,
nothing on stderr. A caller that patiently waited the full 90 s would be told less than
one that gave up at 15 s, because at least the timeout says which command stopped
answering.

## A note on `test_alerts_reports_nothing_before_any_scan_has_run`

This test passes on both images, and on `ubuntu:22.04` it passes for the wrong reason.
"Reports nothing because nothing was found" and "reports nothing, ever" are identical
from inside it.

It is kept, and its docstring says so. It pins real behaviour — a fresh container should
have no alerts — and it is the direct answer to the assignment's question about what
would still pass if the sensor were completely broken. The test below it is what tells
the two cases apart. A green result here means something only when that one is green too.

---

## Reflection

> Draft. Rewrite in your own words before this is submitted.

I built the discovery script first, before any test, because the assignment says not to
assume the sensor is well behaved and I had no idea what "well behaved" would even look
like. Running all three commands in a fresh container of each image, twice, told me the
output was JSON Lines with random payloads and told me which pairs were broken — and
that shaped every assertion afterwards into a shape check rather than a value check.
The container plumbing came second, because a hanging command has to be survivable
before it can be reported; the tests came third and were quick, once the result object
carried `timed_out` and the two streams separately. Running it, not reading about it, is
the only way I learned that the Ubuntu `logs` error text is different on every
invocation — five runs gave five different sentences, so even the error message is
unassertable. I chose to test stderr on a command that otherwise succeeds, which is the
only reason the Alpine `scan` defect is visible at all; I chose not to test any payload
content — feature names, finding text, record counts — because those are random by
design and such a test would fail for reasons that are not defects. The moment I
overrode the AI was in `_docker`: the first version raised one `DockerError` for both
"the call timed out" and "docker is not on PATH", which meant a machine with no Docker
would have been reported as "the sensor never returned" — a fabricated finding in the
deliverable. I split out `DockerTimeout` so only a real overrun can ever produce that
claim.
