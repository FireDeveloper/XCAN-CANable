#pragma once
#include <stdint.h>
#include <stm32f0xx.h>

#define PCAN_TICKS_FROM_US( _us ) (((uint32_t)(_us)*1000u)/42666u)

/**
 * @brief Start TIM3 as the 16-bit protocol tick counter, one tick per 42.666 us.
 * @note The prescaler is loaded at once, so the first ticks already have the 42.666 us period.
 */
void pcan_timestamp_init( void );

/**
 * @brief Read the protocol tick counter.
 * @return TIM3 count, one tick per 42.666 us, wrapping at 16 bits.
 */
static inline uint16_t pcan_timestamp_ticks( void )
{
  //? Inline: the CAN interrupt stamps every frame and echo with it, and a call costs more than the register read.
  return TIM3->CNT; // ticks since TIM3 started
}

uint16_t pcan_timestamp_millis( void );
