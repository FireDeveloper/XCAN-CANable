"""Every 1 Mbit/s bit timing the host offers: board A opens with each one and exchanges frames with board B at the standard setting.

Usage:
    python tests/bit_timing.py

With the probe, A's CAN_BTR register must also hold the timing exactly as the host gave it.
bxCAN passes no frames with a 1-quantum segment 1; those settings run last, so their errors disturb no other, and only their register is checked.
Fails when an open fails, a frame of a working setting goes missing either way, or CAN_BTR differs.
"""

import sys

import bench
import probe

#& Settings

#? The host's 1 Mbit/s list on an 8 MHz time base: BTR0/BTR1, sample point, jump width in quanta, and whether bxCAN passes frames with it.
#? 94h is 14h with the SJA1000's triple-sampling bit; bxCAN has no such mode and samples once.
ENTRIES = (
    (0x0005, "88%", 1, True), (0x0014, "75%", 1, True), (0x0023, "62%", 1, True), (0x0032, "50%", 1, True),
    (0x0101, "75%, 4 quanta", 1, True), (0x0110, "50%, 4 quanta", 1, False), (0x4014, "75%", 2, True), (0x4023, "62%", 2, True), (0x4032, "50%", 2, True),
    (0x4110, "50%, 4 quanta", 2, False), (0x8023, "62%", 3, True), (0x8032, "50%", 3, True), (0xC032, "50%", 4, True),
    (0x0094, "75%, sampled 3x", 1, True),
)
FRAMES = 200  # frames each way per setting


def main():
    """Try every entry.

    Returns:
        0 when every entry opens and programs the host's CAN_BTR, and every working entry exchanges all frames; 1 otherwise.
    """
    failures = []  # what went wrong
    bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[1000])  # partner at the standard 00h/14h
    for btr, label, sjw, works in sorted(ENTRIES, key=lambda entry: not entry[3]):  # working settings first
        #- Open A with this setting and read back CAN_BTR
        status = bench.open_channel(bench.CHANNEL_A, btr)  # board under test
        want, sample_point = probe.can_btr_for(btr)  # what the firmware should program
        try:  # the probe is optional
            got = probe.read_words(probe.CAN_BTR)[0] & probe.BTR_FIELDS  # what it programmed
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
        print("%02Xh/%02Xh (%-14s SJW %d) open %s | %s | sample point %.1f%% | A->B %d/%d, B->A %d/%d, A %s%s" % (
            btr >> 8, btr & 0xFF, label + ",", sjw, bench.status_text(status), register, 100 * sample_point, at_b, FRAMES, at_a, FRAMES,
            bench.status_text(bench.channel_status(bench.CHANNEL_A)), "" if works else " | no frames expected on bxCAN"))  # one line per setting
        if status != bench.OK or (got is not None and got != want) or (works and (at_b != FRAMES or at_a != FRAMES)):  # anything off
            failures.append("%02Xh/%02Xh" % (btr >> 8, btr & 0xFF))  # name the setting
        bench.close_channel(bench.CHANNEL_A)  # next setting
    bench.close_channels()  # both off

    #- Verdict
    print("PASS" if not failures else "FAIL: " + ", ".join(failures))  # failing settings by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
