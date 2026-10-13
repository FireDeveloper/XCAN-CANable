"""Bus errors as error frames at 500 kbit/s and 1 Mbit/s, Tx Only and Rx Only: A sends where nobody acknowledges, then hears a board on half its bit rate.

Usage:
    python tests/bus_errors.py

Tx Only: board B listens only, so A's frame gets no acknowledge, and each retry must come back as an ACK error, direction transmit, segment ACK slot.
Rx Only: B sends at half A's bit rate, and the errors A sees must come back as stuff or form errors, direction receive.
An error frame's identifier is the error type and its data holds the direction, segment, receive and transmit error counters.
With the probe on board A and its ELF (see probe.py), every bus error A's controller detected must reach the host as its own error frame.
Bus state changes add a few error frames of their own.
Fails when no error frames arrive, they decode as anything else, or, with the probe, a bus error goes missing.
"""

import sys
import time

import bench
import probe

#& Settings

ERR_FORM = 0x2  # error frame identifier: form error
ERR_STUFF = 0x4  # error frame identifier: stuff error
ERR_OTHER = 0x8  # error frame identifier: other error, which covers a missing acknowledge
DIR_TRANSMIT = 0  # first data byte: the board was sending
DIR_RECEIVE = 1  # first data byte: the board was receiving
SEGMENT_ACK = 0x19  # second data byte: ACK slot
SECONDS = 0.5  # how long each mode collects while the errors run
SETTLE = 0.3  # seconds for the last error frames to arrive once the errors stop
RATES = (500, 1000)  # board A's bit rates, kbit/s
STATE_REPORTS = 4  # most error frames a run adds for bus state changes: warning and error passive, each way


def collect(seconds):
    """Read board A's error frames for a while.

    Args:
        seconds: How long to read.

    Returns:
        List of (identifier, data bytes) of the error frames.
    """
    errors = []  # error frames so far
    end = time.time() + seconds  # stop time
    while time.time() < end:  # read until then
        status, message, _ = bench.read(bench.CHANNEL_A)  # next message
        if status & bench.QRCVEMPTY:  # nothing yet
            time.sleep(0.002)  # wait for more
            continue  # read again
        if status == bench.OK and message.MSGTYPE & bench.MSG_ERRFRAME:  # an error frame
            errors.append((message.ID, bytes(message.DATA[:message.LEN])))  # type and details
    return errors  # in arrival order


def describe(errors):
    """Format an error frame count and the first few frames.

    Args:
        errors: Result of collect().

    Returns:
        Text with the count, the rate and up to three frames.
    """
    shown = "; ".join("type 0x%X data %s" % (can_id, data.hex(" ")) for can_id, data in errors[:3])  # first frames
    return "%d error frames, %.0f per second (%s)" % (len(errors), len(errors) / SECONDS, shown or "-")  # summary


def counters_or_none(symbols):
    """Read board A's firmware counters when the probe answers.

    Args:
        symbols: Result of probe.elf_symbols(), or None.

    Returns:
        The counters, or None without the probe.
    """
    if symbols is None:  # no ELF, or another board on the probe
        return None  # nothing to read
    try:  # the probe is optional
        return probe.firmware_counters(symbols)  # current counters
    except (OSError, RuntimeError, KeyError):  # no programmer, no answer or another image
        return None  # skip the count check


def run(kbps, mode, symbols):
    """Raise bus errors in one traffic mode at one bit rate and check the error frames.

    Args:
        kbps: Board A's bit rate.
        mode: bench.TX_ONLY or bench.RX_ONLY.
        symbols: Result of probe.elf_symbols() for the image on board A, or None.

    Returns:
        List of failures, empty when this mode passed.
    """
    failures = []  # what went wrong
    name = "%dk %s" % (kbps, mode)  # for the report

    #- Board B where it causes the errors, board A reporting them
    if mode == bench.TX_ONLY:
        bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[kbps], listen_only=True)  # B hears without acknowledging
    else:
        bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[kbps // 2])  # B sends at half the rate
    bench.open_channel(bench.CHANNEL_A, bench.BIT_RATES[kbps], error_frames=True)  # A reports bus errors
    collect(0.2)  # discard what the open produced
    before = counters_or_none(symbols)  # A's counters before the errors

    #- Errors for a while, then stop them with A still open
    if mode == bench.TX_ONLY:
        bench.send_frames(bench.CHANNEL_A, 0x123, 1)  # one frame, retried after every error
    else:
        bench.send_frames(bench.CHANNEL_B, 0x155, 3)  # frames A cannot decode
    errors = collect(SECONDS)  # the errors while they run
    if mode == bench.TX_ONLY:
        bench.set_value(bench.CHANNEL_B, bench.PARAM_LISTEN_ONLY, 0)  # B acknowledges, so the retries end
    else:
        bench.close_channel(bench.CHANNEL_B)  # B leaves the bus
    rest = collect(SETTLE)  # error frames still on their way
    after = counters_or_none(symbols)  # A's counters once the errors stopped
    bench.close_channels()  # both off

    #- Each error frame decodes as the mode expects
    if mode == bench.TX_ONLY:
        matching = [e for e in errors if e[0] == ERR_OTHER and len(e[1]) >= 2 and e[1][0] == DIR_TRANSMIT and e[1][1] == SEGMENT_ACK]  # ACK errors in Tx Only
        print("%s: %s, %d of them ACK errors" % (name, describe(errors), len(matching)))  # the outcome
    else:
        matching = [e for e in errors if e[0] in (ERR_FORM, ERR_STUFF) and e[1][:1] == bytes([DIR_RECEIVE])]  # stuff and form errors while receiving
        print("%s: %s, %d of them stuff or form errors while receiving" % (name, describe(errors), len(matching)))  # the outcome
    if not matching or len(matching) < len(errors) / 2:  # missing or mostly something else
        failures.append("%s: error frames missing or decoded wrong" % name)  # name the mode

    #- Every bus error the controller saw reached the host
    if before and after:  # the probe answered both times
        seen = after["bus_errs"] - before["bus_errs"]  # bus errors A's controller detected
        lost = after["bus_err_ovfs"] - before["bus_err_ovfs"]  # bus errors that found the firmware's queue full
        reported = len(errors) + len(rest)  # error frames the host received for them
        print("  controller saw %d bus errors, %d lost to a full queue; the host got %d error frames" % (seen, lost, reported))  # the count
        if lost or not 0 <= reported - seen <= STATE_REPORTS:  # a bus error without its own error frame
            failures.append("%s: %d error frames for %d bus errors" % (name, reported, seen))  # name the shortfall
    return failures  # this mode's verdict


def main():
    """Check Tx Only and Rx Only at every bit rate.

    Returns:
        0 when every mode delivers correctly decoded error frames, one per bus error with the probe; 1 otherwise.
    """
    #- Find board A's firmware counters, when the probe sits on it
    try:  # the ELF is optional
        symbols = probe.elf_symbols()  # addresses of the firmware counters
    except (OSError, ValueError) as error:  # no build here
        symbols = None  # check the error frames only
        print("no firmware counters: %s" % error)  # say why
    if symbols:  # see which board the probe reads
        for channel in bench.CHANNELS:  # both on one bit rate, so the probe frames get acknowledged
            bench.open_channel(channel, bench.BIT_RATES[500])  # partner and board under test
        if probe.probed_channel(symbols) != bench.CHANNEL_A:  # the counters would be another board's
            symbols = None  # check the error frames only
            print("no firmware counters: board A is not on the probe")  # say why
        bench.close_channels()  # both off

    #- Both modes at every bit rate
    failures = []  # what went wrong
    for kbps in RATES:  # each bit rate of board A
        for mode in (bench.TX_ONLY, bench.RX_ONLY):  # A sending, then A receiving
            failures += run(kbps, mode, symbols)  # this mode's failures

    #- Verdict
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
