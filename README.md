# XCAN firmware for the DykbRadio CANable V1.0

[![build](https://github.com/FireDeveloper/XCAN-CANable/actions/workflows/firmware_build.yml/badge.svg?branch=develop)](https://github.com/FireDeveloper/XCAN-CANable/actions/workflows/firmware_build.yml?query=branch%3Adevelop)

USB-to-CAN firmware for the DykbRadio "CANable V1.0" (STM32F072C8T6). It implements the CAN-USB protocol, so the board runs with the Windows CAN Driver and the Linux CAN Driver for that adapter family.

It started as a fork of [moonglow/pcan_cantact](https://github.com/moonglow/pcan_cantact) and is now built around this one board: interrupt-driven CAN, transmission at the full bus rate, flow control in both directions, and complete bus state and error reporting.

## Highlights

- Sends and receives at the full CAN bus rate, up to 1 Mbit/s (see [Performance](#performance)).
- Bit timing exactly as the host sets it: prescaler, time segments and jump width (every 1 Mbit/s setting a host offers checked, see [Bit timing](#bit-timing)).
- Frames leave in the order the host queued them, while every transmit mailbox of the CAN controller stays busy.
- Transmit echo (self-reception) once the frame was acknowledged on the bus, stamped with the completion time.
- Bus state reporting: error active, warning, error passive and bus-off, with the receive and transmit error counters.
- Error frames: every bus error reaches the host as its own error frame, with its type (bit, form, stuff, ACK, CRC), direction, frame segment and both error counters, also at the 57,000 errors a second a node on the wrong bit rate causes on a 1 Mbit/s bus.
- Bus-off holds until the host restarts the channel; the controller never rejoins the bus on its own.
- Single-shot transmission, listen-only mode and 42.67 µs timestamps.
- When USB falls behind, frames wait in a receive buffer and transmission with echo slows down to match, so echoes are not dropped.
- Serial number derived from the chip's unique ID; LED identify on request.
- USB vendor and product IDs at a fixed place in the image: set your own in a downloaded `.bin` or `.hex` with `utilities/usb_id.py`, no rebuild needed.

## Compared with the factory firmware

The board comes with a build of the upstream project made for the STM32F042, the smaller chip with 6 KB of RAM; both boards bought for this project carried the same image, read out over SWD. It works with the same host drivers.

Both firmwares ran the [tests](#tests) on the same board, against a second board with XCAN v1.1.0, each test from a fresh reset.

✅ yes · ⚠️ partly · ❌ no

| Feature | Factory firmware | XCAN | Test |
|---|:---:|:---:|---|
| **Traffic** | | | |
| Lone frame latency, median | ✅ 1.3 to 1.4 ms | ✅ 1.3 ms | [`latency.py`](tests/latency.py) |
| Bus load, Tx Only | ❌ down to 77% | ✅ 99.8% or more | [`throughput.py`](tests/throughput.py) |
| Frames lost, Rx Only | ⚠️ 1,333 of 187,397 | ✅ 0 of 187,397 | [`throughput.py`](tests/throughput.py) |
| Frames lost, RxTx | ❌ 81,683 of 187,397, unreported | ✅ 0 of 187,397 | [`throughput.py`](tests/throughput.py) |
| Echoes lost, Tx Only and RxTx | ❌ 96,369 of 374,794 | ✅ 0 of 374,794 | [`throughput.py`](tests/throughput.py) |
| Unacknowledged frames echoed | ❌ 30 of 30 | ✅ 0 of 30 | [`tx_paths.py`](tests/tx_paths.py) |
| Stale frames sent after a reconnect | ❌ 29 of 30 | ✅ 0 of 30 | [`tx_paths.py`](tests/tx_paths.py) |
| Frames kept across a listen-only switch | ❌ 2 of 60 | ✅ 60 of 60, in order | [`tx_paths.py`](tests/tx_paths.py) |
| **Bus state and errors** | | | |
| Warning and error passive | ❌ host shows OK at TEC 128 | ✅ shown at TEC 128, also after a reconnect | [`bus_on_state.py`](tests/bus_on_state.py) |
| Error frames with type, direction and segment | ❌ 0 per second | ✅ every one, up to 57,302/s at 1 Mbit/s | [`bus_errors.py`](tests/bus_errors.py) |
| Bus-off held until the host restarts | ❌ hidden, or cleared without a restart | ✅ held, and back within 0.1 s of the restart | [`bus_off.py`](tests/bus_off.py) |
| **Settings** | | | |
| 1 Mbit/s bit timings the host offers | ⚠️ all but 01h/10h and 41h/10h | ⚠️ all but 01h/10h and 41h/10h | [`bit_timing.py`](tests/bit_timing.py) |
| Single-shot frames (Linux) | ❌ retried | ✅ one attempt | upstream code |
| Serial number | ❌ FFFFFFFFh on every board | ✅ from the chip's 96-bit unique ID | upstream code |
| LED identify | ❌ ignored | ✅ fast blink on request | upstream code |
| **Setup** | | | |
| Works with the Windows and Linux CAN Drivers | ✅ | ✅ | |
| Setting the USB IDs your driver expects | ❌ recompile the source | ✅ `usb_id.py` on the image, no rebuild | |
| Built for this chip | ❌ STM32F042 build, 6 KB of RAM | ✅ STM32F072 build, 128-frame queues in 16 KB | |

### Throughput by bit rate

Echo on, in the traffic modes and over the frame layouts of the [Performance](#performance) tables: 11- and 29-bit identifiers, 8 data bytes or none.

| | 1 Mbit/s | 500 kbit/s | 250 kbit/s | 125 kbit/s |
|---|:---:|:---:|:---:|:---:|
| **Bus load, Tx Only** | | | | |
| Factory firmware | ❌ 77 to 94% | ⚠️ 96 to 99% | ✅ 99.2 to 99.8% | ✅ 100% |
| XCAN | ✅ 99.8 to 100% | ✅ 99.9 to 100% | ✅ 100% | ✅ 100% |
| **Frames lost, Rx Only** | | | | |
| Factory firmware | ⚠️ up to 3% | ✅ 0% | ✅ 0% | ✅ 0% |
| XCAN | ✅ 0% | ✅ 0% | ✅ 0% | ✅ 0% |
| **Frames lost, RxTx** | | | | |
| Factory firmware | ❌ 41 to 72% | ❌ 25 to 60% | ❌ 8 to 60% | ⚠️ up to 12% |
| XCAN | ✅ 0% | ✅ 0% | ✅ 0% | ✅ 0% |
| **Echoes lost, Tx Only** | | | | |
| Factory firmware | ❌ up to 14% | ❌ up to 12% | ❌ up to 13% | ❌ up to 14% |
| XCAN | ✅ 0% | ✅ 0% | ✅ 0% | ✅ 0% |
| **Echoes lost, RxTx** | | | | |
| Factory firmware | ❌ 41 to 48% | ❌ 37 to 60% | ❌ 20 to 60% | ❌ up to 23% |
| XCAN | ✅ 0% | ✅ 0% | ✅ 0% | ✅ 0% |

The code changes behind these differences are listed under [Changes from upstream](#changes-from-upstream).

To switch, follow [Flash](#flash), which also saves the factory image so you can go back.

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

The traffic modes are named from board A, the board under test: in **Tx Only** it sends to board B, in **Rx Only** board B sends to it, and in **RxTx** both send at once.

Load is the share of bus time the frames used, from each frame's exact length on the wire (stuff bits, CRC, acknowledge, end of frame and interframe space), so 100% means back to back with no idle bit. An 11-bit frame takes 111 to 135 bit times with 8 data bytes and 47 to 55 without, depending on its stuff bits.

Loads assume the nominal bit rate, so the clock tolerance between the boards and the PC can show a full bus as 100.1%.

About 750,000 frames crossed the bus over these runs: none was lost, duplicated, reordered or corrupted, both channels stayed error active, and neither board reported an overrun.

### Tx Only

Frames per second and bus load from board A to board B, with the firmware as the STM32Cube extension builds it (ST's GCC 14.3.1).

| Frames | 1 Mbit/s | 500 kbit/s | 250 kbit/s | 125 kbit/s |
|---|---|---|---|---|
| 11-bit ID, 8 data bytes | 8,488/s, 99.9% | 4,240/s, 99.9% | 2,118/s, 100% | 1,057/s, 100.1% |
| 29-bit ID, 8 data bytes | 7,205/s, 99.9% | 3,600/s, 100% | 1,798/s, 100.1% | 898/s, 100.1% |
| 29-bit ID, no data | 14,200/s, 99.8% | 7,080/s, 100% | 3,524/s, 100% | 1,762/s, 100.1% |
| 11-bit ID, no data | 20,311/s, 99.8% | 10,167/s, 99.9% | 5,089/s, 100.1% | 2,546/s, 100.1% |

Every cell is a full bus, including the shortest frames with an echo for each: an 11-bit frame without data spends 49 µs on the wire, and the board handles it and its echo in less. When echoes back up because USB is busy, the board pauses sending rather than dropping them, so nothing is lost, and the receiving board keeps up with a full bus.

The bus never idles at 1 Mbit/s; the last 0.1-0.2% goes to CAN's own bit timing. The receivers drive each frame's acknowledge bit, their edge reaches the sender a round trip late, and resynchronization stretches that bit by one time quantum, 125 ns at 00h/14h, so every frame runs about 0.1 bit time long whether echo is on or off.

With 01h/01h, which has 250 ns quanta at the same bit rate and sample point, the delay stays inside one quantum and the same frames fill 100% of the bus.

### Rx Only

Frames per second and bus load from board B to board A, on board A's timestamps.

| Frames | 1 Mbit/s | 500 kbit/s | 250 kbit/s | 125 kbit/s |
|---|---|---|---|---|
| 11-bit ID, 8 data bytes | 8,489/s, 99.9% | 4,244/s, 100% | 2,117/s, 100% | 1,057/s, 100.1% |
| 29-bit ID, 8 data bytes | 7,259/s, 99.9% | 3,625/s, 99.9% | 1,810/s, 100% | 904/s, 100.1% |
| 29-bit ID, no data | 14,207/s, 99.9% | 7,078/s, 99.9% | 3,525/s, 100.1% | 1,763/s, 100.1% |
| 11-bit ID, no data | 20,449/s, 99.8% | 10,237/s, 99.9% | 5,127/s, 100.1% | 2,565/s, 100.1% |

Board A takes every frame of a full bus and passes it on to the host, the shortest frames included.

### RxTx

Frames per second and load of the whole bus as board A saw it, and how it split: Tx is board A's own frames, Rx the frames it received from board B.

| Frames | 1 Mbit/s | 500 kbit/s | 250 kbit/s | 125 kbit/s |
|---|---|---|---|---|
| 11-bit ID, 8 data bytes | 8,491/s, 99.9%<br>Tx 4,245 + Rx 4,246 | 4,242/s, 100%<br>Tx 2,121 + Rx 2,121 | 2,118/s, 100%<br>Tx 1,059 + Rx 1,059 | 1,057/s, 100.1%<br>Tx 529 + Rx 529 |
| 29-bit ID, 8 data bytes | 7,234/s, 100%<br>Tx 3,617 + Rx 3,617 | 3,613/s, 100%<br>Tx 1,806 + Rx 1,807 | 1,804/s, 100%<br>Tx 902 + Rx 902 | 901/s, 100.1%<br>Tx 450 + Rx 451 |
| 29-bit ID, no data | 14,203/s, 99.9%<br>Tx 7,102 + Rx 7,102 | 7,078/s, 99.9%<br>Tx 3,539 + Rx 3,539 | 3,524/s, 100.1%<br>Tx 1,762 + Rx 1,762 | 1,763/s, 100.1%<br>Tx 881 + Rx 881 |
| 11-bit ID, no data | 20,383/s, 99.8%<br>Tx 10,191 + Rx 10,191 | 10,201/s, 99.9%<br>Tx 5,101 + Rx 5,101 | 5,109/s, 100.1%<br>Tx 2,554 + Rx 2,554 | 2,556/s, 100.1%<br>Tx 1,278 + Rx 1,278 |

Both boards send the same number of frames back to back, so each takes half of the bus over a run; arbitration only decides whose frame goes next, the lower identifier first. Board A sends at half the bus rate, with an echo for each frame, while it receives the other half, and loses nothing either way.

### Error frame rates

| Traffic | Error frames | Bit rate | Per second | Bus errors reported | Decoded as |
|---|---|---|---|---|---|
| Tx Only | Board A sends and no node acknowledges | 500 kbit/s | 3,554 | all | other (ACK), transmit, ACK slot |
| Tx Only | Board A sends and no node acknowledges | 1 Mbit/s | 7,168 | all | other (ACK), transmit, ACK slot |
| Rx Only | Board B sends at half the bit rate | 500 kbit/s | 28,578 | all | stuff and form errors, receive |
| Rx Only | Board B sends at half the bit rate | 1 Mbit/s | 57,302 | all | stuff and form errors, receive |

Every bus error the controller detects reaches the host as its own error frame. `tests/bus_errors.py` counts the errors in the firmware over SWD and the error frames at the host: in Rx Only at 1 Mbit/s the controller saw 37,856 bus errors and the host got 37,856 error frames, and a longer storm of 370,784 bus errors gave 370,784 error frames, each carrying the error counters as they were at its error. Bus state changes add a few error frames of their own.

Sending, the rate is the limit CAN itself sets: an error-passive transmitter spends 139 bit times per attempt on this frame (the stuffed frame up to the ACK slot, a passive error flag, the error delimiter, the intermission and the suspend time), 3,597 attempts per second at 500 kbit/s and 7,194 at 1 Mbit/s.

### Resources

| Resource | Size |
|---|---|
| Flash | 17,364 bytes of 65,536 (26%) |
| RAM, static | 11,152 bytes of 16,384 |
| Stack reserve | 2,048 bytes |
| Transmit queue | 128 frames |
| Receive buffer | 128 frames or echoes |
| USB record buffers | 2 × 896 bytes, 14 USB packets each: one fills while USB sends the other |
| Bus error queue | 64 errors |

## CAN behavior

### Bit timing

The host sends SJA1000 bit timing (BTR0/BTR1) for an 8 MHz clock, and the firmware applies it as given on the 48 MHz CAN clock: prescaler, both time segments and the resynchronization jump width. The bitrate and the sample point are the ones the host picks, up to 1 Mbit/s.

01h/10h and 41h/10h give the first segment a single time quantum, below what the CAN bit timing rules allow, and bxCAN on this board passes no frames with them and goes error passive or bus-off, even against a board on the same setting; 01h/01h, also 4 quanta per bit but with 2 in the first segment, works. Pick 40h/32h instead of 01h/10h and C0h/32h instead of 41h/10h: they give the same bitrate, sample point and jump width in 125 ns quanta.

bxCAN samples each bit once, so a triple-sampling setting (sample count 3) runs with single sampling.

Every 1 Mbit/s setting a host tool offers for an 8 MHz clock was checked on the board: the controller's timing register read back as the host gave it, and 200 frames went each way against a second adapter set to 00h/14h, except with the two settings above.

| BTR0 | BTR1 | Time quantum | Sample point | SJW | Result |
|---|---|---|---|---|---|
| 00h | 05h | 125 ns | 87.5% | 1 | OK |
| 00h | 14h | 125 ns | 75% | 1 | OK |
| 00h | 23h | 125 ns | 62.5% | 1 | OK |
| 00h | 32h | 125 ns | 50% | 1 | OK |
| 01h | 01h | 250 ns | 75% | 1 | OK |
| 01h | 10h | 250 ns | 50% | 1 | Fails, use 40h/32h |
| 40h | 14h | 125 ns | 75% | 2 | OK |
| 40h | 23h | 125 ns | 62.5% | 2 | OK |
| 40h | 32h | 125 ns | 50% | 2 | OK |
| 41h | 10h | 250 ns | 50% | 2 | Fails, use C0h/32h |
| 80h | 23h | 125 ns | 62.5% | 3 | OK |
| 80h | 32h | 125 ns | 50% | 3 | OK |
| C0h | 32h | 125 ns | 50% | 4 | OK |
| 00h | 94h | 125 ns | 75% | 1 | Works, sampled once: bxCAN has no triple sampling |

### Bit rates

`tests/bit_rates.py` runs every bit rate a host tool offers on both boards, with 20 to 100 frames each way, and the controller's timing register reads back as the host gave it at every rate. The timing codes are the Windows CAN Driver's standard ones; it has none for 200 kbit/s, so both common choices were tried.

| Bit rate | BTR0/BTR1 | Sample point | Result |
|---|---|---|---|
| 1 Mbit/s | 00h/14h | 75% | OK |
| 800 kbit/s | 00h/16h | 80% | OK |
| 500 kbit/s | 00h/1Ch | 87.5% | OK |
| 250 kbit/s | 01h/1Ch | 87.5% | OK |
| 200 kbit/s | 01h/2Fh, 41h/2Fh | 85% | OK |
| 125 kbit/s | 03h/1Ch | 87.5% | OK |
| 100 kbit/s | 43h/2Fh | 85% | OK |
| 95.238 kbit/s | C3h/4Eh | 76.2% | OK |
| 83.333 kbit/s | 85h/2Bh | 81.3% | OK |
| 50 kbit/s | 47h/2Fh | 85% | OK |
| 47.619 kbit/s | 14h/14h | 75% | OK |
| 33.333 kbit/s | 8Bh/2Fh | 85% | OK |
| 20 kbit/s | 53h/2Fh | 85% | OK |
| 10 kbit/s | 67h/2Fh | 85% | Fails, below the transceiver's limit |
| 5 kbit/s | 7Fh/7Fh | 68% | Fails, below the transceiver's limit |

Below about 12 kbit/s the board's CAN transceiver stops sending. It releases the bus once it has been held dominant for longer than its time-out, a guard against a stuck transmitter, and CAN allows five dominant bits in a row: they last 400 µs at 12.5 kbit/s (5Fh/2Fh), where frames pass, and 450 µs at 11.1 kbit/s (63h/2Fh), where the board reads back a recessive bus while it sends a dominant bit and goes bus-off. The firmware programs these rates as given; the limit is the transceiver's.

### Bus states

| Controller state | Error counters | Windows CAN Driver shows |
|---|---|---|
| Error active | both below 96 | OK |
| Warning | one at 96 or above | bus heavy |
| Error passive | one above 127 | bus heavy |
| Bus-off | transmit counter above 255 | bus off |

Only changes are reported, the way the original adapter reports its CAN controller's status interrupts. The controller keeps its error counters across a bus off/on, so a warning or passive state left from the previous session is reported as soon as the channel opens.

With error reporting enabled, each change also carries both error counters; the Linux CAN Driver tells warning from error passive by them.

### Bus-off

The controller stays bus-off until the host restarts the channel, the way the original adapter's SJA1000 controller stays in reset mode until its host clears it. The restart runs the CAN bus-off recovery, 128 occurrences of 11 recessive bits, and the end of the recovery is reported as a state change.

The firmware takes either restart the host drivers send: bus off then on, or bus on alone, which is what the Linux CAN Driver sends for `restart-ms` and `ip link set can0 type can restart`.

On Windows the channel restarts when the application reopens it, or by itself when the application turns on the driver's automatic bus-off reset: in `tests/bus_off.py autoreset` the driver restarted the board after every bus-off during the short, and frames got through again within 0.1 s of the last bus-off report once the short was gone.

### Error frames

When the host enables error reporting, every bus error is sent as an error frame with its type (bit, form, stuff or other), direction (transmit or receive), the frame segment where it is known (ACK slot for ACK errors, CRC sequence for CRC errors) and both error counters as they were at that error. Each bus error gets its own error frame (see [Error frame rates](#error-frame-rates)).

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

- The CAN interrupt does the time-critical work straight from the controller's registers: it empties the 3-frame receive FIFO into a 128-entry ring and stamps each frame, retires finished transmit mailboxes in the order they were loaded, queues their echoes, refills free mailboxes from the 128-frame transmit queue, and queues every bus error with its type, direction and both error counters. The HAL's interrupt handler runs only for aborts, failed attempts and FIFO overruns.
- The main loop polls USB, packs received frames, echoes, bus errors and status back to back into 64-byte USB packets, and decodes the host's frames into the transmit queue. It reads each frame in place in the ring and updates the LEDs once per pass.
- The controller sends its mailboxes in load order (transmit FIFO priority), so the host's order holds with several mailboxes in flight.

### Flow control

- The USB data endpoint pauses while the transmit queue has fewer than 20 free slots, the most frames one USB packet can carry, so the host's own queue fills instead of frames being dropped.
- USB sends one record buffer straight from memory while records fill the other, so a transfer needs no copy. Received frames stay in the ring only when the filling buffer is full and USB is still busy.
- Echoes travel through the same ring, so a frame that asks for an echo waits while the ring is half full; the other half stays free for frames from other nodes.
- Bus errors wait in their own 64-entry queue while the record buffer is full and USB still busy, so none is merged or dropped; an error that finds the queue full is counted as lost.

### Source files

| File | Role |
|---|---|
| `src/Src/main.c` | Clock setup and the main loop |
| `src/Src/pcan_can.c` | CAN controller: interrupt, receive ring, transmit queue and mailboxes, bus error queue, bus state |
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

In VS Code with the STM32Cube extension, open the `src/` folder itself, not the repository root, and run **STM32Cube: Set up STM32Cube projects** if the extension does not offer it. `src/.settings/` names the device and pins the extension's own CMake, Ninja and GCC, which it downloads on first use, and it builds, flashes and debugs through the same presets.

Its GCC is ST's build, so its images differ slightly from the CI images, which use Ubuntu's `gcc-arm-none-eabi`.

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

You need:

- an ST-Link with SWD, any version
- STM32CubeProgrammer, or the STM32Cube extension for VS Code, which brings its own `STM32_Programmer_CLI`
- the XCAN image: `xcan-canable-<tag>.bin` from a GitHub Release, or your own build (see [Build](#build))
- Python 3, for `utilities/usb_id.py`

1. **Wire the ST-Link to the [SWD header](#swd-header).** Connect SWDIO to pin 2, SWCLK to pin 3 and GND to pin 4. Connect pin 1 (Vcc) to the ST-Link's target voltage input (T_VCC), which only senses the board's 3.3 V; leave any 3.3 V output of the ST-Link unconnected. Plug the board into USB, which powers it.

2. **Check the connection.** The programmer should report device ID 0x448 (STM32F07x) and a voltage around 3.3 V:

   ```
   STM32_Programmer_CLI -c port=SWD mode=HOTPLUG
   ```

3. **Save the factory image**, so you can go back to it later:

   ```
   STM32_Programmer_CLI -c port=SWD mode=HOTPLUG -u 0x08000000 0x10000 factory.bin
   ```

4. **Note the board's USB IDs** while it still runs the factory firmware: in Device Manager, the adapter's Properties, Details, Hardware Ids (`VID_xxxx&PID_yyyy`), or `lsusb` on Linux.

5. **Put those IDs into the XCAN image** (see [USB IDs](#usb-ids)):

   ```
   python utilities/usb_id.py xcan-canable-<tag>.bin --vid 0x1234 --pid 0x5678 -o xcan.bin
   ```

6. **Flash it.** `-v` reads the image back and compares it, and `-rst` starts the new firmware:

   ```
   STM32_Programmer_CLI -c port=SWD mode=UR -w xcan.bin 0x08000000 -v -rst
   ```

7. **Check the board.** It leaves USB and comes back with the new firmware; both LEDs blink slowly until the host driver takes it, then stay on (see [Hardware](#hardware)). A virtual machine that captures USB devices, such as VMware, may first ask whether the board goes to the host or to the VM.

To go back to the factory firmware, flash `factory.bin` the same way as in step 6.

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

`restart-ms` lets the host restart the channel after a bus-off; without it, `sudo ip link set can0 type can restart` does it by hand. `one-shot on` and `berr-reporting on` enable single-shot transmission and the error counters.

The measurements above were made with the Windows CAN Driver; Linux support follows the same protocol but was not measured.

## Tests

`tests/` holds hardware tests for a Windows PC with the Windows CAN Driver and Python 3, without extra packages. The bench is two boards with this firmware on one terminated CAN bus, both on USB; the tests call them A and B in the order the driver numbers them.

The tests that look inside the firmware also need board A on an ST-Link and STM32CubeProgrammer or the STM32Cube extension. They find the firmware's variables in `src/build/Release/xcan_canable.elf`, or the file `XCAN_ELF` names, so board A must run that image.

```
python tests/throughput.py
```

| Test | What it checks |
|---|---|
| `throughput.py` | Every bit rate and frame layout in Tx Only, Rx Only and RxTx, with echo: every frame and echo checked, bus load from each frame's length (the Performance tables) |
| `latency.py` | Lone frames in Rx Only and Tx Only, and their echoes, arrive unchanged within 20 ms |
| `tx_paths.py` | Frames stuck at bus off are never echoed and never reach a later session; frames queued across a listen-only switch all arrive in order |
| `bus_on_state.py` | An error state left from the last session shows at bus-on and clears once frames get acknowledged |
| `bus_errors.py` | At 500 kbit/s and 1 Mbit/s, in Tx Only and Rx Only, bus errors arrive as error frames with type, direction and segment; with the probe, one error frame for every bus error the controller saw |
| `bit_timing.py` | Every 1 Mbit/s setting the host offers lands in the controller's CAN_BTR as given, and every one bxCAN can run exchanges frames |
| `bit_rates.py` | Every bit rate the host tools offer, on both boards: CAN_BTR as given, and every frame both ways down to the transceiver's limit of about 12 kbit/s |
| `bus_off.py` | Interactive: a CANH-CANL short sends board A bus-off, and it stays off until the channel restarts; with `autoreset`, the driver's automatic bus-off reset brings it back |
| `frame_timing.py` | Report: where bus time goes in Tx Only, idle gaps against a stretch every frame shares |
| `cpu_profile.py` | Report: board A's CPU time per function in Tx Only, from program counter samples read over SWD |

Each test prints PASS or FAIL and exits with 0 or 1; the ones marked Report always exit with 0. A channel whose reads keep failing, such as a board that left USB, stops the test with an error instead of leaving it hanging.

Close every other program that uses the boards, such as a CAN viewer, before a test. While one program has a channel open, the driver lets another open it only at the same bit rate, so a test that changes the bit rate fails to open the channel or never reaches the board.

A board leaves USB and comes back whenever it is flashed or reset. A virtual machine that captures USB devices, such as VMware, may then hold it until you pick the host in its prompt, and until then the Windows CAN Driver does not see the board.

## Changes from upstream

- Retargeted to the DykbRadio CANable V1.0 (STM32F072C8T6) alone; the other boards and the STM32F042 build were removed.
- Bus states: warning and error passive are reported with the error counters, only real state changes are sent, a state carried over from the last session shows at connect, and bus-off holds until the host restarts the channel.
- An error frame for every bus error, with type, direction, segment and both error counters, up to 57,000 a second at 1 Mbit/s.
- Interrupt-driven receive and transmit, every transmit mailbox in use in load order, full bus rate in both directions.
- Per-frame work trimmed: the interrupt reads and loads the controller registers itself, frames reach USB without copies, records are packed back to back, and the LEDs update once per main-loop pass, so even echoed frames without data keep the bus full.
- USB flow control replaces a blocking wait of up to a second per frame; receive back-pressure keeps echoes from being dropped.
- Echoes are sent when the frame completes on the bus; single-shot frames are honoured.
- Fixed record corruption (DLC 9 to 15, records on a packet boundary), frames from an old session going out after bus off/on, RTR handling, the USB control path, and IN endpoint recovery after a reset.
- Timestamp fixes: the timer prescaler applies from the start, records keep decodable 1-byte timestamps, and the time sync record opens its own packet.
- The vendored HAL's mailbox abort clears pending completion flags, so the firmware aborts with a single register write instead.
- Serial number from the chip's unique ID; LED identify.
- Includes USB and buffer fixes from [awto-au/pcan_cantact](https://github.com/awto-au/pcan_cantact).

## License

MIT, see [LICENSE](LICENSE). The vendored code under `src/Drivers/` and `src/Middlewares/`, and the STMicroelectronics startup, system and linker files, keep their own licenses.
