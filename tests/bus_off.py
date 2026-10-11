"""Bus-off behaviour: shorting the bus sends board A bus-off, and it stays off until the host restarts the channel.

Usage:
    python tests/bus_off.py

Interactive: when the script asks, short CANH to CANL for about 3 seconds, then remove the short.
A must report bus-off during the short and keep reporting it afterwards, since the firmware never rejoins the bus on its own.
After the script reopens the channel, A's frames must reach board B again.
"""

import sys
import time

import bench

#& Settings

KBPS = 500  # bit rate of the run
BUSOFF = 0x00010  # status flag: bus-off
SHORT_WAIT = 60.0  # seconds to wait for the short
HOLD = 10.0  # seconds to keep watching after bus-off, long after the short is gone
CHECK_FRAMES = 3  # frames after the reopen


def main():
    """Watch board A through the short, then reopen it.

    Returns:
        0 when bus-off appears, holds and clears only with the reopen, 1 otherwise.
    """
    btr = bench.BIT_RATES[KBPS]  # bit timing of both boards
    failures = []  # what went wrong

    #- Both boards on the bus, no automatic restart in the driver
    bench.open_channel(bench.CHANNEL_B, btr)  # partner acknowledges
    bench.open_channel(bench.CHANNEL_A, btr)  # board under test
    bench.set_value(bench.CHANNEL_A, bench.PARAM_BUSOFF_AUTORESET, 0)  # the driver must not hide the bus-off
    print("short CANH to CANL now for about 3 s, then remove the short (waiting up to %.0f s)" % SHORT_WAIT)  # instruction
    sys.stdout.flush()  # show it at once

    #- Keep sending through the short, watching every status change
    start = time.time()  # reference for the printed times
    seen_at, last, end, index = None, None, start + SHORT_WAIT, 0  # bus-off time, last status, deadline, frame number
    while time.time() < end:  # until the deadline
        bench.send_frames(bench.CHANNEL_A, 0x123 + (index & 0xF), 1)  # one frame, so errors accumulate during the short
        index += 1  # next frame
        bench.Capture().drain(bench.CHANNEL_A)  # status messages update the driver's state
        status = bench.channel_status(bench.CHANNEL_A)  # current status
        if status != last:  # a change
            print("%.1f s  %s" % (time.time() - start, bench.status_text(status)))  # when and what
            sys.stdout.flush()  # live output
            last = status  # remember it
        if seen_at is None and status & BUSOFF:  # bus-off appeared
            seen_at = time.time()  # mark it
            end = seen_at + HOLD  # watch a while longer, past the end of the short
        time.sleep(0.05)  # frame period
    if seen_at is None:  # never went bus-off
        failures.append("no bus-off seen; was the bus shorted?")  # setup missing
    elif not last & BUSOFF:  # recovered on its own
        failures.append("bus-off cleared without a restart")  # the firmware rejoined by itself

    #- A reopen brings A back
    bench.close_channel(bench.CHANNEL_A)  # host restarts the channel
    bench.open_channel(bench.CHANNEL_A, btr)  # back on the bus
    time.sleep(0.5)  # let it settle
    bench.read_frames(bench.CHANNEL_B, 0.1)  # discard what B collected
    bench.send_frames(bench.CHANNEL_A, 0x321, CHECK_FRAMES)  # frames after the restart
    arrived = len([1 for can_id, _ in bench.read_frames(bench.CHANNEL_B, 0.5) if can_id == 0x321])  # what reached B
    status = bench.channel_status(bench.CHANNEL_A)  # A after the restart
    print("after the reopen: %d of %d frames reached B, A %s" % (arrived, CHECK_FRAMES, bench.status_text(status)))  # the outcome
    if arrived != CHECK_FRAMES or status != bench.OK:  # still broken
        failures.append("board A did not work again after the reopen")  # restart failed
    bench.close_channels()  # bus off

    #- Verdict
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
