"""Bus-off behaviour: shorting the bus sends board A bus-off, and only a restart from the host brings it back.

Usage:
    python tests/bus_off.py              A must stay bus-off until the script reopens the channel
    python tests/bus_off.py autoreset    the driver's automatic bus-off reset must bring A back

Interactive: when the script asks, short CANH to CANL for about 3 seconds, then remove the short.
Without an argument, A must report bus-off during the short and keep reporting it afterwards, since the firmware never rejoins the bus on its own.
With autoreset, the driver resets A whenever it reads the bus-off state, so A's frames must reach board B again once the short is gone, without a reopen.
In both modes, A's frames must reach B after the script reopens the channel.
"""

import sys
import time

import bench

#& Settings

KBPS = 500  # bit rate of the run
BUSOFF = 0x00010  # status flag: bus-off
SHORT_WAIT = 60.0  # seconds to wait for the short
HOLD = 10.0  # seconds to keep watching after bus-off, long after the short is gone
FIRST_ID = 0x123  # identifier of the first frame sent through the short; the next ones count up through 16 identifiers
CHECK_FRAMES = 3  # frames after the reopen


def main():
    """Watch board A through the short, then reopen it.

    Returns:
        0 when bus-off appears and A comes back only through a restart, or through the driver's reset with autoreset; 1 otherwise.
    """
    autoreset = sys.argv[1:] == ["autoreset"]  # the driver restarts a bus-off channel by itself
    btr = bench.BIT_RATES[KBPS]  # bit timing of both boards
    failures = []  # what went wrong

    #- Both boards on the bus, the driver's automatic restart as the mode asks
    bench.open_channel(bench.CHANNEL_B, btr)  # partner acknowledges
    bench.open_channel(bench.CHANNEL_A, btr)  # board under test
    bench.set_value(bench.CHANNEL_A, bench.PARAM_BUSOFF_AUTORESET, 1 if autoreset else 0)  # without autoreset the driver must not hide the bus-off
    print("short CANH to CANL now for about 3 s, then remove the short (waiting up to %.0f s)" % SHORT_WAIT)  # instruction
    sys.stdout.flush()  # show it at once

    #- Keep sending through the short, watching every status change and whether A's frames reach B again
    start = time.time()  # reference for the printed times
    seen_at, last_off, back_at, last, end, index = None, None, None, None, start + SHORT_WAIT, 0  # bus-off times, comeback, last status, deadline, frame number
    while time.time() < end:  # until the deadline
        message = bench.Message()  # zeroed frame
        message.ID, message.LEN = FIRST_ID + (index & 0xF), 8  # 11-bit data frame, the identifier counting up
        written = bench.write(bench.CHANNEL_A, message)  # one frame, so errors accumulate during the short
        index += 1  # next frame
        capture = bench.Capture()  # what A delivered in this pass
        capture.drain(bench.CHANNEL_A)  # status messages update the driver's state
        status = bench.channel_status(bench.CHANNEL_A)  # current status
        if status != last:  # a change
            print("%.1f s  %s" % (time.time() - start, bench.status_text(status)))  # when and what
            sys.stdout.flush()  # live output
            last = status  # remember it
        #? With autoreset the driver resets A in whichever call reads the bus-off first, so the write and the reads count as well as the status.
        if (written | status) & BUSOFF or any("BUSOFF" in note for note in capture.notes):  # A is bus-off now
            last_off = time.time()  # latest bus-off report
            if seen_at is None:  # the first one
                seen_at = last_off  # mark it
                end = seen_at + HOLD  # watch a while longer, past the end of the short
                bench.Capture().drain(bench.CHANNEL_B)  # drop A's frames from before the short
        elif seen_at is not None and back_at is None:  # A no longer reports bus-off
            capture = bench.Capture()  # what B received since the last pass
            capture.drain(bench.CHANNEL_B)  # frames from A, if they get through again
            if any(FIRST_ID <= frame[1] < FIRST_ID + 0x10 for frame in capture.frames):  # A sends again
                back_at = time.time()  # A is back on the bus
                print("%.1f s  A's frames reach B again, %.2f s after the last bus-off report" % (back_at - start, back_at - last_off))  # comeback
                sys.stdout.flush()  # live output
        time.sleep(0.05)  # frame period
    if seen_at is None:  # never went bus-off
        failures.append("no bus-off seen; was the bus shorted?")  # setup missing
    elif autoreset and back_at is None:  # the driver's reset never brought A back
        failures.append("the driver's automatic reset did not bring board A back")  # no comeback
    elif not autoreset and (back_at is not None or not last & BUSOFF):  # recovered on its own
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
