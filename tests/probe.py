"""SWD access to the board on the ST-Link, through STM32_Programmer_CLI, for the tests that look inside the firmware.

Addresses of the firmware's own variables come from its ELF, src/build/Release/xcan_canable.elf or the file named by XCAN_ELF, so the tests follow every build.
The programmer is found through XCAN_PROGRAMMER, the PATH, STM32CubeProgrammer's install folder or the STM32Cube extension's bundles.
Every read uses hot-plug mode, so the firmware keeps running while it is watched.
"""

import bisect
import glob
import os
import shutil
import struct
import subprocess
import time

import bench

#& Locations

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repository root, one level above tests/
DEFAULT_ELF = os.path.join(REPO, "src", "build", "Release", "xcan_canable.elf")  # image of the Release preset
CAN_ESR = 0x40006418  # bxCAN error status: TEC, REC and the warning, passive and bus-off flags
CAN_BTR = 0x4000641C  # bxCAN bit timing
DWT_PCSR = 0xE000101C  # program counter sample register
COUNTERS = ("tx_msgs", "tx_errs", "tx_ovfs", "rx_msgs", "rx_errs", "rx_ovfs")  # the counters can_dev in src/Src/pcan_can.c starts with


def programmer():
    """Find STM32_Programmer_CLI.

    Returns:
        Path of the executable.

    Raises:
        FileNotFoundError: No programmer in any known place.
    """
    executable = "STM32_Programmer_CLI.exe"  # file name in every install
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")  # standalone install root
    standalone = os.path.join(program_files, "STMicroelectronics", "STM32Cube", "STM32CubeProgrammer", "bin", executable)  # STM32CubeProgrammer
    bundles = os.path.join(os.environ.get("LOCALAPPDATA", ""), "stm32cube", "bundles", "programmer", "*", "bin", executable)  # STM32Cube extension
    candidates = [os.environ.get("XCAN_PROGRAMMER"), shutil.which("STM32_Programmer_CLI"), standalone]  # explicit choice, PATH, standalone
    candidates += sorted(glob.glob(bundles), reverse=True)  # extension bundles, latest version folder first
    for path in candidates:  # first one that exists
        if path and os.path.isfile(path):  # set and present
            return path  # use it
    raise FileNotFoundError("STM32_Programmer_CLI not found; set XCAN_PROGRAMMER to its path")  # nothing installed


def _run(arguments, timeout):
    """Run the programmer connected to the board in hot-plug mode and collect its memory dump lines.

    Args:
        arguments: Commands after the connection, such as ["-r32", "0x40006418", "4"].
        timeout: Seconds before giving up.

    Returns:
        The words of every "0xADDRESS : WORD ..." line, in order.
    """
    command = [programmer(), "-c", "port=SWD", "mode=HOTPLUG", "freq=4000"] + arguments  # hot-plug leaves the firmware running
    output = subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout  # the dump goes to stdout
    words = []  # dumped words
    for line in output.splitlines():  # dump lines start with the address
        if line.startswith("0x"):  # "0xADDRESS : WORD WORD ..."
            words += [int(word, 16) for word in line.split(":")[1].split()]  # words after the colon
    return words  # every dumped word


def read_words(address, count=1):
    """Read 32-bit words from the board's memory.

    Args:
        address: Address of the first word.
        count: Number of words.

    Returns:
        List of the words.

    Raises:
        FileNotFoundError: No programmer installed.
        RuntimeError: The probe or the board did not answer.
    """
    words = _run(["-r32", hex(address), str(4 * count)], 30)  # one read
    if len(words) < count:  # probe unplugged or board unpowered
        raise RuntimeError("no answer from the probe at 0x%08X" % address)  # nothing to measure with
    return words[:count]  # the words asked for


def sample_word(address, times):
    """Read one word many times in a single probe session, as fast as the probe allows.

    Args:
        address: Address to read, such as DWT_PCSR.
        times: Number of reads.

    Returns:
        List of the values read; shorter when the probe stopped answering.
    """
    arguments = []  # one read command per sample
    for _ in range(times):  # each sample
        arguments += ["-r32", hex(address), "4"]  # one word
    return _run(arguments, 120)  # every sample in one session


#& Firmware symbols


def elf_symbols(path=None):
    """Read the symbol table of the firmware's ELF.

    Args:
        path: ELF file; None takes XCAN_ELF, then DEFAULT_ELF.

    Returns:
        {name: (address, size, kind)} with kind "func" or "object"; function addresses without the Thumb bit.

    Raises:
        OSError: The file cannot be read.
        ValueError: The file is not a 32-bit little-endian ELF with a symbol table.
    """
    #- Load and check the file
    path = path or os.environ.get("XCAN_ELF") or DEFAULT_ELF  # explicit, environment or the Release build
    with open(path, "rb") as file:  # binary
        data = file.read()  # whole file
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:  # magic, ELFCLASS32, little-endian
        raise ValueError("%s is not a 32-bit little-endian ELF" % path)  # not a firmware image

    #- Walk the section headers to the symbol table and its strings
    (section_offset,) = struct.unpack_from("<I", data, 0x20)  # e_shoff
    entry_size, section_count = struct.unpack_from("<HH", data, 0x2E)  # e_shentsize, e_shnum
    sections = [struct.unpack_from("<10I", data, section_offset + i * entry_size) for i in range(section_count)]  # Elf32_Shdr fields
    symbols = {}  # name -> (address, size, kind)
    for section in sections:  # find SHT_SYMTAB
        if section[1] != 2:  # not the symbol table
            continue  # next section
        strings = sections[section[6]]  # sh_link names its string table
        for offset in range(section[4], section[4] + section[5], 16):  # Elf32_Sym entries of 16 bytes
            name_offset, value, size, info = struct.unpack_from("<IIIB", data, offset)  # st_name, st_value, st_size, st_info
            kind = {1: "object", 2: "func"}.get(info & 0xF)  # STT_OBJECT or STT_FUNC
            if not kind:  # sections, files and other symbol types
                continue  # not needed
            start = strings[4] + name_offset  # name in the string table
            name = data[start:data.index(b"\0", start)].decode(errors="replace")  # zero-terminated
            symbols[name] = (value & ~1 if kind == "func" else value, size, kind)  # Thumb bit off for code addresses
    if not symbols:  # stripped image
        raise ValueError("%s has no symbol table" % path)  # cannot locate variables
    return symbols  # every function and variable


def function_finder(symbols):
    """Build a lookup from a code address to the function that contains it.

    Args:
        symbols: Result of elf_symbols().

    Returns:
        A function taking an address and returning the function name, or "?" outside every function.
    """
    functions = sorted((address, size, name) for name, (address, size, kind) in symbols.items() if kind == "func")  # by address
    starts = [entry[0] for entry in functions]  # start addresses for bisection

    def name_of(address):
        """Name the function at an address."""
        index = bisect.bisect_right(starts, address) - 1  # last function starting at or before the address
        if index >= 0 and address < functions[index][0] + max(functions[index][1], 2):  # inside its body
            return functions[index][2]  # found
        return "?"  # between functions, or code without a symbol
    return name_of  # the lookup


#& Firmware state


def firmware_counters(symbols):
    """Read the frame and error counters at the start of the firmware's can_dev.

    Args:
        symbols: Result of elf_symbols() for the image on the board.

    Returns:
        {counter name: value} for every name in COUNTERS.

    Raises:
        KeyError: The image has no can_dev.
        FileNotFoundError: No programmer installed.
        RuntimeError: The probe did not answer.
    """
    words = read_words(symbols["can_dev"][0], len(COUNTERS))  # first fields of can_dev
    return dict(zip(COUNTERS, words))  # by name


def error_state():
    """Read the controller's error counters and state.

    Returns:
        (transmit error counter, receive error counter, "active", "warning", "passive" or "bus-off").

    Raises:
        FileNotFoundError: No programmer installed.
        RuntimeError: The probe did not answer.
    """
    esr = read_words(CAN_ESR)[0]  # error status register
    state = "bus-off" if esr & 4 else "passive" if esr & 2 else "warning" if esr & 1 else "active"  # most severe flag first
    return (esr >> 16) & 0xFF, esr >> 24, state  # TEC, REC, state


def probed_channel(symbols):
    """Find which driver channel the probed board is, by sending frames from channel A and watching its counters.

    Args:
        symbols: Result of elf_symbols() for the image on the probed board.

    Returns:
        bench.CHANNEL_A or bench.CHANNEL_B, or None without a working probe.

    Note:
        Both channels must be open at the same bit rate, so the frames get acknowledged.
    """
    try:  # the probe is optional for most tests
        before = firmware_counters(symbols)  # counters before the frames
    except (OSError, RuntimeError, KeyError):  # no programmer, no answer or another image
        return None  # cannot tell
    bench.send_frames(bench.CHANNEL_A, 0x7F0, 20)  # frames from channel A
    time.sleep(0.5)  # let them cross the bus
    for channel in bench.CHANNELS:  # discard what they produced
        bench.Capture().drain(channel)  # empty the queues
    after = firmware_counters(symbols)  # counters after the frames
    if after["tx_msgs"] - before["tx_msgs"] >= 20:  # the probed board sent them
        return bench.CHANNEL_A  # it is channel A
    if after["rx_msgs"] - before["rx_msgs"] >= 20:  # the probed board received them
        return bench.CHANNEL_B  # it is channel B
    return None  # the counters did not move as expected
