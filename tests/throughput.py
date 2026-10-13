"""Throughput of the XCAN firmware between two boards: every bit rate and frame layout in Tx Only, Rx Only and RxTx, echo on.

Usage:
    python tests/throughput.py              every bit rate in bench.BIT_RATES
    python tests/throughput.py 1000 500     only these, in kbit/s

The modes are named from board A: Tx Only, A sends to B; Rx Only, B sends to A; RxTx, both send at once.
Every frame and every echo is checked against what was sent, and any loss, duplicate, reordering, corruption, missing echo or error state fails the test.
Load is the share of bus time the frames used, from each frame's exact length on the wire, so 100% means back to back.
In RxTx each direction's rate and load are its share of the whole bus as board A saw it, so the directions add up to the bus total.
With the probe and the board's ELF (see probe.py), the probed board's firmware counters must also stay free of errors and overflows.
"""

import sys

import bench
import probe

#& Settings

BURST_SECONDS = 2.0  # each burst lasts about this long at wire speed
MIN_FRAMES = 2000  # smallest burst per sender
MAX_FRAMES = 40000  # largest burst per sender
WARMUP_FRAMES = 300  # frames each way before the first burst


def burst_size(kind, kbps):
    """Pick the frames per sender for a burst of about BURST_SECONDS at wire speed.

    Args:
        kind: bench.FrameKind of the burst.
        kbps: Bit rate.

    Returns:
        Frames per sender.
    """
    lengths = [bench.wire_bits(kind, side, sequence) for side in (0, 1) for sequence in range(1000)]  # typical frames
    wire_rate = kbps * 1000.0 / (sum(lengths) / len(lengths))  # frames per second back to back
    return int(min(MAX_FRAMES, max(MIN_FRAMES, BURST_SECONDS * wire_rate)))  # within the limits


def counters_or_none(symbols):
    """Read the probed board's firmware counters when a probe answers.

    Args:
        symbols: Result of probe.elf_symbols(), or None.

    Returns:
        The counters, or None without a probe.
    """
    if symbols is None:  # no ELF
        return None  # nothing to read
    try:  # the probe is optional
        return probe.firmware_counters(symbols)  # current counters
    except (OSError, RuntimeError, KeyError):  # no programmer, no answer or another image
        return None  # skip the counter check


def main():
    """Run the matrix and print one line per direction, then a summary.

    Returns:
        0 when every run passed, 1 otherwise.
    """
    #^ Prepare
    #- Bit rates and the firmware image
    rates = [int(arg) for arg in sys.argv[1:]] or list(bench.BIT_RATES)  # kbit/s
    try:  # the ELF is optional
        symbols = probe.elf_symbols()  # addresses for the counter check
    except (OSError, ValueError) as error:  # no build here
        symbols = None  # run without counters
        print("no firmware counters: %s" % error)  # say why
    failures, summary, probed, counters = [], [], None, None  # results across the matrix

    for kbps in rates:  # each bit rate
        #- Both channels on the bus, echo on
        for channel in bench.CHANNELS:  # both boards
            status = bench.open_channel(channel, bench.BIT_RATES[kbps], echo=True)  # echo for every frame sent
            if status != bench.OK:  # board missing or busy
                print("open %s at %dk: %s" % (bench.NAMES[channel], kbps, bench.status_text(status)))  # say which
                bench.close_channels()  # leave the bus clean
                return 1  # cannot test
        if kbps == rates[0]:  # once, before the first burst
            versions = [bench.get_text(channel, bench.PARAM_FIRMWARE_VERSION) for channel in bench.CHANNELS]  # what each board runs
            print("firmware A %s, B %s" % tuple(versions))  # both versions
            #? Error counters survive a reopen, and an error-passive sender adds 8 bit times after each frame, which earlier error tests leave behind.
            bench.send_burst(bench.KINDS[0], WARMUP_FRAMES, list(bench.CHANNELS))  # good frames each way bring the counters down
            probed = probe.probed_channel(symbols) if symbols else None  # which board the counters belong to
            counters = counters_or_none(symbols) if probed else None  # counters before the matrix
            print("board on the probe: %s" % (bench.NAMES[probed] if probed else "none found"))  # whose counters follow

        #^ Bursts
        for kind in bench.KINDS:  # each frame layout
            count = burst_size(kind, kbps)  # frames per sender
            print("\n%dk %s, %d frames per sender" % (kbps, kind.name, count))  # case header
            for mode, senders in ((bench.TX_ONLY, [bench.CHANNEL_A]), (bench.RX_ONLY, [bench.CHANNEL_B]), (bench.RXTX, list(bench.CHANNELS))):  # traffic modes
                #- Send and check every frame and echo
                captures, sent, full = bench.send_burst(kind, count, senders)  # the burst
                rows = {sender: bench.direction(kind, kbps, sender, captures, sent) for sender in senders}  # delivery and echoes per direction
                if mode == bench.RXTX:  # both boards sent
                    #? Each direction's own first-to-last span overlaps the other's, so its share of one common timeline is what adds up to the bus.
                    a_share, b_share = bench.bus_share(kind, kbps, bench.CHANNEL_A, captures)  # A->B and B->A as board A saw them
                    rows[bench.CHANNEL_A].update(rate=a_share[0], load=a_share[1])  # A's share of the bus
                    rows[bench.CHANNEL_B].update(rate=b_share[0], load=b_share[1])  # B's share of the bus
                for sender in senders:  # each direction in the burst
                    row = rows[sender]  # delivery and echoes
                    row.update(kbps=kbps, kind=kind.name, mode=mode)  # where it belongs in the summary
                    summary.append(row)  # for the table at the end
                    print("  %-7s %s  sent %5d  received %5d  lost %d  dup %d  gaps %d  bad %d | %6.0f/s %5.1f %% | echoes %5d gaps %d %6.0f/s | queue full %d" % (
                        mode, row["dir"], row["sent"], row["received"], row["lost"], row["dup"], row["gaps"], row["bad"], row["rate"], row["load"],
                        row["echoes"], row["echo_gaps"], row["echo_rate"], full[sender]))  # one line per direction
                    if row["lost"] or row["dup"] or row["gaps"] or row["bad"] or row["echoes"] != row["sent"] or row["echo_gaps"]:  # anything off
                        failures.append("%dk %s %s %s: frames or echoes wrong" % (kbps, kind.name, mode, row["dir"]))  # name the run

                #- Whole bus and channel states
                if mode == bench.RXTX:  # both boards sent
                    for channel in bench.CHANNELS:  # each board's view
                        rate, load = bench.whole_bus(kind, kbps, channel, captures)  # both directions together
                        print("  %-7s bus seen by %s: %6.0f/s %5.1f %%" % (mode, bench.NAMES[channel], rate, load))  # bus total
                        viewer = "bus by " + bench.NAMES[channel]  # whose timestamps
                        summary.append({"kbps": kbps, "kind": kind.name, "mode": "bus", "dir": viewer, "rate": rate, "load": load})  # bus total
                for channel in bench.CHANNELS:  # after every burst
                    status = bench.channel_status(channel)  # warning, passive or worse
                    notes = captures[channel].notes  # status messages and read errors
                    if status != bench.OK or notes:  # anything to report
                        print("    %s: %s %s" % (bench.NAMES[channel], bench.status_text(status), "; ".join(notes[:6])))  # first notes only
                    if status != bench.OK:  # not error active
                        failures.append("%dk %s %s: %s %s" % (kbps, kind.name, mode, bench.NAMES[channel], bench.status_text(status)))  # name the state

            #- Firmware counters of the probed board
            now = counters_or_none(symbols) if probed else None  # after this frame layout
            if counters and now:  # both reads worked
                delta = {name: (now[name] - counters[name]) & 0xFFFFFFFF for name in probe.COUNTERS}  # change, wrap-safe
                print("  firmware counters of %s: %s" % (bench.NAMES[probed], ", ".join("%s +%d" % item for item in delta.items())))  # the changes
                if delta["tx_errs"] or delta["tx_ovfs"] or delta["bus_errs"] or delta["rx_ovfs"] or delta["bus_err_ovfs"]:  # errors or overflows
                    failures.append("%dk %s: firmware counted errors or overflows" % (kbps, kind.name))  # name the layout
            counters = now or counters  # next baseline
        bench.close_channels()  # before the next bit rate

    #^ Summary
    #- Table of every run
    layout = "%-6s %-10s %-8s %-10s %7s %7s %5s %5s %4s %8s %7s"  # column widths
    print("\n" + layout % ("rate", "frames", "mode", "dir", "sent", "recv", "lost", "gaps", "bad", "frames/s", "load"))  # header
    for row in summary:  # every direction and bus total
        is_bus = row["mode"] == "bus"  # bus total rows count no frames
        counts = ("",) * 5 if is_bus else (row["sent"], row["received"], row["lost"], row["gaps"], row["bad"])  # frame counts
        mode = bench.RXTX if is_bus else row["mode"]  # bus totals come from the RxTx bursts
        print(layout % (("%dk" % row["kbps"], row["kind"], mode, row["dir"]) + counts + ("%.0f" % row["rate"], "%.1f%%" % row["load"])))  # one row

    #- Verdict
    print("\nPASS" if not failures else "\nFAIL\n  " + "\n  ".join(failures))  # every failure by name
    return 1 if failures else 0  # exit code


if __name__ == "__main__":
    sys.exit(main())  # main()'s result as the exit code
