/**
 * @file pcan_varian.h
 * @brief Pin map of the DykbRadio CANable V1.0 (STM32F072C8T6).
 */
#pragma once

#include "io_macro.h"

#define IOPIN_TX    A, 0, MODE_OUTPUT_PP, NOPULL, SPEED_FREQ_MEDIUM, NOAF // TX LED, lit when high
#define IOPIN_RX    A, 1, MODE_OUTPUT_PP, NOPULL, SPEED_FREQ_MEDIUM, NOAF // RX LED, lit when high
#define LED_ON      PIN_HI // drive a LED pin high to light it
#define LED_OFF     PIN_LOW // drive a LED pin low to turn it off

#define CAN_RX      B, 8, MODE_AF_PP, NOPULL, SPEED_FREQ_HIGH, AF4_CAN // bxCAN RX on PB8
#define CAN_TX      B, 9, MODE_AF_PP, NOPULL, SPEED_FREQ_HIGH, AF4_CAN // bxCAN TX on PB9

#define pcan_variant_io_init() // no board pins beyond the LEDs and CAN
