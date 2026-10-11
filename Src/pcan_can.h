#pragma once
#include <stdint.h>

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
void pcan_can_poll( void );
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
 * @brief Join or leave the CAN bus.
 * @param[in] mode Non-zero joins the bus, zero leaves it.
 * @note Both directions drop the frames still queued for transmission, so frames from an earlier session never reach the bus.
 */
void pcan_can_set_bus_active( uint16_t mode );

/**
 * @brief Read the CAN controller's bus state and error counters from its error status register.
 * @param[out] p_status Bus state and error counters; must not be NULL.
 * @note The transmit error counter reads 255 while the controller is bus-off.
 */
void pcan_can_get_bus_status( can_bus_status_t *p_status );
void pcan_can_set_silent( uint8_t silent_mode );
void pcan_can_set_loopback( uint8_t loopback );
