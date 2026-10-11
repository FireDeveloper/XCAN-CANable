## XCAN firmware for the DykbRadio CANable V1.0

![build workflow](https://github.com/FireDeveloper/pcan_cantact/actions/workflows/firmware_build.yml/badge.svg)

PCAN-USB protocol firmware, forked from [moonglow/pcan_cantact](https://github.com/moonglow/pcan_cantact) and trimmed to the DykbRadio "CANable V1.0".

## Target Hardware

DykbRadio "CANable V1.0" with an STM32F072C8T6 (64 KB flash, 16 KB RAM, no crystal).

| Function | Pin |
|---|---|
| CAN RX | PB8, AF4 |
| CAN TX | PB9, AF4 |
| TX LED | PA0, lit when high |
| RX LED | PA1, lit when high |

The system clock is the internal 48 MHz oscillator, trimmed by the clock recovery system (CRS) from the USB start-of-frame packets.

## Build

Needs the GNU Arm Embedded Toolchain (`arm-none-eabi-gcc`) and GNU make.

```
make
```

The firmware lands in `build/pcan_canable.elf`, `.hex` and `.bin`; `make OPT=-Og DEBUG=1` builds a debug image.

## Flash

With an ST-Link on the SWD pads and STM32CubeProgrammer:

```
STM32_Programmer_CLI -c port=SWD mode=UR -w build/pcan_canable.bin 0x08000000 -v -rst
```

License
----

WTFPL
