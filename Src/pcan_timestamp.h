#pragma once
#include <stdint.h>

#define PCAN_TICKS_FROM_US( _us ) (((uint32_t)(_us)*1000u)/42666u)

/**
 * @brief Start TIM3 as the 16-bit protocol tick counter, one tick per 42.666 us.
 * @note The prescaler is loaded at once, so the first ticks already have the 42.666 us period.
 */
void pcan_timestamp_init( void );
uint16_t pcan_timestamp_ticks( void );
uint16_t pcan_timestamp_millis( void );
