"""Where bus time goes in Tx Only, board A sending back to back: idle gaps between frames, or every frame running long.

Usage:
    python tests/frame_timing.py

The receiver's timestamp deltas are compared with each frame's exact length on the wire.
Idle gaps show up as single large deltas; a stretch that every frame shares shows up as a steady excess per frame.
The 125 kbit/s run calibrates the clock offset between the boards and the PC, and the last runs repeat 1 Mbit/s with 250 ns time quanta.
The report is informational and always exits 0; the README's performance notes come from it.
"""

import sys
import time

import bench

#& Settings

TICK_US = 2048 / 48.0  # the firmware's timestamp period, us
GAP_US = 2.5 * TICK_US  # a delta this much over the frame length is an idle gap, beyond timestamp jitter

#? 00h/14h splits a 1 Mbit/s bit into 8 quanta of 125 ns; 01h/01h into 4 of 250 ns, at the same 75% sample point.
RUNS = (  # (kbit/s, BTR0/BTR1 of board A, frame kind, frames, echo)
    (125, 0x031C, bench.KINDS[0], 4000, True),
    (1000, 0x0014, bench.KINDS[0], 40000, True),
    (1000, 0x0014, bench.KINDS[0], 40000, False),
    (1000, 0x0014, bench.KINDS[3], 80000, True),
    (1000, 0x0014, bench.KINDS[3], 80000, False),
    (1000, 0x0101, bench.KINDS[3], 80000, False),
)


def timing_run(kbps, btr, kind, count, echo):
    """Send a burst from board A and analyse board B's timestamps.

    Args:
        kbps: Nominal bit rate.
        btr: BTR0/BTR1 of board A; board B uses the standard setting for kbps.
        kind: bench.FrameKind to send.
        count: Frames to send.
        echo: Deliver echoes to board A, which adds work for its firmware.

    Returns:
        Report line, or None when nothing arrived.
    """
    #- Send the burst
    bench.open_channel(bench.CHANNEL_A, btr, echo=echo)  # sender
    bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[kbps])  # receiver and timekeeper
    time.sleep(0.2)  # let the channels settle
    captures, _, _ = bench.send_burst(kind, count, [bench.CHANNEL_A])  # back to back
    bench.close_channels()  # bus off
    frames, bad = bench.decode(kind, 0, captures[bench.CHANNEL_B].frames)  # what B received
    if len(frames) < 2:  # nothing to measure
        return None  # no report

    #- Compare each timestamp delta with the frame's wire length
    bit_us = 1000.0 / kbps  # one bit time
    span = frames[-1][0] - frames[0][0]  # first to last frame, us
    wire = sum(bench.wire_bits(kind, side, sequence) for _, side, sequence in frames[1:]) * bit_us  # their wire time, us
    excess = [(b[0] - a[0]) - bench.wire_bits(kind, b[1], b[2]) * bit_us for a, b in zip(frames, frames[1:])]  # time beyond each frame
    gaps = [value for value in excess if value > GAP_US]  # idle gaps
    per_frame_ns = (span - wire) / (len(frames) - 1) * 1000  # average excess per frame
    return "%s %5dk %02Xh/%02Xh %-9s echo %-3s frames %6d bad %d | load %.3f %% | %+.0f ns per frame (%+.3f bit) | gaps over %.0f us: %d, %.2f ms in them" % (
        bench.TX_ONLY, kbps, btr >> 8, btr & 0xFF, kind.name, "on" if echo else "off", len(frames), bad, 100.0 * wire / span, per_frame_ns, per_frame_ns / 1000 / bit_us,
        GAP_US, len(gaps), sum(gaps) / 1000)  # one line per run


def main():
    """Run every timing case and print a line for each.

    Returns:
        0, the report is informational.
    """
    for kbps, btr, kind, count, echo in RUNS:  # each case
        line = timing_run(kbps, btr, kind, count, echo)  # measure
        print(line or "%dk %s: nothing received" % (kbps, kind.name))  # result
    return 0  # informational


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
