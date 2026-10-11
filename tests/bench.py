"""Two-board CAN test bench for the XCAN firmware, driven through the Windows CAN Driver's API.

Bench: two boards running the firmware on one terminated CAN bus, both on USB, one of them also on an ST-Link (see probe.py).
The driver numbers the boards in plug-in order as its first and second USB channel, A and B in every report.

Every test frame carries a sequence number, in its data or, without data, in its identifier, so the receiver checks order, loss and content of each frame.
Frame lengths on the wire are computed exactly, stuff bits included, to turn rates into bus load.
"""

import collections
import ctypes
import functools
import time

#& Driver

DRIVER_LIBRARY = "PCANBasic.dll"  # the Windows CAN Driver's API library, installed with the driver
CHANNEL_A = 0x51  # the driver's first USB channel
CHANNEL_B = 0x52  # the driver's second USB channel
CHANNELS = (CHANNEL_A, CHANNEL_B)  # both boards
NAMES = {CHANNEL_A: "A", CHANNEL_B: "B"}  # short names in reports
SIDES = {CHANNEL_A: 0, CHANNEL_B: 1}  # identifier space each board sends in, so the boards never send the same identifier

#? The driver takes the SJA1000 BTR0/BTR1 pair as its bit rate code; these are its standard settings.
BIT_RATES = {1000: 0x0014, 500: 0x001C, 250: 0x011C, 125: 0x031C}  # kbit/s -> BTR0/BTR1

OK = 0x00000  # status without error flags
QRCVEMPTY = 0x00020  # status: the receive queue is empty
QXMTFULL = 0x00080  # status: the transmit queue is full
STATUS_NAMES = {  # status flags by bit
    0x00001: "XMTFULL", 0x00002: "OVERRUN", 0x00004: "BUSLIGHT", 0x00008: "BUSHEAVY", 0x00010: "BUSOFF", 0x00020: "QRCVEMPTY",
    0x00040: "QOVERRUN", 0x00080: "QXMTFULL", 0x40000: "BUSPASSIVE", 0x4000000: "INITIALIZE", 0x8000000: "ILLOPERATION",
}

MSG_RTR = 0x01  # message type: remote frame
MSG_EXT = 0x02  # message type: 29-bit identifier
MSG_ECHO = 0x20  # message type: echo of a frame this channel sent
MSG_ERRFRAME = 0x40  # message type: bus error report
MSG_STATUS = 0x80  # message type: bus state report

PARAM_BUSOFF_AUTORESET = 0x07  # parameter: restart a bus-off channel by itself
PARAM_LISTEN_ONLY = 0x08  # parameter: receive without acknowledging or sending
PARAM_ALLOW_ERROR_FRAMES = 0x20  # parameter: deliver bus errors as error frames
PARAM_FIRMWARE_VERSION = 0x29  # parameter: firmware version text
PARAM_ALLOW_ECHO_FRAMES = 0x2C  # parameter: deliver an echo for each frame sent


class Message(ctypes.Structure):
    """One CAN frame as the driver passes it."""

    _fields_ = [("ID", ctypes.c_uint), ("MSGTYPE", ctypes.c_ubyte), ("LEN", ctypes.c_ubyte), ("DATA", ctypes.c_ubyte * 8)]  # the driver's frame layout


class Timestamp(ctypes.Structure):
    """When a frame arrived, from the board's timestamps converted by the driver."""

    _fields_ = [("millis", ctypes.c_uint), ("millis_overflow", ctypes.c_ushort), ("micros", ctypes.c_ushort)]  # the driver's time layout


driver = ctypes.WinDLL(DRIVER_LIBRARY)  # raises OSError without the driver
for _name in ("CAN_Initialize", "CAN_Uninitialize", "CAN_GetStatus", "CAN_Read", "CAN_Write", "CAN_SetValue", "CAN_GetValue"):  # every call the bench makes
    getattr(driver, _name).restype = ctypes.c_uint  # status codes are unsigned 32-bit


def status_text(status):
    """Name the flags of a driver status code.

    Args:
        status: Status code from the driver.

    Returns:
        "OK", the flag names joined by "|", or the code in hex when no flag is known.
    """
    if status == OK:  # no flag set
        return "OK"  # healthy channel
    names = [name for bit, name in STATUS_NAMES.items() if status & bit]  # known flags
    return "|".join(names) or "0x%X" % status  # unknown codes in hex


def set_value(channel, parameter, value):
    """Set a 32-bit channel parameter.

    Args:
        channel: CHANNEL_A or CHANNEL_B.
        parameter: One of the PARAM_* codes.
        value: New value.

    Returns:
        The driver status.
    """
    number = ctypes.c_uint(value)  # the driver reads the value through a pointer
    return driver.CAN_SetValue(channel, ctypes.c_ubyte(parameter), ctypes.byref(number), 4)  # four bytes


def get_text(channel, parameter):
    """Read a text parameter, such as PARAM_FIRMWARE_VERSION.

    Args:
        channel: CHANNEL_A or CHANNEL_B; open it first.
        parameter: One of the PARAM_* codes.

    Returns:
        The text, or "error" with the status names when the driver refuses.
    """
    text = ctypes.create_string_buffer(256)  # room for any parameter text
    status = driver.CAN_GetValue(channel, ctypes.c_ubyte(parameter), text, 256)  # fills the buffer
    return text.value.decode(errors="replace") if status == OK else "error " + status_text(status)  # text or the reason


def open_channel(channel, btr, echo=False, error_frames=False, listen_only=False):
    """Put a channel on the bus.

    Args:
        channel: CHANNEL_A or CHANNEL_B.
        btr: BTR0/BTR1 bit timing code, such as BIT_RATES[1000].
        echo: Deliver an echo for each frame this channel sends.
        error_frames: Deliver bus errors as error frames.
        listen_only: Receive without acknowledging or sending.

    Returns:
        The driver status of the open.
    """
    set_value(channel, PARAM_LISTEN_ONLY, 1 if listen_only else 0)  # applies to this open
    status = driver.CAN_Initialize(channel, btr, 0, 0, 0)  # bus on
    if status == OK and echo:  # echoes asked for
        set_value(channel, PARAM_ALLOW_ECHO_FRAMES, 1)  # an echo per frame sent
    if status == OK and error_frames:  # error frames asked for
        set_value(channel, PARAM_ALLOW_ERROR_FRAMES, 1)  # bus errors as error frames
    return status  # OK, or why the channel stays closed


def close_channel(channel):
    """Take one channel off the bus.

    Args:
        channel: CHANNEL_A or CHANNEL_B.
    """
    driver.CAN_Uninitialize(channel)  # bus off


def close_channels():
    """Take both channels off the bus."""
    for channel in CHANNELS:  # whichever is open
        close_channel(channel)  # bus off


def read(channel):
    """Take the next message a channel received.

    Args:
        channel: CHANNEL_A or CHANNEL_B.

    Returns:
        (status, Message, Timestamp); status has QRCVEMPTY set when nothing was waiting.
    """
    message, stamp = Message(), Timestamp()  # filled by the driver
    status = driver.CAN_Read(channel, ctypes.byref(message), ctypes.byref(stamp))  # next message
    return status, message, stamp  # the message is valid only with OK


def write(channel, message):
    """Queue one frame for sending.

    Args:
        channel: Sending channel.
        message: Message to send, such as one from make_frame().

    Returns:
        The driver status: OK, QXMTFULL when the queue is full, or why the channel refused.
    """
    return driver.CAN_Write(channel, ctypes.byref(message))  # the driver copies the frame


def channel_status(channel):
    """Read a channel's status.

    Args:
        channel: CHANNEL_A or CHANNEL_B.

    Returns:
        The status code, OK or its flags.
    """
    return driver.CAN_GetStatus(channel)  # current flags


#& Frames

#? A test frame either carries 8 data bytes or none; without data the sequence number goes into the identifier.
FrameKind = collections.namedtuple("FrameKind", "name extended length")  # report name, 29-bit identifier, data bytes
KINDS = (  # every layout the throughput test sends
    FrameKind("11-bit 8B", False, 8),
    FrameKind("29-bit 8B", True, 8),
    FrameKind("29-bit 0B", True, 0),
    FrameKind("11-bit 0B", False, 0),
)


def frame_fields(kind, side, sequence):
    """Build the identifier and data of one test frame.

    Args:
        kind: FrameKind of the frame.
        side: 0 for board A's frames, 1 for board B's.
        sequence: Frame number from 0.

    Returns:
        (identifier, extended, data bytes).
    """
    if kind.length == 0 and kind.extended:  # 29-bit identifier without data
        return ((side + 1) << 26) | (sequence & 0x3FFFFFF), True, b""  # side in the top bits, sequence below
    if kind.length == 0:  # 11-bit identifier without data
        return ((sequence & 0x3FF) << 1) | side, False, b""  # sequence modulo 1024, side in bit 0
    word = sequence ^ 0xA5A5A5A5  # second data word, a pattern that differs from the first
    data = bytes([(sequence >> (8 * k)) & 0xFF for k in range(4)] + [(word >> (8 * k)) & 0xFF for k in range(4)])  # sequence, then pattern, little-endian
    return ((0x1000000 << side) | 0x123) if kind.extended else (0x100 << side), kind.extended, data  # fixed identifier per side


def make_frame(kind, side, sequence):
    """Build one test frame for the driver.

    Args:
        kind: FrameKind of the frame.
        side: 0 for board A's frames, 1 for board B's.
        sequence: Frame number from 0.

    Returns:
        The Message.
    """
    can_id, extended, data = frame_fields(kind, side, sequence)  # what goes on the wire
    message = Message()  # zeroed frame
    message.ID, message.MSGTYPE, message.LEN = can_id, MSG_EXT if extended else 0, len(data)  # header
    for index, byte in enumerate(data):  # payload
        message.DATA[index] = byte  # one byte at a time, ctypes arrays take no slices
    return message  # ready for CAN_Write


def sequence_of(kind, can_id, data):
    """Recover a test frame's sequence number.

    Args:
        kind: FrameKind the frame was sent as.
        can_id: Received identifier.
        data: Received data bytes.

    Returns:
        The sequence number, modulo 1024 for 11-bit frames without data, or -1 when the frame is too short to hold one.
    """
    if kind.length == 0:  # number in the identifier
        return (can_id & 0x3FFFFFF) if kind.extended else (can_id >> 1)  # undo frame_fields()
    if len(data) < 4:  # truncated payload
        return -1  # no sequence number
    return data[0] | data[1] << 8 | data[2] << 16 | data[3] << 24  # first data word


def sequence_modulus(kind):
    """Tell when sequence numbers of a frame kind wrap.

    Args:
        kind: FrameKind.

    Returns:
        1024 for 11-bit frames without data, 2**32 for the others.
    """
    return 1024 if kind.length == 0 and not kind.extended else 1 << 32  # identifier bits limit the short frames


def _bits(value, width):
    """Split a value into bits, most significant first.

    Args:
        value: Unsigned value.
        width: Number of bits.

    Returns:
        List of 0 and 1.
    """
    return [(value >> (width - 1 - k)) & 1 for k in range(width)]  # MSB first, as on the wire


def _crc15(stream):
    """Compute the CAN CRC-15 of a bit stream.

    Args:
        stream: Bits from start of frame to the end of the data field.

    Returns:
        The 15-bit CRC.
    """
    crc = 0  # initial value
    for bit in stream:  # each bit, first on the wire first
        top = bit ^ (crc >> 14)  # bit shifted out XOR incoming bit
        crc = (crc << 1) & 0x7FFF  # shift within 15 bits
        if top:  # feedback
            crc ^= 0x4599  # CAN polynomial
    return crc  # CRC sequence


@functools.lru_cache(maxsize=None)
def wire_bits(kind, side, sequence):
    """Count a test frame's bit times on the wire.

    Args:
        kind: FrameKind of the frame.
        side: 0 for board A's frames, 1 for board B's.
        sequence: Frame number.

    Returns:
        Bit times from start of frame through the 3-bit intermission, stuff bits included.
    """
    #- Unstuffed bits from start of frame through the CRC
    can_id, extended, data = frame_fields(kind, side, sequence)  # the frame as sent
    if extended:  # SOF, base ID, SRR, IDE, extended ID, RTR, r1, r0, DLC
        stream = [0] + _bits(can_id >> 18, 11) + [1, 1] + _bits(can_id & 0x3FFFF, 18) + [0, 0, 0] + _bits(len(data), 4)  # 29-bit header
    else:  # SOF, ID, RTR, IDE, r0, DLC
        stream = [0] + _bits(can_id, 11) + [0, 0, 0] + _bits(len(data), 4)  # 11-bit header
    for byte in data:  # payload, first byte first
        stream += _bits(byte, 8)  # MSB first
    stream += _bits(_crc15(stream), 15)  # CRC sequence

    #- Stuff bits: one complement bit after five equal bits
    count, last, run = 0, None, 0  # bits so far, last bit level, length of the current run
    for bit in stream:  # stuffed region
        count += 1  # the bit itself
        run = run + 1 if bit == last else 1  # extend or restart the run
        last = bit  # level for the next comparison
        if run == 5:  # five equal bits
            count += 1  # the stuff bit
            last, run = 1 - bit, 1  # the stuff bit starts a new run
    return count + 13  # CRC delimiter, ACK slot and delimiter, 7-bit EOF, 3-bit intermission


#& Capturing


class Capture:
    """What one channel delivered: frames from the other board, echoes of its own frames, and status or error notes."""

    def __init__(self):
        """Start an empty capture."""
        self.frames = []  # (us, identifier, extended, data) from the other board
        self.echoes = []  # (us, identifier, extended, data) of frames this channel sent
        self.notes = []  # status messages, error frames and read errors as text

    def drain(self, channel):
        """Read everything the driver queued for a channel.

        Args:
            channel: CHANNEL_A or CHANNEL_B.
        """
        message, stamp = Message(), Timestamp()  # reused for each read
        while True:  # until the queue is empty
            status = driver.CAN_Read(channel, ctypes.byref(message), ctypes.byref(stamp))  # next message
            if status & QRCVEMPTY:  # nothing left
                return  # queue drained
            if status != OK:  # overrun or other trouble
                self.notes.append("read " + status_text(status))  # keep the reason
                continue  # next message
            if message.MSGTYPE & (MSG_STATUS | MSG_ERRFRAME):  # report, not a frame
                kind = "status" if message.MSGTYPE & MSG_STATUS else "errframe"  # which report
                self.notes.append("%s 0x%X %s" % (kind, message.ID, bytes(message.DATA[:message.LEN]).hex(" ")))  # keep it as text
                continue  # next message
            micros = stamp.micros + 1000 * (stamp.millis + (stamp.millis_overflow << 32))  # arrival time in us
            entry = (micros, message.ID, bool(message.MSGTYPE & MSG_EXT), bytes(message.DATA[:message.LEN]))  # copy, the buffer is reused
            (self.echoes if message.MSGTYPE & MSG_ECHO else self.frames).append(entry)  # sort by origin


def drain_until_quiet(captures, quiet=1.0):
    """Read both channels until nothing new arrives for a while.

    Args:
        captures: {channel: Capture}.
        quiet: Seconds without a new message that end the wait.
    """
    idle, last = time.perf_counter(), None  # start of the quiet period, message counts seen
    while time.perf_counter() - idle < quiet:  # still within the quiet period
        for channel in CHANNELS:  # both boards
            captures[channel].drain(channel)  # take what arrived
        now = tuple(len(c.frames) + len(c.echoes) for c in captures.values())  # message counts
        if now != last:  # something new
            last, idle = now, time.perf_counter()  # restart the quiet period
        time.sleep(0.001)  # let the driver collect more


def send_burst(kind, count, senders, stall=3.0):
    """Send numbered frames from each sender as fast as the driver takes them, reading both channels meanwhile.

    Args:
        kind: FrameKind to send.
        count: Frames per sender.
        senders: Channels that send, one or both.
        stall: Seconds without a frame accepted that end the burst early.

    Returns:
        ({channel: Capture}, {sender: frames sent}, {sender: writes refused with a full queue}).
    """
    #- Start from empty queues
    captures = {channel: Capture() for channel in CHANNELS}  # one per board
    for channel in CHANNELS:  # leftovers from earlier runs
        Capture().drain(channel)  # discard

    #- Send, alternating senders, until each sent its frames or the bus stalls
    sent = {channel: 0 for channel in senders}  # frames accepted per sender
    full = {channel: 0 for channel in senders}  # writes refused per sender
    pending = {channel: make_frame(kind, SIDES[channel], 0) for channel in senders}  # next frame per sender
    active = list(senders)  # senders with frames left
    last_progress = time.perf_counter()  # when the driver last took a frame
    while active:  # frames left to send
        for channel in list(active):  # one write per sender per pass
            status = driver.CAN_Write(channel, ctypes.byref(pending[channel]))  # queue the frame
            if status == OK:  # taken
                sent[channel] += 1  # count it
                last_progress = time.perf_counter()  # the bus moves
                if sent[channel] == count:  # all sent
                    active.remove(channel)  # this sender is done
                else:
                    pending[channel] = make_frame(kind, SIDES[channel], sent[channel])  # next number
            elif status & QXMTFULL:  # driver queue full
                full[channel] += 1  # try again next pass
            else:  # channel trouble
                captures[channel].notes.append("write " + status_text(status))  # keep the reason
                active.remove(channel)  # stop this sender
        for channel in CHANNELS:  # keep the receive queues short
            captures[channel].drain(channel)  # read what arrived
        if time.perf_counter() - last_progress > stall:  # nothing accepted for too long
            for channel in active:  # senders cut short
                captures[channel].notes.append("write stalled at %d" % sent[channel])  # report the stall
            break  # give up

    #- Collect the frames still on their way
    drain_until_quiet(captures)  # until both channels fall silent
    return captures, sent, full  # what each board delivered and sent


def send_frames(channel, can_id, count):
    """Queue simple data frames: 8 bytes, the frame index in the first data word.

    Args:
        channel: Sending channel.
        can_id: 11-bit identifier.
        count: Frames to queue.

    Returns:
        Frames the driver accepted.
    """
    accepted = 0  # frames taken
    for index in range(count):  # each frame
        message = Message()  # zeroed frame
        message.ID, message.MSGTYPE, message.LEN = can_id, 0, 8  # 11-bit data frame
        for k in range(4):  # first data word
            message.DATA[k] = (index >> (8 * k)) & 0xFF  # index, little-endian
        accepted += driver.CAN_Write(channel, ctypes.byref(message)) == OK  # count accepted frames
    return accepted  # how many were queued


def read_frames(channel, seconds):
    """Read the data frames a channel receives for a while.

    Args:
        channel: Receiving channel.
        seconds: How long to read.

    Returns:
        List of (identifier, index from the first two data bytes), echoes, status and error frames left out.
    """
    frames, message, stamp = [], Message(), Timestamp()  # results and read buffers
    end = time.time() + seconds  # stop time
    while time.time() < end:  # read until then
        status = driver.CAN_Read(channel, ctypes.byref(message), ctypes.byref(stamp))  # next message
        if status & QRCVEMPTY:  # nothing yet
            time.sleep(0.005)  # wait for more
            continue  # read again
        if status == OK and not message.MSGTYPE & (MSG_ECHO | MSG_ERRFRAME | MSG_STATUS):  # a data frame
            frames.append((message.ID, message.DATA[0] | message.DATA[1] << 8))  # identifier and index
    return frames  # in arrival order


#& Analysis


def decode(kind, side, entries):
    """Check received frames or echoes against what a board sent.

    Args:
        kind: FrameKind the frames were sent as.
        side: 0 when board A sent them, 1 for board B.
        entries: (us, identifier, extended, data) from a Capture.

    Returns:
        ([(us, side, sequence)], count of frames whose identifier, length or data differ from the sent frame).
    """
    decoded, bad = [], 0  # good frames, mismatches
    for micros, can_id, extended, data in entries:  # each frame
        sequence = sequence_of(kind, can_id, data)  # its number
        if sequence < 0 or frame_fields(kind, side, sequence) != (can_id, extended, data):  # not what was sent
            bad += 1  # corrupted or foreign
            continue  # leave it out
        decoded.append((micros, side, sequence))  # time and number
    return decoded, bad  # the good frames and the mismatch count


def rate_and_load(kind, kbps, decoded):
    """Turn decoded frames into a frame rate and a bus load.

    Args:
        kind: FrameKind of the frames.
        kbps: Bit rate.
        decoded: [(us, side, sequence)] in arrival order.

    Returns:
        (frames per second, percent of the bus the frames occupied).
    """
    if len(decoded) < 2 or decoded[-1][0] <= decoded[0][0]:  # no span to measure
        return 0.0, 0.0  # nothing to report
    span = (decoded[-1][0] - decoded[0][0]) / 1e6  # seconds from the first frame to the last
    wire = sum(wire_bits(kind, side, sequence) for _, side, sequence in decoded[1:])  # bit times the later frames took
    return (len(decoded) - 1) / span, 100.0 * wire / (span * kbps * 1000.0)  # frames/s and percent of bus time


def direction(kind, kbps, sender, captures, sent):
    """Report one direction of a burst: delivery at the receiver and echoes at the sender.

    Args:
        kind: FrameKind sent.
        kbps: Bit rate.
        sender: Sending channel.
        captures: {channel: Capture} from send_burst().
        sent: {sender: frames sent} from send_burst().

    Returns:
        Dict with dir, sent, received, lost, dup, gaps, bad, rate, load, echoes, echo_gaps, echo_rate.
    """
    receiver = CHANNEL_B if sender == CHANNEL_A else CHANNEL_A  # the other board
    frames, bad = decode(kind, SIDES[sender], captures[receiver].frames)  # what arrived
    echoes, echo_bad = decode(kind, SIDES[sender], captures[sender].echoes)  # what the sender saw leave
    modulus = sequence_modulus(kind)  # where numbers wrap
    sequences = [s for _, _, s in frames]  # arrival order
    gaps = sum(1 for a, b in zip(sequences, sequences[1:]) if b != (a + 1) % modulus)  # breaks in the order
    unique = len(set(sequences)) if modulus > 1024 else len(sequences)  # wrapped numbers repeat by design
    echo_sequences = [s for _, _, s in echoes]  # echo order
    echo_gaps = sum(1 for a, b in zip(echo_sequences, echo_sequences[1:]) if b != (a + 1) % modulus)  # breaks in the echo order
    rate, load = rate_and_load(kind, kbps, frames)  # at the receiver
    echo_rate, _ = rate_and_load(kind, kbps, echoes)  # at the sender
    return {"dir": "%s->%s" % (NAMES[sender], NAMES[receiver]), "sent": sent[sender], "received": len(frames), "lost": sent[sender] - unique,
            "dup": len(frames) - unique, "gaps": gaps, "bad": bad + echo_bad, "rate": rate, "load": load,
            "echoes": len(echoes), "echo_gaps": echo_gaps, "echo_rate": echo_rate}  # one row of the report


def whole_bus(kind, kbps, channel, captures):
    """Measure the whole bus as one channel saw it: its own echoes plus the other board's frames, on its clock.

    Args:
        kind: FrameKind sent by both boards.
        kbps: Bit rate.
        channel: Channel whose view to use.
        captures: {channel: Capture} from send_burst().

    Returns:
        (frames per second, percent of the bus) for both boards together.
    """
    other = CHANNEL_B if channel == CHANNEL_A else CHANNEL_A  # the other board
    own, _ = decode(kind, SIDES[channel], captures[channel].echoes)  # this board's frames, when they left
    theirs, _ = decode(kind, SIDES[other], captures[channel].frames)  # the other board's frames, when they arrived
    return rate_and_load(kind, kbps, sorted(own + theirs))  # one timeline
