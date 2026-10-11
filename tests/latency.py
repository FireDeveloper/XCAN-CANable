"""Latency of lone frames: how long a single frame, and its echo, take from the sender's write to the host reading them.

Usage:
    python tests/latency.py

Frames go out one at a time with pauses, so each travels alone through the firmware's USB path, including the zero-length packet that ends a transfer.
Fails when a frame or an echo goes missing, arrives changed, or takes longer than LIMIT_MS.
"""

import statistics
import sys
import time

import bench

#& Settings

KIND = bench.KINDS[0]  # 11-bit identifier, 8 data bytes
KBPS = 500  # bit rate of the run
FRAMES = 30  # lone frames per direction
PAUSE = 0.05  # seconds between frames, long enough for the USB transfer to end
LIMIT_MS = 20.0  # slowest acceptable delivery


def lone_frames(sender, want_echo):
    """Send lone frames from one board and time their arrival.

    Args:
        sender: Sending channel.
        want_echo: Also time the sender's echoes.

    Returns:
        (received, bad, [receive latencies in ms], [echo latencies in ms]).
    """
    receiver = bench.CHANNEL_B if sender == bench.CHANNEL_A else bench.CHANNEL_A  # the other board
    received, bad, frame_ms, echo_ms = 0, 0, [], []  # results
    for sequence in range(FRAMES):  # one frame at a time
        #- Send one frame and wait for it and its echo
        captures = {channel: bench.Capture() for channel in bench.CHANNELS}  # fresh per frame
        start = time.perf_counter()  # write time
        bench.write(sender, bench.make_frame(KIND, bench.SIDES[sender], sequence))  # the lone frame
        frame_at = echo_at = None  # arrival times
        while time.perf_counter() - start < 2.0 and (frame_at is None or (want_echo and echo_at is None)):  # until both arrived or 2 s passed
            for channel in bench.CHANNELS:  # read both boards
                captures[channel].drain(channel)  # what arrived
            if frame_at is None and captures[receiver].frames:  # the frame is in
                frame_at = time.perf_counter() - start  # receive latency
            if echo_at is None and captures[sender].echoes:  # the echo is in
                echo_at = time.perf_counter() - start  # echo latency

        #- Check content and record the times
        _, wrong = bench.decode(KIND, bench.SIDES[sender], captures[receiver].frames)  # content check
        bad += wrong  # changed frames
        if frame_at is not None:  # arrived
            received += 1  # count it
            frame_ms.append(frame_at * 1000)  # in ms
        if echo_at is not None:  # echo arrived
            echo_ms.append(echo_at * 1000)  # in ms
        time.sleep(PAUSE)  # let the transfer end before the next frame
    return received, bad, frame_ms, echo_ms  # the run


def spread(values):
    """Format min, median and max of a list of milliseconds.

    Args:
        values: Latencies in ms.

    Returns:
        Text, or "none" for an empty list.
    """
    if not values:  # nothing measured
        return "none"  # say so
    return "min %.1f median %.1f max %.1f ms" % (min(values), statistics.median(values), max(values))  # spread


def main():
    """Time lone frames in both directions and echoes on board A.

    Returns:
        0 when every frame and echo arrived unchanged within LIMIT_MS, 1 otherwise.
    """
    #- Both channels on the bus, echo on
    for channel in bench.CHANNELS:  # both boards
        status = bench.open_channel(channel, bench.BIT_RATES[KBPS], echo=True)  # echoes for the timing
        if status != bench.OK:  # board missing or busy
            print("open %s: %s" % (bench.NAMES[channel], bench.status_text(status)))  # say which
            bench.close_channels()  # leave the bus clean
            return 1  # cannot test
    time.sleep(0.3)  # let the channels settle
    for channel in bench.CHANNELS:  # discard what the open produced
        bench.Capture().drain(channel)  # empty queues

    #- Time each direction
    failures = []  # what went wrong
    for sender, want_echo in ((bench.CHANNEL_B, False), (bench.CHANNEL_A, True)):  # B's frames through A, then A's frames and echoes
        received, bad, frame_ms, echo_ms = lone_frames(sender, want_echo)  # the run
        print("%s sends %d lone frames: received %d, bad %d | receive %s | echo %s" % (
            bench.NAMES[sender], FRAMES, received, bad, spread(frame_ms), spread(echo_ms) if want_echo else "-"))  # results
        if received != FRAMES or bad or (frame_ms and max(frame_ms) > LIMIT_MS):  # missing, changed or slow frames
            failures.append("%s: frames missing, changed or slower than %.0f ms" % (bench.NAMES[sender], LIMIT_MS))  # name the direction
        if want_echo and (len(echo_ms) != FRAMES or (echo_ms and max(echo_ms) > LIMIT_MS)):  # missing or slow echoes
            failures.append("%s: echoes missing or slower than %.0f ms" % (bench.NAMES[sender], LIMIT_MS))  # name the direction
    bench.close_channels()  # bus off

    #- Verdict
    print("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
