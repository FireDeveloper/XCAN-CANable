"""Where board A's CPU time goes while it sends back to back, from program counter samples read over SWD.

Usage:
    python tests/cpu_profile.py               11-bit frames without data at 1 Mbit/s, echo on
    python tests/cpu_profile.py --echo-off    the same without echoes
    python tests/cpu_profile.py --kind 0      another layout from bench.KINDS

Board A must be the probed board, running the image whose ELF probe.py finds, so every sample can be named.
DWT_PCSR holds a recent program counter; reading it over and over while A sends gives each function's share of the CPU.
Informational: exits 1 only when the probe, the ELF or board A is missing.
"""

import argparse
import collections
import sys
import threading

import bench
import probe

#& Settings

KBPS = 1000  # bit rate of the run
FRAMES = 300000  # frames board A sends while the sampler runs
WARMUP = 2000  # frames sent before sampling starts
SAMPLES_PER_SESSION = 50  # program counter reads per probe session
TOP = 30  # functions listed


def sample_until(stop, samples):
    """Collect program counter samples until stop is set.

    Args:
        stop: threading.Event that ends the sampling.
        samples: List the samples are appended to.
    """
    while not stop.is_set():  # until the burst ends
        samples += probe.sample_word(probe.DWT_PCSR, SAMPLES_PER_SESSION)  # one probe session


def main():
    """Profile board A during a burst and list the busiest functions.

    Returns:
        0 after a profile, 1 when the probe, the ELF or board A is missing.
    """
    #^ Prepare
    #- Options and the image's functions
    parser = argparse.ArgumentParser(description="Profile board A over SWD while it sends back to back.")  # command line
    parser.add_argument("--echo-off", action="store_true", help="send without echoes")  # less work per frame
    parser.add_argument("--kind", type=int, default=3, choices=range(len(bench.KINDS)), help="frame layout, index into bench.KINDS")  # layout
    options = parser.parse_args()  # parsed
    try:  # the ELF names the samples
        symbols = probe.elf_symbols()  # every function
    except (OSError, ValueError) as error:  # no build here
        print("cannot name the samples: %s" % error)  # say why
        return 1  # no profile
    name_of = probe.function_finder(symbols)  # address to function

    #- Board A on the probe and on the bus
    for channel in bench.CHANNELS:  # both boards
        bench.open_channel(channel, bench.BIT_RATES[KBPS], echo=not options.echo_off)  # echo as asked
    if probe.probed_channel(symbols) != bench.CHANNEL_A:  # wrong board on the probe, or none
        print("board A must be on the probe")  # samples would belong to the other board
        bench.close_channels()  # bus off
        return 1  # no profile

    #^ Burst with the sampler running
    #- Send, sampling from the end of the warm-up to the last write
    kind = bench.KINDS[options.kind]  # layout to send
    frames = [bench.make_frame(kind, 0, sequence) for sequence in range(1024)]  # reused frames, sequence order is not checked here
    captures = {channel: bench.Capture() for channel in bench.CHANNELS}  # received messages, read to keep the queues short
    stop, samples = threading.Event(), []  # sampler control and results
    sampler = threading.Thread(target=sample_until, args=(stop, samples))  # runs beside the writes
    for index in range(FRAMES):  # back to back
        if index == WARMUP:  # the bus is full now
            sampler.start()  # start sampling
        while bench.write(bench.CHANNEL_A, frames[index & 0x3FF]) & bench.QXMTFULL:  # driver queue full
            for channel in bench.CHANNELS:  # make room
                captures[channel].drain(channel)  # read what arrived
    stop.set()  # the last frame is queued, the bus is still full
    sampler.join()  # finish the current session
    bench.drain_until_quiet(captures)  # let the burst end
    bench.close_channels()  # bus off

    #- Count the samples per function
    histogram = collections.Counter(name_of(address) for address in samples)  # samples per function
    print("%d samples, %s at %d kbit/s, echo %s" % (len(samples), kind.name, KBPS, "off" if options.echo_off else "on"))  # what was measured
    for name, count in histogram.most_common(TOP):  # busiest first
        print("  %5.1f %%  %s" % (100.0 * count / len(samples), name))  # share and function
    return 0  # informational


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
