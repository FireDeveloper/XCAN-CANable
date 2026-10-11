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
  int       dbsize; // largest transfer pcan_flush_data() accepts
  uint32_t  total_tx;
};

/**
 * @brief Forget an IN endpoint's pending transfer so new data can be queued on it.
 * @param[in] ep IN endpoint address, e.g. PCAN_USB_EP_MSGIN.
 * @return 1 when the endpoint was reset, 0 before the device is configured or when the HAL refuses.
 */
int pcan_flush_ep( uint8_t ep );

/**
 * @brief Start an IN transfer of @p size bytes from @p src on the endpoint of @p pfsm, unless an earlier transfer still runs.
 * @param[in,out] pfsm Endpoint transfer state; total_tx counts the bytes sent.
 * @param[in]     src  Bytes to send; NULL with @p size 0 sends a zero-length packet.
 * @param[in]     size Bytes to send, at most pfsm->dbsize.
 * @return 1 when the transfer started, 0 when the endpoint is busy, the device is not configured or @p size is too large.
 * @warning The USB driver reads @p src packet by packet while the transfer runs, so @p src must stay unchanged until pcan_flush_data_busy() returns 0.
 */
int pcan_flush_data( struct t_m2h_fsm *pfsm, void *src, int size );

/**
 * @brief Tell whether the endpoint of @p pfsm cannot start a transfer yet.
 * @param[in] pfsm Endpoint transfer state.
 * @return 1 while an earlier transfer runs or before the device is configured, 0 when pcan_flush_data() would start one.
 */
int pcan_flush_data_busy( const struct t_m2h_fsm *pfsm );

uint16_t pcan_usb_send_command_buffer( const void *p, uint16_t size );
uint16_t pcan_usb_send_data_buffer( const void *p, uint16_t size );
void pcan_usb_init( void );

/**
 * @brief Service the USB peripheral and resume the data endpoint once the TX FIFO has room.
 * @note Call it from the main loop; the USB interrupt is not used.
 */
void pcan_usb_poll( void );

