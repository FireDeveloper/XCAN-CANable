"""Transmit path checks: frames stuck at bus off never reach a later session, and queued frames survive a listen-only switch.

Usage:
    python tests/tx_paths.py

Board A is the board under test and board B its partner.
Part 1: B listens only, so A's frames stay unacknowledged and stuck in A; after A leaves and rejoins the bus, B must see none of them, then A's new frames.
Part 2: A switches listen-only on and off while frames wait in its queue; B must still receive every one of them, in order.
"""

import sys
import time

import bench

#& Settings

KBPS = 500  # bit rate of both parts
STUCK_ID = 0x123  # identifier of the frames nobody acknowledges
NEW_ID = 0x456  # identifier of the frames after the reconnect
QUEUED_ID = 0x321  # identifier of the frames queued across the mode switch


def main():
    """Run both parts.

    Returns:
        0 when nothing replays and nothing is lost, 1 otherwise.
    """
    btr = bench.BIT_RATES[KBPS]  # bit timing of both boards
    failures = []  # what went wrong

    #^ Part 1: no replay after bus off and on
    #- Frames stuck in A while nothing acknowledges
    bench.open_channel(bench.CHANNEL_B, btr, listen_only=True)  # B hears without acknowledging
    bench.open_channel(bench.CHANNEL_A, btr)  # board under test
    queued = bench.send_frames(bench.CHANNEL_A, STUCK_ID, 30)  # frames that cannot complete
    time.sleep(0.3)  # A keeps retrying them
    print("A queued %d frames nobody acknowledges, status %s" % (queued, bench.status_text(bench.channel_status(bench.CHANNEL_A))))  # stuck frames
    bench.close_channels()  # bus off drops A's queue

    #- A new session sends only new frames
    bench.open_channel(bench.CHANNEL_B, btr)  # B acknowledges again
    bench.open_channel(bench.CHANNEL_A, btr)  # A rejoins the bus
    replayed = [frame for frame in bench.read_frames(bench.CHANNEL_B, 0.5) if frame[0] == STUCK_ID]  # old frames reaching B
    bench.send_frames(bench.CHANNEL_A, NEW_ID, 5)  # frames of the new session
    new = [index for can_id, index in bench.read_frames(bench.CHANNEL_B, 0.5) if can_id == NEW_ID]  # what B got
    print("after the reconnect B received %d stuck frames, then new frames %s" % (len(replayed), new))  # the outcome
    if replayed:  # frames from the closed session
        failures.append("stuck frames went out after the reconnect")  # replay
    if new != list(range(5)):  # missing or out of order
        failures.append("new frames missing or out of order")  # lost frames

    #^ Part 2: listen-only switch with frames queued
    #- Queue frames, switch listen-only on and off
    queued = bench.send_frames(bench.CHANNEL_A, QUEUED_ID, 60)  # frames waiting in A
    bench.set_value(bench.CHANNEL_A, bench.PARAM_LISTEN_ONLY, 1)  # mode change with frames queued
    time.sleep(0.05)  # stay in listen-only a moment
    bench.set_value(bench.CHANNEL_A, bench.PARAM_LISTEN_ONLY, 0)  # back to normal

    #- Every frame arrives, in order
    indices = [index for can_id, index in bench.read_frames(bench.CHANNEL_B, 1.0) if can_id == QUEUED_ID]  # what B got
    gaps = [b for a, b in zip(indices, indices[1:]) if b != a + 1]  # breaks in the order
    status_a = bench.status_text(bench.channel_status(bench.CHANNEL_A))  # board under test
    status_b = bench.status_text(bench.channel_status(bench.CHANNEL_B))  # partner
    print("A queued %d frames across the switch; B received %d, gaps %s, status A %s B %s" % (queued, len(indices), gaps, status_a, status_b))  # the outcome
    if indices != list(range(60)):  # missing or out of order
        failures.append("frames queued across the listen-only switch missing or out of order")  # lost frames
    bench.close_channels()  # bus off

    #- Verdict
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
