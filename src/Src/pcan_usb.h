#pragma once
#include <stdint.h>

/* CAN-USB Endpoints */
#define PCAN_USB_EP_CMDOUT  0x01
#define PCAN_USB_EP_CMDIN   0x81
#define PCAN_USB_EP_MSGOUT  0x02
#define PCAN_USB_EP_MSGIN   0x82

struct t_class_data
{
  uint16_t  ep_tx_data_pending[15];
  uint8_t   ep_tx_in_use[15];
  uint8_t   buffer_cmd[16];
  uint8_t   buffer_data[64];
};

struct t_m2h_fsm
{
  uint8_t   state;
  uint8_t   ep_addr;
  uint8_t   *pdbuf;
  int       dbsize;
  uint32_t  total_tx;
};

/**
 * @brief Forget an IN endpoint's pending transfer so new data can be queued on it.
 * @param[in] ep IN endpoint address, e.g. PCAN_USB_EP_MSGIN.
 * @return 1 when the endpoint was reset, 0 before the device is configured or when the HAL refuses.
 */
int pcan_flush_ep( uint8_t ep );
int pcan_flush_data( struct t_m2h_fsm *pfsm, void *src, int size );

uint16_t pcan_usb_send_command_buffer( const void *p, uint16_t size );
uint16_t pcan_usb_send_data_buffer( const void *p, uint16_t size );
void pcan_usb_init( void );

/**
 * @brief Service the USB peripheral and resume the data endpoint once the TX FIFO has room.
 * @note Call it from the main loop; the USB interrupt is not used.
 */
void pcan_usb_poll( void );

