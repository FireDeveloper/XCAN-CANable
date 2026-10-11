"""Bus-on report: an error state left from the previous session shows at the next bus-on, and clears once frames get acknowledged.

Usage:
    python tests/bus_on_state.py

Session 1: board B listens only, so board A's frame goes unacknowledged and A turns error passive.
Session 2: A rejoins without traffic; the controller keeps its error counters, so the passive state must show at once.
Session 3: B acknowledges again; once A's frames go through, A must report error active again.
With the probe, A's error counters are shown after each session.
"""

import sys
import time

import bench
import probe

#& Settings

KBPS = 500  # bit rate of every session
HEAVY = 0x00008  # status flag for an error counter at warning level or above, which covers error passive


def watch(seconds):
    """Follow board A's status for a while.

    Args:
        seconds: How long to watch.

    Returns:
        The distinct statuses in order, consecutive repeats dropped.
    """
    seen, end = [], time.time() + seconds  # statuses so far, stop time
    while time.time() < end:  # until then
        bench.Capture().drain(bench.CHANNEL_A)  # status messages update the driver's state
        status = bench.channel_status(bench.CHANNEL_A)  # current status
        if not seen or seen[-1] != status:  # a change
            seen.append(status)  # keep it
        time.sleep(0.02)  # poll period
    return seen  # the sequence


def report(label, statuses):
    """Print a session's status sequence with the controller's error counters when a probe answers.

    Args:
        label: Session description.
        statuses: Result of watch().
    """
    try:  # the probe is optional
        tec, rec, state = probe.error_state()  # straight from the controller
        counters = "TEC %d REC %d %s" % (tec, rec, state)  # error counters
    except (OSError, RuntimeError):  # no programmer or no answer
        counters = "no probe"  # counters unknown
    print("%-40s %s | %s" % (label, " -> ".join(bench.status_text(s) for s in statuses), counters))  # one line per session


def main():
    """Run the sessions.

    Returns:
        0 when the passive state shows in session 2 and clears in session 3, 1 otherwise.
    """
    btr = bench.BIT_RATES[KBPS]  # bit timing of both boards
    failures = []  # what went wrong

    #- Session 1: A turns error passive
    bench.open_channel(bench.CHANNEL_B, btr, listen_only=True)  # nothing acknowledges
    bench.open_channel(bench.CHANNEL_A, btr)  # board under test
    bench.send_frames(bench.CHANNEL_A, 0x123, 1)  # one frame, retried until the counters rise
    session1 = watch(0.5)  # status as it degrades
    report("session 1, one frame, nothing acknowledges", session1)  # show it
    bench.close_channel(bench.CHANNEL_A)  # A leaves the bus; B stays listen-only
    if not session1[-1] & HEAVY:  # never got worse
        failures.append("session 1 did not reach the error state")  # the setup failed

    #- Session 2: the state shows at bus-on
    bench.open_channel(bench.CHANNEL_A, btr)  # A rejoins, counters kept
    session2 = watch(0.5)  # status from the first poll
    report("session 2, no traffic", session2)  # show it
    bench.close_channels()  # both off
    if not session2[0] & HEAVY:  # reported healthy at bus-on
        failures.append("session 2 did not report the state carried over")  # the bus-on report is missing

    #- Session 3: acknowledged frames clear it
    bench.open_channel(bench.CHANNEL_B, btr)  # B acknowledges now
    bench.open_channel(bench.CHANNEL_A, btr)  # A rejoins
    report("session 3, no traffic, B acknowledges", watch(0.3))  # still carried over
    bench.send_frames(bench.CHANNEL_A, 0x124, 40)  # good frames lower the counters
    session3 = watch(0.5)  # status as it recovers
    report("session 3, after 40 acknowledged frames", session3)  # show it
    bench.close_channels()  # both off
    if session3[-1] != bench.OK:  # still degraded
        failures.append("session 3 did not return to error active")  # recovery not reported

    #- Verdict
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
