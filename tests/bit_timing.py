"""Every 1 Mbit/s bit timing the host offers: board A opens with each one and exchanges frames with board B at the standard setting.

Usage:
    python tests/bit_timing.py

With the probe, A's CAN_BTR register must also hold the value the firmware's mapping gives, half-length quanta for a 1-quantum segment 1 included.
Fails when an open fails, a frame goes missing either way, or CAN_BTR differs.
"""

import sys

import bench
import probe

#& Settings

#? The host's 1 Mbit/s list on an 8 MHz time base: BTR0/BTR1, sample point, and jump width in quanta.
ENTRIES = (
    (0x0005, "88%", 1), (0x0014, "75%", 1), (0x0023, "62%", 1), (0x0032, "50%", 1),
    (0x0101, "75%, 4 quanta", 1), (0x0110, "50%, 4 quanta", 1), (0x4014, "75%", 2), (0x4023, "62%", 2), (0x4032, "50%", 2),
    (0x4110, "50%, 4 quanta", 2), (0x8023, "62%", 3), (0x8032, "50%", 3), (0xC032, "50%", 4),
)
FRAMES = 200  # frames each way per setting
BTR_FIELDS = 0x037F03FF  # SJW, TS2, TS1 and BRP fields of CAN_BTR, mode bits left out


def expected_btr(btr):
    """Compute the CAN_BTR value the firmware programs for a host bit timing.

    Args:
        btr: BTR0/BTR1 pair.

    Returns:
        (CAN_BTR fields, sample point as a fraction).
    """
    btr0, btr1 = btr >> 8, btr & 0xFF  # the two registers
    brp, sjw = (btr0 & 0x3F) + 1, (btr0 >> 6) + 1  # prescaler and jump width
    tseg1, tseg2 = (btr1 & 0x0F) + 1, ((btr1 >> 4) & 0x7) + 1  # time segments in host quanta
    split = 2 if tseg1 == 1 and tseg2 <= 4 else 1  # see pcan_can_set_bitrate(): a 1-quantum segment 1 runs on half-length quanta
    bs1, bs2, jump = (tseg1 + 1) * split - 1, tseg2 * split, min(sjw * split, 4)  # bxCAN segments and jump width
    fields = ((jump - 1) << 24) | ((bs2 - 1) << 20) | ((bs1 - 1) << 16) | (brp * 6 // split - 1)  # CAN_BTR layout, 48 MHz clock
    return fields, (1 + bs1) / (1 + bs1 + bs2)  # register value and sample point


def main():
    """Try every entry.

    Returns:
        0 when every entry opens, exchanges all frames and programs the expected CAN_BTR, 1 otherwise.
    """
    failures = []  # what went wrong
    bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[1000])  # partner at the standard 00h/14h
    for btr, label, sjw in ENTRIES:  # each setting
        #- Open A with this setting and read back CAN_BTR
        status = bench.open_channel(bench.CHANNEL_A, btr)  # board under test
        want, sample_point = expected_btr(btr)  # what the firmware should program
        try:  # the probe is optional
            got = probe.read_words(probe.CAN_BTR)[0] & BTR_FIELDS  # what it programmed
        except (OSError, RuntimeError):  # no programmer or no answer
            got = None  # skip the register check

        #- Exchange frames both ways
        bench.read_frames(bench.CHANNEL_A, 0.05)  # discard A's leftovers
        bench.read_frames(bench.CHANNEL_B, 0.05)  # discard B's leftovers
        bench.send_frames(bench.CHANNEL_A, 0x111, FRAMES)  # A to B
        at_b = len([1 for can_id, _ in bench.read_frames(bench.CHANNEL_B, 0.4) if can_id == 0x111])  # arrived at B
        bench.send_frames(bench.CHANNEL_B, 0x222, FRAMES)  # B to A
        at_a = len([1 for can_id, _ in bench.read_frames(bench.CHANNEL_A, 0.4) if can_id == 0x222])  # arrived at A

        #- Judge this setting
        register = "no probe" if got is None else "CAN_BTR %08X %s" % (got, "ok" if got == want else "expected %08X" % want)  # register check
        print("%02Xh/%02Xh (%-14s SJW %d) open %s | %s | sample point %.1f%% | A->B %d/%d, B->A %d/%d, A %s" % (
            btr >> 8, btr & 0xFF, label + ",", sjw, bench.status_text(status), register, 100 * sample_point, at_b, FRAMES, at_a, FRAMES,
            bench.status_text(bench.channel_status(bench.CHANNEL_A))))  # one line per setting
        if status != bench.OK or at_b != FRAMES or at_a != FRAMES or (got is not None and got != want):  # anything off
            failures.append("%02Xh/%02Xh" % (btr >> 8, btr & 0xFF))  # name the setting
        bench.close_channel(bench.CHANNEL_A)  # next setting
    bench.close_channels()  # both off

    #- Verdict
    print("PASS" if not failures else "FAIL: " + ", ".join(failures))  # failing settings by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
