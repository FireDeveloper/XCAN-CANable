#pragma once
#include <stdint.h>

#define CAN_FLAG_ONESHOT (0x10) // transmit once, no automatic retransmission
#define CAN_FLAG_ECHO  (0x20)
#define CAN_FLAG_RTR   (0x40)
#define CAN_FLAG_EXTID (0x80)

#define CAN_ERROR_FLAG_BUSOFF    (1<<0)
#define CAN_ERROR_FLAG_RX_OVF    (1<<1)
#define CAN_ERROR_FLAG_TX_ERR    (1<<2)

//? The values rise with severity; pcan_bus_state_poll() compares them to tell a worsening bus from a recovering one.
#define CAN_BUS_STATE_ACTIVE     (0) // both error counters below 96
#define CAN_BUS_STATE_WARNING    (1) // an error counter at or above 96
#define CAN_BUS_STATE_PASSIVE    (2) // an error counter above 127
#define CAN_BUS_STATE_OFF        (3) // transmit error counter above 255

typedef struct
{
  uint8_t   state; // CAN_BUS_STATE_*
  uint8_t   rx_err; // receive error counter
  uint8_t   tx_err; // transmit error counter
}
can_bus_status_t;

typedef struct
{
  uint32_t  id;
  uint8_t   data[8];
  uint8_t   dlc;
  uint8_t   flags;
  /* for self receive */
  uint8_t   dummy;
  /* in pcan ticks */
  uint16_t timestamp;
}
can_message_t;

void pcan_can_init( void );

/**
 * @brief Pass the frames and echoes the CAN interrupt queued to the protocol, report latched errors, and start transmission of queued frames.
 * @note Call it from the main loop; the CAN interrupt itself only queues work, so the protocol callbacks never run in interrupt context.
 * @note The CAN interrupt loads every frame into mailbox 0; this only raises it when frames wait while the mailbox sits idle.
 */
void pcan_can_poll( void );

/**
 * @brief Set the CAN bit timing from SJA1000-style values on the 8 MHz PCAN-USB time base.
 * @param[in] brp   Baud rate prescaler, 1-64.
 * @param[in] tseg1 Time segment 1 in time quanta, 1-16 (larger values clamp to 16).
 * @param[in] tseg2 Time segment 2 in time quanta, 1-8 (larger values clamp to 8).
 * @param[in] sjw   Resynchronisation jump width in time quanta, 1-4 (larger values clamp to 4).
 * @note A started controller rejoins the bus with the new timing; one that cannot resync stays off the bus until the next bus-on command.
 */
void pcan_can_set_bitrate( uint16_t brp, uint8_t tseg1, uint8_t tseg2, uint8_t sjw );
int pcan_can_send_message( const can_message_t *msg );

/**
 * @brief Count the free slots in the software TX FIFO.
 * @return Frames pcan_can_send_message() can still queue.
 */
uint32_t pcan_can_tx_free( void );

void pcan_can_install_rx_callback( void (*cb)( can_message_t * ) );
void pcan_can_install_error_callback( void (*cb)( uint8_t, uint8_t, uint8_t ) );

/**
 * @brief Join or leave the CAN bus; joining while already on restarts the channel.
 * @param[in] mode Non-zero joins the bus, zero leaves it.
 * @return 0 on success, -1 when the controller could not join the bus.
 * @retval 0  Bus left, or joined.
 * @retval -1 The controller saw no idle bus within the HAL timeout and stays off the bus.
 * @note Both directions drop the frames still queued for transmission, so frames from an earlier session never reach the bus.
 * @note A restart is how a bus-off controller recovers; it does not rejoin the bus on its own.
 */
int pcan_can_set_bus_active( uint16_t mode );

/**
 * @brief Read the CAN controller's bus state and error counters from its error status register.
 * @param[out] p_status Bus state and error counters; must not be NULL.
 * @note The transmit error counter reads 255 while the controller is bus-off.
 */
void pcan_can_get_bus_status( can_bus_status_t *p_status );

/**
 * @brief Switch listen-only (silent) mode on or off.
 * @param[in] silent_mode Non-zero for listen-only, zero for normal mode.
 * @note Replaces loopback mode; a started controller rejoins the bus in the new mode.
 */
void pcan_can_set_silent( uint8_t silent_mode );

/**
 * @brief Switch internal loopback mode on or off.
 * @param[in] loopback Non-zero for loopback, zero for normal mode.
 * @note Replaces silent mode; a started controller rejoins the bus in the new mode.
 */
void pcan_can_set_loopback( uint8_t loopback );
