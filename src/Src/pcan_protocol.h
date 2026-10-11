#pragma once
#include <stdint.h>

/**
 * @brief Set up the protocol state and the CAN controller; the USB serial number comes from the chip's unique ID.
 */
void pcan_protocol_init( void );
void pcan_protocol_poll( void );
void pcan_protocol_process_command( uint8_t *ptr, uint16_t size );
void pcan_protocol_process_data( uint8_t *ptr, uint16_t size );

/**
 * @brief Tell whether the TX path can take another full USB data packet.
 * @return 1 when the TX FIFO has room for every record a data packet can hold, or the bus is off and packets are dropped; 0 otherwise.
 * @note The USB data endpoint stays paused (NAKing) while this returns 0.
 */
uint8_t pcan_protocol_data_ready( void );
