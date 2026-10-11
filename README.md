# XCAN firmware for the DykbRadio CANable V1.0

[![build](https://github.com/FireDeveloper/XCAN-CANable/actions/workflows/firmware_build.yml/badge.svg?branch=develop)](https://github.com/FireDeveloper/XCAN-CANable/actions/workflows/firmware_build.yml?query=branch%3Adevelop)

USB-to-CAN firmware for the DykbRadio "CANable V1.0" (STM32F072C8T6). It implements the CAN-USB protocol, so the board runs with the Windows CAN Driver and the Linux CAN Driver for that adapter family. It started as a fork of [moonglow/pcan_cantact](https://github.com/moonglow/pcan_cantact) and is now built around this one board: interrupt-driven CAN, transmission at the full bus rate, flow control in both directions, and complete bus state and error reporting.

## Highlights

- Sends and receives at the full CAN bus rate, up to 1 Mbit/s (see [Performance](#performance)).
- Bit timing exactly as the host sets it: bitrate, sample point and jump width (every 1 Mbit/s setting checked, see [Bit timing](#bit-timing)).
- Frames leave in the order the host queued them, while every transmit mailbox of the CAN controller stays busy.
- Transmit echo (self-reception) once the frame was acknowledged on the bus, stamped with the completion time.
- Bus state reporting: error active, warning, error passive and bus-off, with the receive and transmit error counters.
- Error frames: every bus error reaches the host with its type (bit, form, stuff, ACK, CRC), direction and frame segment.
- Bus-off holds until the host restarts the channel; the controller never rejoins the bus on its own.
- Single-shot transmission, listen-only mode and 42.67 µs timestamps.
- When USB falls behind, frames wait in a receive buffer and transmission with echo slows down to match, so echoes are not dropped.
- Serial number derived from the chip's unique ID; LED identify on request.
- USB vendor and product IDs at a fixed place in the image: set your own in a downloaded `.bin` or `.hex` with `utilities/usb_id.py`, no rebuild needed.

## Hardware

<img src="docs/dykbradio_canable_v1.0.png" alt="DykbRadio CANable V1.0, Pro and Nano variants, top side" width="400"> <img src="docs/dykbradio_canable_v1.0_bottom.png" alt="DykbRadio CANable V1.0, Pro and Nano variants, bottom side with the CAN terminal labels and the through-hole SWD header" width="400">

| Item | Value |
|---|---|
| Board | DykbRadio "CANable V1.0" |
| MCU | STM32F072C8T6, Cortex-M0 at 48 MHz, 64 KB flash, 16 KB RAM |
| Clock | Internal 48 MHz oscillator, trimmed from the USB start-of-frame packets by the clock recovery system (no crystal) |
| CAN | bxCAN, RX on PB8 and TX on PB9 (AF4) |
| TX LED | PA0, lit when high |
| RX LED | PA1, lit when high |
| USB | Full speed, device descriptor at flash address 0x08000100 (see [USB IDs](#usb-ids)) |

| Situation | TX LED | RX LED |
|---|---|---|
| Powered, no host driver yet | slow blink | slow blink |
| Driver loaded, channel closed | on | on |
| Channel open | off, flashes for each sent frame | off, flashes for each received frame |
| Identify request from the host | fast blink | fast blink |

### SWD header

The SWD header is a row of through-hole pads, reachable from either side of the board. Turning the board over mirrors the pin order, so go by the square pad, which is pin 1.

<img src="docs/swd_pinout.svg" alt="SWD header pinout: pin 1 Vcc (square pad), pin 2 SWDIO, pin 3 SWCLK, pin 4 GND" width="560">

| Pin | Pad | Signal |
|---|---|---|
| 1 | square | Vcc |
| 2 | round | SWDIO |
| 3 | round | SWCLK |
| 4 | round | GND |

## Performance

Measured between two of these boards on one bus with the Windows CAN Driver, echo frames enabled on both channels, at the standard bit timings 00h/14h, 00h/1Ch, 01h/1Ch and 03h/1Ch (BTR0/BTR1). Each run is about two seconds of back-to-back traffic per sender, at least 2,000 frames, and every received frame and every echo is checked against what was sent: identifier, length, data and order.

Load is the share of bus time the frames used, from each frame's exact length on the wire (stuff bits, CRC, acknowledge, end of frame and interframe space), so 100% means back to back with no idle bit. An 11-bit frame takes 111 to 135 bit times with 8 data bytes and 47 to 55 without, depending on its stuff bits. Loads assume the nominal bit rate, so the clock tolerance between the boards and the PC can show a full bus as 100.1%.

About 750,000 frames crossed the bus over these runs: none was lost, duplicated, reordered or corrupted, both channels stayed error active, and neither board reported an overrun.

### One sender

Frames per second and bus load from one board to the other, with the firmware as the STM32Cube extension builds it (ST's GCC 14.3.1).

| Frames | 1 Mbit/s | 500 kbit/s | 250 kbit/s | 125 kbit/s |
|---|---|---|---|---|
| 11-bit ID, 8 data bytes | 8,492/s, 100% | 4,246/s, 100.1% | 2,118/s, 100% | 1,057/s, 100.1% |
| 29-bit ID, 8 data bytes | 7,208/s, 100% | 3,603/s, 100.1% | 1,798/s, 100% | 898/s, 100.1% |
| 29-bit ID, no data | 14,205/s, 99.9% | 7,080/s, 100% | 3,524/s, 100% | 1,762/s, 100.1% |
| 11-bit ID, no data | 20,311/s, 99.8% | 10,164/s, 99.9% | 5,090/s, 100.1% | 2,545/s, 100.1% |

Every cell is a full bus, including the shortest frames with an echo for each: an 11-bit frame without data spends 49 µs on the wire, and the board handles it and its echo in less. When echoes back up because USB is busy, the board pauses sending rather than dropping them, so nothing is lost, and the receiving board keeps up with a full bus.

The bus never idles at 1 Mbit/s; the last 0.1-0.2% goes to CAN's own bit timing. The receivers drive each frame's acknowledge bit, their edge reaches the sender a round trip late, and resynchronization stretches that bit by one time quantum, 125 ns at 00h/14h, so every frame runs about 0.1 bit time long whether echo is on or off. With 01h/01h, which has 250 ns quanta at the same bit rate and sample point, the delay stays inside one quantum and the same frames fill 100% of the bus.

### Both boards sending

Frames per second and load of the whole bus, with both boards sending at once, measured with v1.0.0 on both boards.

| Frames | 1 Mbit/s | 500 kbit/s | 250 kbit/s | 125 kbit/s |
|---|---|---|---|---|
| 11-bit ID, 8 data bytes | 8,489/s, 99.9% | 4,243/s, 100% | 2,118/s, 100% | 1,057/s, 100.1% |
| 29-bit ID, 8 data bytes | 7,232/s, 99.9% | 3,614/s, 100% | 1,804/s, 100% | 901/s, 100.1% |
| 29-bit ID, no data | 14,207/s, 99.9% | 7,080/s, 100% | 3,524/s, 100% | 1,763/s, 100.1% |
| 11-bit ID, no data | 20,395/s, 99.9% | 10,204/s, 99.9% | 5,108/s, 100% | 2,556/s, 100.1% |

Each board sends back to back, so the one whose frames have the lower identifiers wins arbitration until its queue runs empty, and the other takes the remaining bus time, which is ordinary CAN arbitration.

### Error frame rates

| Error frames | Rate | Decoded as |
|---|---|---|
| Board sends at 500 kbit/s and no node acknowledges | 3,580/s | other (ACK), transmit, ACK slot |
| Another node sends at 250 kbit/s into a 500 kbit/s bus | 11,200/s | stuff and form errors, receive |

### Resources

| Resource | Size |
|---|---|
| Flash | 17,032 bytes of 65,536 (26%) |
| RAM, static | 10,880 bytes of 16,384 |
| Stack reserve | 2,048 bytes |
| Transmit queue | 128 frames |
| Receive buffer | 128 frames or echoes |
| USB record buffers | 2 × 896 bytes, 14 USB packets each: one fills while USB sends the other |

## CAN behavior

### Bit timing

The host sends SJA1000 bit timing (BTR0/BTR1) for an 8 MHz clock, and the firmware applies it as given on the 48 MHz CAN clock: prescaler, both time segments and the resynchronization jump width. The bitrate and the sample point are the ones the host picks, up to 1 Mbit/s. A timing whose first segment is a single time quantum runs with half-length quanta instead, keeping the bitrate, sample point and jump width, because bxCAN went bus-off with such timings on this board. bxCAN samples each bit once, so a triple-sampling setting (sample count 3) runs with single sampling.

Every 1 Mbit/s setting a host tool offers for an 8 MHz clock was checked on the board: the controller's timing register read back as expected, and 200 frames went each way against a second adapter set to 00h/14h.

| BTR0 | BTR1 | Time quantum | Sample point | SJW | Result |
|---|---|---|---|---|---|
| 00h | 05h | 125 ns | 87.5% | 1 | OK |
| 00h | 14h | 125 ns | 75% | 1 | OK |
| 00h | 23h | 125 ns | 62.5% | 1 | OK |
| 00h | 32h | 125 ns | 50% | 1 | OK |
| 01h | 01h | 250 ns | 75% | 1 | OK |
| 01h | 10h | 250 ns | 50% | 1 | OK, runs with 125 ns quanta |
| 40h | 14h | 125 ns | 75% | 2 | OK |
| 40h | 23h | 125 ns | 62.5% | 2 | OK |
| 40h | 32h | 125 ns | 50% | 2 | OK |
| 41h | 10h | 250 ns | 50% | 2 | OK, runs with 125 ns quanta |
| 80h | 23h | 125 ns | 62.5% | 3 | OK |
| 80h | 32h | 125 ns | 50% | 3 | OK |
| C0h | 32h | 125 ns | 50% | 4 | OK |

Sample count 3 (BTR1 94h) also works, with single sampling.

### Bus states

| Controller state | Error counters | Windows CAN Driver shows |
|---|---|---|
| Error active | both below 96 | OK |
| Warning | one at 96 or above | bus heavy |
| Error passive | one above 127 | bus heavy |
| Bus-off | transmit counter above 255 | bus off |

Only changes are reported, the way the original adapter reports its CAN controller's status interrupts. The controller keeps its error counters across a bus off/on, so a warning or passive state left from the previous session is reported as soon as the channel opens. With error reporting enabled, each change also carries both error counters; the Linux CAN Driver tells warning from error passive by them.

### Bus-off

The controller stays bus-off until the host restarts the channel (bus off then on). The restart runs the CAN bus-off recovery, 128 occurrences of 11 recessive bits, and the end of the recovery is reported as a state change.

### Error frames

When the host enables error reporting, every bus error is sent as an error frame with its type (bit, form, stuff or other), direction (transmit or receive), the frame segment where it is known (ACK slot for ACK errors, CRC sequence for CRC errors) and both error counters. Errors that pile up between two main-loop passes share one report.

### Echo

A frame sent with echo comes back to the host once the controller reports it acknowledged on the bus, stamped with the completion time. Echoes keep the order of the frames. A frame that never reached the bus is not echoed: one aborted at bus off, or a single-shot frame whose attempt failed.

### Single-shot and listen-only

A frame marked single-shot gets one attempt and is neither retransmitted nor echoed when it fails (the Linux CAN Driver's one-shot mode). Listen-only mode receives without acknowledging or transmitting; switching it while frames wait keeps them queued, and they go out once the mode is back to normal.

### Timestamps

Frames are stamped in the CAN interrupt with a 16-bit timer that ticks every 42.67 µs. The host extends it to its own clock through a time sync record sent once a second.

### Identity

The serial number is the XOR of the 32-bit words of the STM32's 96-bit unique ID, so it differs between boards. The device number set by the host is kept until power-off.

## How it works

### Data path

- The CAN interrupt does the time-critical work straight from the controller's registers: it empties the 3-frame receive FIFO into a 128-entry ring and stamps each frame, retires finished transmit mailboxes in the order they were loaded, queues their echoes, and refills free mailboxes from the 128-frame transmit queue. The HAL's interrupt handler runs only for aborts, failed attempts, FIFO overruns and errors.
- The main loop polls USB, packs received frames, echoes and status back to back into 64-byte USB packets, and decodes the host's frames into the transmit queue. It reads each frame in place in the ring and updates the LEDs once per pass.
- The controller sends its mailboxes in load order (transmit FIFO priority), so the host's order holds with several mailboxes in flight.

### Flow control

- The USB data endpoint pauses while the transmit queue has fewer than 20 free slots, the most frames one USB packet can carry, so the host's own queue fills instead of frames being dropped.
- USB sends one record buffer straight from memory while records fill the other, so a transfer needs no copy. Received frames stay in the ring only when the filling buffer is full and USB is still busy.
- Echoes travel through the same ring, so a frame that asks for an echo waits while the ring is half full; the other half stays free for frames from other nodes.

### Source files

| File | Role |
|---|---|
| `src/Src/main.c` | Clock setup and the main loop |
| `src/Src/pcan_can.c` | CAN controller: interrupt, receive ring, transmit queue and mailboxes, bus state, error events |
| `src/Src/pcan_protocol.c` | Protocol: host commands, USB record packing, bus state and error reports, timestamps in records |
| `src/Src/pcan_usb.c` | USB device class: endpoints, data flow control, vendor requests |
| `src/Src/pcan_led.c` | LED modes and blinking |
| `src/Src/pcan_timestamp.c` | 42.67 µs timer and millisecond time |
| `src/Src/pcan_varian.h` | Board pins |
| `src/Src/usbd_desc.c` | USB device descriptor and strings |
| `src/CMakeLists.txt` | Build: sources, compiler flags, output in `src/build/<preset>/` |
| `src/CMakePresets.json` | `Release` and `Debug` build configurations, Ninja |
| `src/gcc-arm-none-eabi.cmake` | Toolchain: the GNU Arm compiler found on the PATH |
| `src/STM32F072C8Tx_FLASH.ld` | Memory layout, including the fixed address of the USB device descriptor |
| `utilities/usb_id.py` | Shows or sets the USB IDs in a built image |

## Build

Needs the GNU Arm toolchain (`arm-none-eabi-gcc` on the PATH), CMake 3.22 or newer and Ninja. The CMake project lives in `src/`:

```
cd src
cmake --preset Release
cmake --build --preset Release
```

The firmware lands in `src/build/Release/xcan_canable.elf`, `.hex` and `.bin`. The `Debug` preset builds an image for stepping through in a debugger into `src/build/Debug/`.

In VS Code with the STM32Cube extension, open the `src/` folder itself, not the repository root, and run **STM32Cube: Set up STM32Cube projects** if the extension does not offer it. `src/.settings/` names the device and pins the extension's own CMake, Ninja and GCC, which it downloads on first use, and it builds, flashes and debugs through the same presets. Its GCC is ST's build, so its images differ slightly from the CI images, which use Ubuntu's `gcc-arm-none-eabi`.

Prebuilt images come with every GitHub Release as `xcan-canable-<tag>.bin`, `.hex` and `.elf`, together with `usb_id.py`; every push to `master` and `develop` also attaches them to its Actions run as the `xcan_canable` artifact.

## USB IDs

The firmware keeps its USB device descriptor at a fixed flash address, 0x08000100, so the vendor and product IDs sit at the same place in every build:

| Field | Flash address | Offset in the `.bin` |
|---|---|---|
| Vendor ID (idVendor) | 0x08000108 | 0x108 |
| Product ID (idProduct) | 0x0800010A | 0x10A |

Both are 16-bit little-endian. A build carries the default IDs, 0x0483 and 0x000C. Set the IDs your host driver expects in the image itself, without a rebuild:

```
python utilities/usb_id.py xcan_canable.bin                            # show the IDs
python utilities/usb_id.py xcan_canable.hex --vid 0x1234 --pid 0x5678  # set them in the image
python utilities/usb_id.py xcan_canable.bin --pid 0x5678 -o board.bin  # write a patched copy
```

The tool needs Python 3 and nothing else. It handles `.bin` and `.hex`, rewriting only the `.hex` records it changes with fresh checksums, and it refuses an image without a device descriptor at 0x08000100. Flash the patched image as below.

## Flash

With an ST-Link on the board's [SWD header](#swd-header) and STM32CubeProgrammer:

```
STM32_Programmer_CLI -c port=SWD mode=UR -w src/build/Release/xcan_canable.bin 0x08000000 -v -rst
```

## Using it

### Windows

Applications built on the Windows CAN Driver's API see a single-channel USB CAN adapter. Enable error frames in the application to receive bus errors, and echo frames to receive transmit echoes.

### Linux

The Linux CAN Driver exposes the board as a SocketCAN interface:

```
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
candump -td can0
cansend can0 123#DEADBEEF
```

`restart-ms` lets the host restart the channel after a bus-off; without it, `sudo ip link set can0 type can restart` does it by hand. `one-shot on` and `berr-reporting on` enable single-shot transmission and the error counters. The measurements above were made with the Windows CAN Driver; Linux support follows the same protocol but was not measured.

## Tests

`tests/` holds hardware tests for a Windows PC with the Windows CAN Driver and Python 3, without extra packages. The bench is two boards with this firmware on one terminated CAN bus, both on USB; the tests call them A and B in the order the driver numbers them. The tests that look inside the firmware also need board A on an ST-Link and STM32CubeProgrammer or the STM32Cube extension. They find the firmware's variables in `src/build/Release/xcan_canable.elf`, or the file `XCAN_ELF` names, so board A must run that image.

```
python tests/throughput.py
```

| Test | What it checks |
|---|---|
| `throughput.py` | Every bit rate and frame layout, each direction and both at once, with echo: every frame and echo checked, bus load from each frame's length (the Performance tables) |
| `latency.py` | Lone frames and their echoes arrive unchanged within 20 ms |
| `tx_paths.py` | Frames stuck at bus off never reach a later session; frames queued across a listen-only switch all arrive in order |
| `bus_on_state.py` | An error state left from the last session shows at bus-on and clears once frames get acknowledged |
| `bus_errors.py` | Bus errors arrive as error frames with type, direction and segment, while sending and while receiving |
| `bit_timing.py` | Every 1 Mbit/s setting the host offers exchanges frames, and the controller's CAN_BTR holds the expected value |
| `bus_off.py` | Interactive: a CANH-CANL short sends board A bus-off, and it stays off until the channel restarts |
| `frame_timing.py` | Report: where bus time goes when board A sends back to back, idle gaps against a stretch every frame shares |
| `cpu_profile.py` | Report: board A's CPU time per function while it sends, from program counter samples read over SWD |

Each test prints PASS or FAIL and exits with 0 or 1; the ones marked Report always exit with 0.

## Changes from upstream

- Retargeted to the DykbRadio CANable V1.0 (STM32F072C8T6) alone; the other boards and the STM32F042 build were removed.
- Bus states: warning and error passive are reported with the error counters, only real state changes are sent, a state carried over from the last session shows at connect, and bus-off holds until the host restarts the channel.
- Error frames for every bus error, with type, direction and segment.
- Interrupt-driven receive and transmit, every transmit mailbox in use in load order, full bus rate in both directions.
- Per-frame work trimmed: the interrupt reads and loads the controller registers itself, frames reach USB without copies, records are packed back to back, and the LEDs update once per main-loop pass, so even echoed frames without data keep the bus full.
- USB flow control replaces a blocking wait of up to a second per frame; receive back-pressure keeps echoes from being dropped.
- Echoes are sent when the frame completes on the bus; single-shot frames are honoured.
- Fixed record corruption (DLC 9 to 15, records on a packet boundary), frames from an old session going out after bus off/on, RTR handling, the USB control path, and IN endpoint recovery after a reset.
- Timestamp fixes: the timer prescaler applies from the start, records keep decodable 1-byte timestamps, and the time sync record opens its own packet.
- The vendored HAL's mailbox abort clears pending completion flags, so the firmware aborts with a single register write instead.
- Bit timings with a 1-quantum first segment run with half-length quanta, so every 1 Mbit/s setting a host offers works.
- Serial number from the chip's unique ID; LED identify.
- Includes USB and buffer fixes from [awto-au/pcan_cantact](https://github.com/awto-au/pcan_cantact).

## License

MIT, see [LICENSE](LICENSE). The vendored code under `src/Drivers/` and `src/Middlewares/`, and the STMicroelectronics startup, system and linker files, keep their own licenses.
