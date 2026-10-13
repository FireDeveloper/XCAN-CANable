"""Every bit rate the host tools offer: both boards on the rate's timing code, frames both ways.

Usage:
    python tests/bit_rates.py

The codes are the Windows CAN Driver's standard ones; it has none for 200 kbit/s, so both common choices run.
12.5 and 11.1 kbit/s bracket the lowest rate the board's CAN transceiver allows: it stops driving a bus held dominant too long.
With the probe, board A's CAN_BTR must hold each timing exactly as the host gave it.
The rates below the transceiver's limit run last, so their errors disturb no other, and only their register is checked.
Fails when an open fails, a frame of a working rate goes missing either way, or CAN_BTR differs.
"""

import sys

import bench
import probe

#& Settings

RATES = (  # (name, BTR0/BTR1, whether the board passes frames at it)
    ("1 Mbit/s", 0x0014, True), ("800 kbit/s", 0x0016, True), ("500 kbit/s", 0x001C, True), ("250 kbit/s", 0x011C, True),
    ("200 kbit/s", 0x012F, True), ("200 kbit/s", 0x412F, True), ("125 kbit/s", 0x031C, True), ("100 kbit/s", 0x432F, True),
    ("95.238 kbit/s", 0xC34E, True), ("83.333 kbit/s", 0x852B, True), ("50 kbit/s", 0x472F, True), ("47.619 kbit/s", 0x1414, True),
    ("33.333 kbit/s", 0x8B2F, True), ("20 kbit/s", 0x532F, True), ("12.5 kbit/s", 0x5F2F, True),
    ("11.1 kbit/s", 0x632F, False), ("10 kbit/s", 0x672F, False), ("5 kbit/s", 0x7F7F, False),
)
FAST_FRAMES = 100  # frames each way at 80 kbit/s and above
SLOW_FRAMES = 20  # frames each way below, where each frame takes milliseconds
FRAME_BITS = 140  # an 8-byte frame with its stuff bits and interframe space, rounded up


def exchange(btr):
    """Send frames from A to B, then from B to A, on the current bit rate.

    Args:
        btr: BTR0/BTR1 both boards run, for the time the frames need.

    Returns:
        (frames sent each way, frames that reached B, frames that reached A).
    """
    bit_us = probe.bit_time_us(btr)  # one bit
    frames = FAST_FRAMES if bit_us <= 12.5 else SLOW_FRAMES  # keep the slow rates short
    window = max(0.5, frames * FRAME_BITS * bit_us / 1e6 * 1.5)  # time for every frame to cross, with margin
    bench.read_frames(bench.CHANNEL_A, 0.05)  # discard A's leftovers
    bench.read_frames(bench.CHANNEL_B, 0.05)  # discard B's leftovers
    bench.send_frames(bench.CHANNEL_A, 0x111, frames)  # A to B
    at_b = len([1 for can_id, _ in bench.read_frames(bench.CHANNEL_B, window) if can_id == 0x111])  # arrived at B
    bench.send_frames(bench.CHANNEL_B, 0x222, frames)  # B to A
    at_a = len([1 for can_id, _ in bench.read_frames(bench.CHANNEL_A, window) if can_id == 0x222])  # arrived at A
    return frames, at_b, at_a  # the counts


def main():
    """Run every rate.

    Returns:
        0 when every rate opens and programs the host's CAN_BTR, and every working rate passes all frames; 1 otherwise.
    """
    failures = []  # what went wrong
    for name, btr, works in sorted(RATES, key=lambda rate: not rate[2]):  # working rates first
        #- Both boards on this rate, A's CAN_BTR read back
        status_b = bench.open_channel(bench.CHANNEL_B, btr)  # partner
        status_a = bench.open_channel(bench.CHANNEL_A, btr)  # board under test
        want, sample_point = probe.can_btr_for(btr)  # what the firmware should program
        try:  # the probe is optional
            got = probe.read_words(probe.CAN_BTR)[0] & probe.BTR_FIELDS  # what it programmed
        except (OSError, RuntimeError):  # no programmer or no answer
            got = None  # skip the register check

        #- Frames both ways, then judge the rate
        frames, at_b, at_a = exchange(btr)  # the traffic
        register = "no probe" if got is None else "CAN_BTR %08X %s" % (got, "as given" if got == want else "expected %08X" % want)  # register check
        status = bench.status_text(bench.channel_status(bench.CHANNEL_A))  # A after the traffic
        note = "" if works else " | below the transceiver's limit, no frames expected"  # rates the board cannot run
        print("%-14s %02Xh/%02Xh sample point %4.1f%% | open A %s, B %s | %s | A->B %3d/%d, B->A %3d/%d, A %s%s" % (
            name, btr >> 8, btr & 0xFF, 100 * sample_point, bench.status_text(status_a), bench.status_text(status_b), register,
            at_b, frames, at_a, frames, status, note))  # one line per rate
        if status_a != bench.OK or status_b != bench.OK or (got is not None and got != want) or (works and (at_b != frames or at_a != frames)):  # anything off
            failures.append("%s %02Xh/%02Xh" % (name, btr >> 8, btr & 0xFF))  # name the rate
        bench.close_channels()  # next rate

    #- Verdict
    print("PASS" if not failures else "FAIL: " + ", ".join(failures))  # failing rates by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
