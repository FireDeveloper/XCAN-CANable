"""Show or set the USB vendor and product IDs in an XCAN firmware image (.bin or .hex), without a rebuild.

The firmware keeps its 18-byte USB device descriptor at flash address 0x08000100 (see src/STM32F072C8Tx_FLASH.ld).
The IDs therefore sit at the same place in every build: the vendor ID at 0x08000108, the product ID at 0x0800010A, both little-endian.

Usage:
    python utilities/usb_id.py src/build/pcan_canable.bin                            show the IDs
    python utilities/usb_id.py src/build/pcan_canable.hex --vid 0x1234 --pid 0x5678  set them in the image
    python utilities/usb_id.py src/build/pcan_canable.bin --pid 0x5678 -o board.bin  write a patched copy
"""

import argparse
import sys

#& Constants

FLASH_BASE = 0x08000000  # address of the first image byte in the MCU's memory map
DESC_ADDR = 0x08000100  # USB device descriptor, placed by src/STM32F072C8Tx_FLASH.ld
VID_ADDR = DESC_ADDR + 8  # idVendor, little-endian
PID_ADDR = DESC_ADDR + 10  # idProduct, little-endian
DESC_HEADER = bytes((0x12, 0x01))  # bLength 18, bDescriptorType DEVICE

#& Image formats


class ImageError(Exception):
    """The file is not a firmware image with the USB device descriptor at its fixed address."""


class BinImage:
    """Raw binary image whose first byte sits at FLASH_BASE."""

    def __init__(self, path):
        """Load a raw binary image.

        Args:
            path: File to read.

        Raises:
            OSError: The file cannot be read.
        """
        with open(path, "rb") as file:  # binary, no newline handling
            self.data = bytearray(file.read())  # whole image, patched in memory

    def read(self, addr, size):
        """Read bytes at a flash address.

        Args:
            addr: Flash address of the first byte.
            size: Number of bytes.

        Returns:
            The bytes.

        Raises:
            ImageError: The image does not reach that far.
        """
        offset = addr - FLASH_BASE  # file offset of the first byte
        if offset < 0 or offset + size > len(self.data):  # outside the image
            raise ImageError("the image ends before 0x%08X" % (addr + size))  # too short to hold the descriptor
        return bytes(self.data[offset:offset + size])  # copy, so later writes do not change it

    def write(self, addr, value):
        """Overwrite bytes at a flash address.

        Args:
            addr: Flash address of the first byte; read() checked the range.
            value: Bytes to store.
        """
        offset = addr - FLASH_BASE  # file offset of the first byte
        self.data[offset:offset + len(value)] = value  # same length, so nothing moves

    def save(self, path):
        """Write the image to a file.

        Args:
            path: File to write.

        Raises:
            OSError: The file cannot be written.
        """
        with open(path, "wb") as file:  # binary, no newline handling
            file.write(self.data)  # whole image


class HexImage:
    """Intel HEX image; only the records holding patched bytes are re-encoded, every other line keeps its text."""

    def __init__(self, path):
        """Load an Intel HEX image and map every data byte to its record.

        Args:
            path: File to read.

        Raises:
            OSError: The file cannot be read.
            ImageError: A record is malformed or fails its checksum.
        """
        with open(path, "r", newline="") as file:  # keep the original line endings
            self.lines = file.read().splitlines(keepends=True)  # one record per line
        self.cells = {}  # flash address -> (line index, payload offset)
        base = 0  # upper address bits from the last extended address record

        #- Walk the records, tracking the extended address
        for index, line in enumerate(self.lines):
            record = self._decode(line, index)  # count, address, type, payload; None for a blank line
            if record is None:
                continue  # nothing on this line
            count, offset, rtype, payload = record  # fields of the record
            if rtype == 0x00:
                for i in range(count):
                    self.cells[base + offset + i] = (index, i)  # where this data byte lives
            elif rtype == 0x02:
                base = int.from_bytes(payload, "big") << 4  # extended segment address
            elif rtype == 0x04:
                base = int.from_bytes(payload, "big") << 16  # extended linear address

    @staticmethod
    def _decode(line, index):
        """Decode one Intel HEX record.

        Args:
            line: The record's text, with or without its line ending.
            index: Line number, for error messages.

        Returns:
            (byte count, 16-bit address, record type, payload), or None for a blank line.

        Raises:
            ImageError: The record is malformed or fails its checksum.
        """
        text = line.strip()  # drop the line ending and padding
        if not text:
            return None  # blank line
        try:
            raw = bytes.fromhex(text[1:])  # everything after the colon
        except ValueError:
            raw = b""  # not hex, rejected below
        if text[0] != ":" or len(raw) < 5 or len(raw) != raw[0] + 5 or sum(raw) & 0xFF:
            raise ImageError("line %d is not a valid Intel HEX record" % (index + 1))  # wrong start, length or checksum
        return raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:-1]  # count, address, type, payload

    def read(self, addr, size):
        """Read bytes at a flash address.

        Args:
            addr: Flash address of the first byte.
            size: Number of bytes.

        Returns:
            The bytes.

        Raises:
            ImageError: A byte in the range is not in any data record.
        """
        value = bytearray()  # bytes collected so far
        for a in range(addr, addr + size):
            if a not in self.cells:
                raise ImageError("the image holds no byte at 0x%08X" % a)  # address not covered by a data record
            index, i = self.cells[a]  # record and payload offset of this byte
            value.append(self._decode(self.lines[index], index)[3][i])  # current value, patches included
        return bytes(value)  # immutable copy

    def write(self, addr, value):
        """Overwrite bytes at a flash address and re-encode the records involved with fresh checksums.

        Args:
            addr: Flash address of the first byte; read() checked the range.
            value: Bytes to store.
        """
        for a, byte in zip(range(addr, addr + len(value)), value):
            index, i = self.cells[a]  # record and payload offset of this byte
            line = self.lines[index]  # current text of that record
            count, offset, rtype, payload = self._decode(line, index)  # its fields
            payload = bytearray(payload)  # editable copy
            payload[i] = byte  # the patched byte
            raw = bytes((count, offset >> 8, offset & 0xFF, rtype)) + bytes(payload)  # record without its checksum
            ending = line[len(line.rstrip("\r\n")):]  # keep the original line ending
            self.lines[index] = ":" + (raw + bytes(((-sum(raw)) & 0xFF,))).hex().upper() + ending  # re-encoded record

    def save(self, path):
        """Write the image to a file.

        Args:
            path: File to write.

        Raises:
            OSError: The file cannot be written.
        """
        with open(path, "w", newline="") as file:  # write the line endings as stored
            file.write("".join(self.lines))  # every record, patched or not

#& Command line


def parse_id(text):
    """Parse a 16-bit USB ID written in hex, with or without 0x.

    Args:
        text: The ID as typed, such as 0x1234 or 1234.

    Returns:
        The ID as an integer.

    Raises:
        argparse.ArgumentTypeError: The text is not a hex number from 0 to 0xFFFF.
    """
    try:
        value = int(text, 16)  # USB IDs are written in hex
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a hex number" % text)  # reported as a usage error
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("%r does not fit in 16 bits" % text)  # USB IDs are 16-bit
    return value  # valid ID


def load(path):
    """Open an image in the format its file extension names.

    Args:
        path: A .hex file is read as Intel HEX, anything else as a raw binary.

    Returns:
        A HexImage or a BinImage.

    Raises:
        OSError: The file cannot be read.
        ImageError: The Intel HEX file is malformed.
    """
    if path.lower().endswith((".hex", ".ihex")):
        return HexImage(path)  # Intel HEX
    return BinImage(path)  # raw binary from FLASH_BASE


def main(argv=None):
    """Show the IDs of an image, and set new ones when asked.

    Args:
        argv: Command-line arguments without the program name; None reads sys.argv.

    Returns:
        0 on success, 1 when the image cannot be read, has no descriptor at the fixed address, or cannot be written.

    Raises:
        SystemExit: argparse exits with status 2 on a usage error, such as an ID that does not fit in 16 bits.
    """
    parser = argparse.ArgumentParser(description="Show or set the USB vendor and product IDs in an XCAN firmware image (.bin or .hex).")
    parser.add_argument("image", help="firmware image, .bin or .hex")  # file to inspect or patch
    parser.add_argument("--vid", type=parse_id, help="vendor ID to set, in hex")  # new idVendor
    parser.add_argument("--pid", type=parse_id, help="product ID to set, in hex")  # new idProduct
    parser.add_argument("-o", "--output", help="write the patched image here instead of changing IMAGE")  # copy instead of in place
    args = parser.parse_args(argv)  # exits with a usage error on bad input

    try:
        #- Find the descriptor at its fixed address
        image = load(args.image)  # bin or hex, by extension
        if image.read(DESC_ADDR, len(DESC_HEADER)) != DESC_HEADER:
            raise ImageError("no USB device descriptor at 0x%08X; is this an image from before the IDs moved there?" % DESC_ADDR)  # wrong file
        vid = int.from_bytes(image.read(VID_ADDR, 2), "little")  # current idVendor
        pid = int.from_bytes(image.read(PID_ADDR, 2), "little")  # current idProduct
        print("%s: VID 0x%04X, PID 0x%04X" % (args.image, vid, pid))  # what the image holds now

        #- Set the requested IDs
        if args.vid is None and args.pid is None:
            return 0  # show only
        if args.vid is not None:
            image.write(VID_ADDR, args.vid.to_bytes(2, "little"))  # new idVendor
            vid = args.vid  # for the report
        if args.pid is not None:
            image.write(PID_ADDR, args.pid.to_bytes(2, "little"))  # new idProduct
            pid = args.pid  # for the report
        output = args.output or args.image  # in place unless -o names another file
        image.save(output)  # write the patched image
        print("%s: VID 0x%04X, PID 0x%04X written" % (output, vid, pid))  # what the new image holds
    except (OSError, ImageError) as error:
        print("usb_id: %s" % error, file=sys.stderr)  # say what failed
        return 1  # failure
    return 0  # success


if __name__ == "__main__":
    sys.exit(main())  # exit status for scripts and CI
