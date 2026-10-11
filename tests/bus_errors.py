"""Bus errors as error frames, from both sides: board A sends where nobody acknowledges, then hears a board on the wrong bit rate.

Usage:
    python tests/bus_errors.py

Transmit side: board B listens only, so A's frame gets no acknowledge, and each retry must come back as an ACK error, direction transmit, segment ACK slot.
Receive side: B sends at 250 kbit/s into A's 500 kbit/s, and the errors A sees must come back as stuff or form errors, direction receive.
An error frame's identifier is the error type and its data holds the direction, segment, receive and transmit error counters.
Fails when no error frames arrive or they decode as anything else.
"""

import sys
import time

import bench

#& Settings

ERR_FORM = 0x2  # error frame identifier: form error
ERR_STUFF = 0x4  # error frame identifier: stuff error
ERR_OTHER = 0x8  # error frame identifier: other error, which covers a missing acknowledge
DIR_TRANSMIT = 0  # first data byte: the board was sending
DIR_RECEIVE = 1  # first data byte: the board was receiving
SEGMENT_ACK = 0x19  # second data byte: ACK slot
SECONDS = 0.5  # how long each side collects


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


def main():
    """Check both sides.

    Returns:
        0 when both sides deliver correctly decoded error frames, 1 otherwise.
    """
    failures = []  # what went wrong

    #- Transmit side: nobody acknowledges A's frame
    bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[500], listen_only=True)  # B hears without acknowledging
    bench.open_channel(bench.CHANNEL_A, bench.BIT_RATES[500], error_frames=True)  # A reports bus errors
    collect(0.2)  # discard what the open produced
    bench.send_frames(bench.CHANNEL_A, 0x123, 1)  # one frame, retried after every error
    errors = collect(SECONDS)  # the retries' errors
    acks = [e for e in errors if e[0] == ERR_OTHER and len(e[1]) >= 2 and e[1][0] == DIR_TRANSMIT and e[1][1] == SEGMENT_ACK]  # ACK errors while sending
    print("transmit side: %s, %d of them ACK errors" % (describe(errors), len(acks)))  # the outcome
    if not acks or len(acks) < len(errors) / 2:  # missing or mostly something else
        failures.append("transmit side: ACK errors missing or decoded wrong")  # name the side
    bench.close_channels()  # both off

    #- Receive side: B on the wrong bit rate
    bench.open_channel(bench.CHANNEL_B, bench.BIT_RATES[250])  # B sends at half the rate
    bench.open_channel(bench.CHANNEL_A, bench.BIT_RATES[500], error_frames=True)  # A reports bus errors
    collect(0.2)  # discard what the open produced
    bench.send_frames(bench.CHANNEL_B, 0x155, 3)  # frames A cannot decode
    errors = collect(SECONDS)  # A's errors
    receiving = [e for e in errors if e[0] in (ERR_FORM, ERR_STUFF) and e[1][:1] == bytes([DIR_RECEIVE])]  # stuff and form errors while receiving
    print("receive side: %s, %d of them stuff or form errors while receiving" % (describe(errors), len(receiving)))  # the outcome
    if not receiving or len(receiving) < len(errors) / 2:  # missing or mostly something else
        failures.append("receive side: stuff or form errors missing or decoded wrong")  # name the side
    bench.close_channels()  # both off

    #- Verdict
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
