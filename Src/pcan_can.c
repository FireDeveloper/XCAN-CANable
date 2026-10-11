#include <stm32f0xx_hal.h>
#include <string.h>
#include <assert.h>
#include "pcan_can.h"
#include "pcan_timestamp.h"
#include "pcan_varian.h"

#define CAN_TX_FIFO_SIZE (100)
#define CAN_RX_RING_SIZE (64) // frames and echoes the CAN interrupt can queue ahead of the main loop
static CAN_HandleTypeDef g_hcan = { .Instance = CAN };
#define INTERNAL_CAN_IT_FLAGS          (  CAN_IT_TX_MAILBOX_EMPTY |\
                                          CAN_IT_RX_FIFO0_MSG_PENDING | CAN_IT_RX_FIFO1_MSG_PENDING |\
                                          CAN_IT_RX_FIFO0_OVERRUN | CAN_IT_RX_FIFO1_OVERRUN |\
                                          CAN_IT_BUSOFF |\
                                          CAN_IT_ERROR_WARNING |\
                                          CAN_IT_ERROR_PASSIVE |\
                                          CAN_IT_BUSOFF |\
                                          CAN_IT_LAST_ERROR_CODE |\
                                          CAN_IT_ERROR )

static struct
{
  uint32_t tx_msgs;
  uint32_t tx_errs;
  uint32_t tx_ovfs;

  uint32_t rx_msgs;
  uint32_t rx_errs;
  uint32_t rx_ovfs;

  //? The main loop fills the TX FIFO and the CAN interrupt empties it, so each index has a single writer.
  //? pcan_can_set_bus_active() resets both with the interrupt masked.
  can_message_t tx_fifo[CAN_TX_FIFO_SIZE];
  volatile uint32_t tx_head; // next slot pcan_can_send_message() fills
  volatile uint32_t tx_tail; // next slot pcan_can_flush_tx() loads into mailbox 0
  //? The vendored HAL_CAN_AddTxMessage() loads mailbox 0 only, so one slot covers every frame in flight.
  can_message_t tx_echo; // frame in mailbox 0, echoed to the host once it completes
  uint8_t tx_echo_valid; // the host asked for an echo of tx_echo
  //? The CAN interrupt fills the ring and the main loop empties it, so each index has a single writer.
  can_message_t rx_ring[CAN_RX_RING_SIZE]; // received frames and echoes waiting for the main loop
  volatile uint32_t rx_head; // next slot the interrupt fills
  volatile uint32_t rx_tail; // next slot the main loop reads
  volatile uint8_t isr_err; // CAN_ERROR_FLAG_* events the interrupt latched for the main loop
  void (*rx_cb)(can_message_t *);
  void (*can_err_cb)( uint8_t err, uint8_t rx_err, uint8_t tx_err );
}
can_dev = { 0 };

void pcan_can_init(void)
{
  CAN_FilterTypeDef filter = { 0 };

  __HAL_RCC_CAN1_CLK_ENABLE();

  PIN_ENABLE_CLOCK( CAN_RX );
  PIN_ENABLE_CLOCK( CAN_TX );

  PIN_INIT( CAN_RX );
  PIN_INIT( CAN_TX );

  HAL_CAN_DeInit( &g_hcan );

  g_hcan.Instance = CAN;
  g_hcan.Init.Prescaler = 16;
  g_hcan.Init.Mode = CAN_MODE_NORMAL;
  g_hcan.Init.SyncJumpWidth = CAN_SJW_1TQ;
  g_hcan.Init.TimeSeg1 = CAN_BS1_1TQ;
  g_hcan.Init.TimeSeg2 = CAN_BS2_1TQ;
  g_hcan.Init.TimeTriggeredMode = DISABLE;
  //? PCAN hosts restart a bus-off channel themselves; Linux cannot restart one that already recovered, which blocks its TX queue.
  g_hcan.Init.AutoBusOff = DISABLE; // stay bus-off until the host restarts the channel
  g_hcan.Init.AutoWakeUp = DISABLE;
  g_hcan.Init.AutoRetransmission = ENABLE;
  g_hcan.Init.ReceiveFifoLocked = DISABLE;
  g_hcan.Init.TransmitFifoPriority = ENABLE;

  if( HAL_CAN_Init( &g_hcan ) != HAL_OK )
  {
    assert( 0 );
  }

  filter.FilterMode = CAN_FILTERMODE_IDMASK;
  filter.FilterScale = CAN_FILTERSCALE_32BIT;
  filter.FilterFIFOAssignment = CAN_FILTER_FIFO0;
  filter.FilterActivation = ENABLE;
  filter.FilterBank = 0;
  filter.SlaveStartFilterBank = 0;
  
  if( HAL_CAN_ConfigFilter( &g_hcan, &filter ) != HAL_OK )
  {
    assert( 0 );
  }

  if( HAL_CAN_ActivateNotification( &g_hcan, INTERNAL_CAN_IT_FLAGS ) != HAL_OK )
  {
    assert( 0 );
  }

  //- Serve the controller from its interrupt
  //? The RX FIFO holds 3 frames, about 150 us at 1 Mbit/s, and a main-loop pass with USB work takes longer, so polling lost frames.
  //? Refilling mailbox 0 from its completion interrupt also sends queued frames back to back.
  HAL_NVIC_SetPriority( CEC_CAN_IRQn, 1, 0 ); // below SysTick
  HAL_NVIC_EnableIRQ( CEC_CAN_IRQn ); // CEC_CAN_IRQHandler() serves the controller and refills mailbox 0
}

/**
 * @brief Apply g_hcan.Init to the controller and put it back on the bus when it was started.
 * @note A controller that fails to enter init mode or to resync stays off the bus until the next bus-on command.
 * @note The CAN interrupt stays masked meanwhile; queued frames go out once the controller is back on the bus.
 */
static void pcan_can_reinit( void )
{
  //- Remember whether the controller was on the bus
  uint8_t was_started = ( HAL_CAN_GetState( &g_hcan ) == HAL_CAN_STATE_LISTENING ); // HAL_CAN_Init leaves it in init mode

  //- Keep the CAN interrupt out while the controller changes
  //? The interrupt loads mailbox 0 and switches MCR.NART, so it must not run between the HAL's read-modify-writes of MCR.
  HAL_NVIC_DisableIRQ( CEC_CAN_IRQn ); // its events wait until the controller is settled

  //- Reconfigure in init mode
  HAL_CAN_AbortTxRequest( &g_hcan, CAN_TX_MAILBOX0 | CAN_TX_MAILBOX1 | CAN_TX_MAILBOX2 ); // a queued frame must not go out with the old settings
  can_dev.tx_echo_valid = 0; // the aborted frame is never echoed
  uint8_t in_init = ( HAL_CAN_Init( &g_hcan ) == HAL_OK ); // controller holds the new settings in init mode

  //- Rejoin the bus when it was on
  if( in_init && was_started && ( HAL_CAN_Start( &g_hcan ) != HAL_OK ) )
    HAL_CAN_Init( &g_hcan ); // leave the error state the start timeout left

  //- Let the interrupt serve the controller again
  HAL_NVIC_EnableIRQ( CEC_CAN_IRQn ); // pending events run now; pcan_can_poll() restarts queued frames
}

void pcan_can_set_bitrate( uint16_t brp, uint8_t tseg1, uint8_t tseg2, uint8_t sjw )
{
  static const uint32_t sjw_table[] = 
  { 
    CAN_SJW_1TQ, CAN_SJW_2TQ, CAN_SJW_3TQ, CAN_SJW_4TQ 
  };
  static const uint32_t tseg1_table[] = 
  { 
    CAN_BS1_1TQ, CAN_BS1_2TQ, CAN_BS1_3TQ, CAN_BS1_4TQ, 
    CAN_BS1_5TQ, CAN_BS1_6TQ, CAN_BS1_7TQ, CAN_BS1_8TQ, 
    CAN_BS1_9TQ, CAN_BS1_10TQ, CAN_BS1_11TQ, CAN_BS1_12TQ,
    CAN_BS1_13TQ, CAN_BS1_14TQ, CAN_BS1_15TQ, CAN_BS1_16TQ
  };
  static const uint32_t tseg2_table[] =
  { 
    CAN_BS2_1TQ, CAN_BS2_2TQ, CAN_BS2_3TQ, CAN_BS2_4TQ,
    CAN_BS2_5TQ, CAN_BS2_6TQ, CAN_BS2_7TQ, CAN_BS2_8TQ
  };

  if( sjw > 4 )
    sjw = 4;
  if( tseg1 > 16 )
    tseg1 = 16;
  if( tseg2 > 8 )
    tseg2 = 8;

  /* CAN bus freq is 48 */
  g_hcan.Init.Prescaler = brp * 6;

  g_hcan.Init.SyncJumpWidth = sjw_table[sjw - 1];
  g_hcan.Init.TimeSeg1 = tseg1_table[tseg1 - 1];
  g_hcan.Init.TimeSeg2 = tseg2_table[tseg2 - 1];

  pcan_can_reinit(); // apply the timing, back on the bus when it was on
}

void pcan_can_install_rx_callback( void (*cb)( can_message_t * ) )
{
  can_dev.rx_cb = cb;
}

void pcan_can_install_error_callback( void (*cb)( uint8_t, uint8_t, uint8_t ) )
{
  can_dev.can_err_cb = cb;
}

static int pcan_try_send_message( const can_message_t *p_msg )
{
  CAN_TxHeaderTypeDef msg = { .TransmitGlobalTime = DISABLE };
  uint32_t txMailbox = 0;

  if( p_msg->flags & CAN_FLAG_EXTID )
  {
    msg.ExtId = p_msg->id & 0x1FFFFFFF;
    msg.IDE = CAN_ID_EXT;
  }
  else
  {
    msg.StdId = p_msg->id & 0x7FF;
    msg.IDE = CAN_ID_STD;
  }
  
  msg.DLC = p_msg->dlc;
  msg.RTR = (p_msg->flags & CAN_FLAG_RTR)?CAN_RTR_REMOTE:CAN_RTR_DATA;
  
  if( HAL_CAN_AddTxMessage( &g_hcan, &msg, (void*)p_msg->data, &txMailbox ) != HAL_OK )
    return -1;

  return txMailbox;
}

/**
 * @brief Move the oldest queued frame into a free TX mailbox.
 * @note Does nothing while the controller is stopped, so a closed channel never loads a mailbox.
 * @note Switches automatic retransmission to match the frame's CAN_FLAG_ONESHOT, waiting for mailbox 0 to empty first.
 * @note Keeps the loaded frame so HAL_CAN_TxMailbox0CompleteCallback() can echo it.
 * @note Runs in the CAN interrupt only, after each event the HAL handled; pcan_can_poll() raises the interrupt for frames queued while mailbox 0 sat idle.
 */
static void pcan_can_flush_tx( void )
{
  can_message_t *p_msg;

  //- Hold queued frames while the controller is stopped
  if( HAL_CAN_GetState( &g_hcan ) != HAL_CAN_STATE_LISTENING )
    return; // bus off, frames stay queued

  /* empty fifo */
  if( can_dev.tx_head == can_dev.tx_tail )
    return;

  __DMB(); // read the slot only after seeing the head that published it
  p_msg = &can_dev.tx_fifo[can_dev.tx_tail];

  //- Wait for mailbox 0 and for HAL_CAN_IRQHandler() to handle its previous frame
  //? Loading mailbox 0 clears its request-completed flag, so a completion the HAL has not handled yet would never echo.
  //? One TSR snapshot decides both: once it shows the mailbox empty and handled, no completion can slip in before the load.
  uint32_t tsr = g_hcan.Instance->TSR; // mailbox 0 state
  if( !( tsr & CAN_TSR_TME0 ) || ( tsr & CAN_TSR_RQCP0 ) )
    return; // frame still on the wire, or a completion that re-enters this interrupt first

  //- Match automatic retransmission to the frame; mailbox 0 is empty, so no frame is pending
  FunctionalState retransmit = ( p_msg->flags & CAN_FLAG_ONESHOT ) ? DISABLE : ENABLE; // one-shot frames get a single attempt
  if( retransmit != g_hcan.Init.AutoRetransmission )
  {
    g_hcan.Init.AutoRetransmission = retransmit; // HAL_CAN_Init() re-applies it after a re-init
    if( retransmit == ENABLE )
      CLEAR_BIT( g_hcan.Instance->MCR, CAN_MCR_NART ); // retransmit until acknowledged
    else
      SET_BIT( g_hcan.Instance->MCR, CAN_MCR_NART ); // one attempt only
  }

  //- Keep the frame for its echo before loading it
  //? The TX-complete interrupt can fire as soon as the frame is loaded, so the echo slot has to be ready first.
  can_dev.tx_echo = *p_msg; // mailbox 0 holds this frame until it completes
  can_dev.tx_echo_valid = ( p_msg->flags & CAN_FLAG_ECHO ) != 0; // echo only what the host asked for

  if( pcan_try_send_message( p_msg ) < 0 )
  {
    can_dev.tx_echo_valid = 0; // nothing loaded, nothing to echo
    return;
  }

  /* update fifo index */
  uint32_t tail = can_dev.tx_tail+1;
  if( tail == CAN_TX_FIFO_SIZE )
    tail = 0;
  __DMB(); // finish reading the slot before the main loop may refill it
  can_dev.tx_tail = tail; // release the slot
}

int pcan_can_send_message( const can_message_t *p_msg )
{
  if( !p_msg )
    return 0;

  uint32_t head = can_dev.tx_head+1;
  if( head == CAN_TX_FIFO_SIZE )
    head = 0;
  /* overflow ? just skip it */
  if( head == can_dev.tx_tail )
  {
    ++can_dev.tx_ovfs;
    return -1;
  }

  can_dev.tx_fifo[can_dev.tx_head] = *p_msg;
  __DMB(); // finish the copy before the interrupt can see the new head
  can_dev.tx_head = head; // hand the slot to pcan_can_flush_tx()

  return 0;
}

uint32_t pcan_can_tx_free( void )
{
  uint32_t used = ( can_dev.tx_head + CAN_TX_FIFO_SIZE - can_dev.tx_tail ) % CAN_TX_FIFO_SIZE; // frames waiting for a mailbox

  //? pcan_can_send_message() keeps one slot empty so a full FIFO differs from an empty one.
  return ( CAN_TX_FIFO_SIZE - 1u ) - used; // slots pcan_can_send_message() can still fill
}

void pcan_can_set_silent( uint8_t silent_mode )
{
  g_hcan.Init.Mode = silent_mode ? CAN_MODE_SILENT: CAN_MODE_NORMAL;
  pcan_can_reinit(); // apply the mode, back on the bus when it was on
}

void pcan_can_set_loopback( uint8_t loopback )
{
  g_hcan.Init.Mode = loopback ? CAN_MODE_LOOPBACK: CAN_MODE_NORMAL;
  pcan_can_reinit(); // apply the mode, back on the bus when it was on
}

/**
 * @brief Drop every frame still queued for transmission.
 * @note Call it with the CAN interrupt masked; it also resets the index pcan_can_flush_tx() advances.
 */
static void pcan_can_tx_fifo_clear( void )
{
  can_dev.tx_head = 0; // empty the software TX FIFO
  can_dev.tx_tail = 0; // read index follows the write index
  can_dev.tx_echo_valid = 0; // an aborted frame is never echoed
}

int pcan_can_set_bus_active( uint16_t mode )
{
  int result = 0; // bus left, or joined

  //- Keep the CAN interrupt out while the controller changes
  HAL_NVIC_DisableIRQ( CEC_CAN_IRQn ); // as in pcan_can_reinit(), the HAL's MCR writes must not interleave with it

  //- Leave the bus and drop pending frames, also before a restart
  HAL_CAN_AbortTxRequest( &g_hcan, CAN_TX_MAILBOX0 | CAN_TX_MAILBOX1 | CAN_TX_MAILBOX2 ); // cancel pending mailbox requests
  if( HAL_CAN_GetState( &g_hcan ) == HAL_CAN_STATE_LISTENING )
    HAL_CAN_Stop( &g_hcan ); // enter init mode once the frame on the wire ends
  if( HAL_CAN_GetState( &g_hcan ) != HAL_CAN_STATE_READY )
    HAL_CAN_Init( &g_hcan ); // leave the error state a failed start or stop left behind
  pcan_can_tx_fifo_clear(); // nothing queued outlives the session

  //- Join the bus when asked; leaving init mode also starts the bus-off recovery sequence
  if( mode && ( HAL_CAN_Start( &g_hcan ) != HAL_OK ) )
  {
    HAL_CAN_Init( &g_hcan ); // leave the error state the start timeout left
    result = -1; // controller never saw an idle bus
  }

  //- Let the interrupt serve the controller again
  HAL_NVIC_EnableIRQ( CEC_CAN_IRQn ); // pending events, such as the abort completion, run now
  return result;
}

void pcan_can_get_bus_status( can_bus_status_t *p_status )
{
  //- Input validation
  assert( p_status ); // reject a null status

  //- Read flags and counters in one access
  uint32_t esr = g_hcan.Instance->ESR; // snapshot the error status register

  //- Extract the error counters
  p_status->rx_err = ( esr & CAN_ESR_REC ) >> CAN_ESR_REC_Pos; // receive error counter
  p_status->tx_err = ( esr & CAN_ESR_TEC ) >> CAN_ESR_TEC_Pos; // transmit error counter, low byte

  //- Map the error flags to a bus state, most severe first
  if( esr & CAN_ESR_BOFF )
  {
    //? ESR.TEC is the low byte of a 9-bit counter, so it wraps once the counter passes 255 and the controller goes bus-off.
    p_status->tx_err = 0xFF; // saturate the wrapped counter
    p_status->state = CAN_BUS_STATE_OFF; // controller is bus-off
  }
  else if( esr & CAN_ESR_EPVF )
  {
    p_status->state = CAN_BUS_STATE_PASSIVE; // controller is error passive
  }
  else if( esr & CAN_ESR_EWGF )
  {
    p_status->state = CAN_BUS_STATE_WARNING; // error warning limit reached
  }
  else
  {
    p_status->state = CAN_BUS_STATE_ACTIVE; // controller is error active
  }
}

/**
 * @brief Queue a received frame or an echo for the main loop.
 * @param[in] p_msg Frame to queue; copied into the ring.
 * @note Runs in the CAN interrupt; a frame that finds the ring full is dropped and latches CAN_ERROR_FLAG_RX_OVF.
 */
static void pcan_can_rx_push( const can_message_t *p_msg )
{
  uint32_t head = can_dev.rx_head; // only the interrupt moves the head
  uint32_t next = ( head + 1u ) % CAN_RX_RING_SIZE; // slot after this one

  //- Drop the frame when the main loop has fallen a whole ring behind
  if( next == can_dev.rx_tail )
  {
    ++can_dev.rx_ovfs; // frames lost to a full ring
    can_dev.isr_err |= CAN_ERROR_FLAG_RX_OVF; // reported as an RX overrun
    return; // ring full
  }

  //- Copy, then publish
  can_dev.rx_ring[head] = *p_msg; // fill the free slot
  __DMB(); // finish the copy before the main loop can see the new head
  can_dev.rx_head = next; // hand the slot to the main loop
}

/**
 * @brief Read one frame from an RX FIFO into the RX ring, stamped with its arrival time.
 * @param[in,out] hcan CAN handle.
 * @param[in]     fifo CAN_RX_FIFO0 or CAN_RX_FIFO1.
 * @note Runs in the CAN interrupt.
 * @note A frame the HAL refuses to read is released unread and counted in rx_errs.
 */
static void pcan_can_rx_frame( CAN_HandleTypeDef *hcan, uint32_t fifo )
{
  CAN_RxHeaderTypeDef hdr = { 0 };
  can_message_t msg = { 0 };

  if( HAL_CAN_GetRxMessage( hcan, fifo, &hdr, msg.data ) != HAL_OK )
  {
    //? The HAL refuses to read while a start or stop timeout holds it in its error state, and a frame left in the FIFO retriggers this interrupt forever.
    if( fifo == CAN_RX_FIFO0 )
      hcan->Instance->RF0R = CAN_RF0R_RFOM0; // release the frame; writing 0 leaves the overrun and full flags set
    else
      hcan->Instance->RF1R = CAN_RF1R_RFOM1; // release the frame; writing 0 leaves the overrun and full flags set
    ++can_dev.rx_errs; // frame lost
    return;
  }

  if( hdr.IDE == CAN_ID_STD )
  {
    msg.id = hdr.StdId;
  }
  else
  {
    msg.id = hdr.ExtId;
    msg.flags |= CAN_FLAG_EXTID;
  }

  if( hdr.RTR == CAN_RTR_REMOTE )
  {
    msg.flags |= CAN_FLAG_RTR;
  }

  msg.dlc = hdr.DLC;
  msg.timestamp = pcan_timestamp_ticks(); // arrival time, taken in the interrupt
  pcan_can_rx_push( &msg ); // the main loop records it

  ++can_dev.rx_msgs;
}

void pcan_can_poll(void)
{
  //- Hand the frames and echoes the interrupt queued to the protocol
  while( can_dev.rx_tail != can_dev.rx_head )
  {
    __DMB(); // read the slot only after seeing the head that published it
    can_message_t msg = can_dev.rx_ring[can_dev.rx_tail]; // copy before releasing the slot
    __DMB(); // finish the copy before the interrupt may refill the slot
    can_dev.rx_tail = ( can_dev.rx_tail + 1u ) % CAN_RX_RING_SIZE; // release the slot
    if( can_dev.rx_cb )
      can_dev.rx_cb( &msg ); // record it for the host
  }

  //- Report the events the interrupt latched, with the controller's counters
  __disable_irq(); // fetch and clear without losing an event the interrupt adds meanwhile
  uint8_t errs = can_dev.isr_err; // events since the last poll
  can_dev.isr_err = 0; // start collecting again
  __enable_irq();
  if( errs && can_dev.can_err_cb )
  {
    can_bus_status_t status;

    pcan_can_get_bus_status( &status ); // read the controller's REC and TEC
    can_dev.can_err_cb( errs, status.rx_err, status.tx_err ); // report the events with the hardware counters
  }

  //- Start mailbox 0 for frames queued while it sat idle
  //? Only the CAN interrupt loads mailbox 0, and an idle mailbox raises no interrupt of its own.
  uint8_t idle = ( g_hcan.Instance->TSR & CAN_TSR_TME0 ) && ( HAL_CAN_GetState( &g_hcan ) == HAL_CAN_STATE_LISTENING ); // empty mailbox, bus on
  if( idle && ( can_dev.tx_head != can_dev.tx_tail ) )
    HAL_NVIC_SetPendingIRQ( CEC_CAN_IRQn ); // CEC_CAN_IRQHandler() loads the next frame
}

void HAL_CAN_RxFifo0MsgPendingCallback( CAN_HandleTypeDef *hcan )
{
  //? Runs in the CAN interrupt, so the 3-frame hardware FIFO is emptied as soon as frames arrive.
  for( uint32_t n = HAL_CAN_GetRxFifoFillLevel( hcan, CAN_RX_FIFO0 ); n; --n ) // frames pending now, so a failed read cannot spin
    pcan_can_rx_frame( hcan, CAN_RX_FIFO0 ); // hand each frame to the protocol
}

void HAL_CAN_RxFifo1MsgPendingCallback( CAN_HandleTypeDef *hcan )
{
  for( uint32_t n = HAL_CAN_GetRxFifoFillLevel( hcan, CAN_RX_FIFO1 ); n; --n ) // frames pending now, so a failed read cannot spin
    pcan_can_rx_frame( hcan, CAN_RX_FIFO1 ); // hand each frame to the protocol
}

void HAL_CAN_TxMailbox0CompleteCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  ++can_dev.tx_msgs;

  //- Queue the echo now that the frame is on the bus
  if( can_dev.tx_echo_valid )
  {
    can_dev.tx_echo_valid = 0; // one echo per frame
    can_dev.tx_echo.timestamp = pcan_timestamp_ticks(); // completion time, not queue time
    pcan_can_rx_push( &can_dev.tx_echo ); // the main loop records it like a received frame with the echo flag
  }
}

void HAL_CAN_TxMailbox0AbortCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  can_dev.tx_echo_valid = 0; // an aborted frame is never echoed
}

void HAL_CAN_TxMailbox1CompleteCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  ++can_dev.tx_msgs;
}

void HAL_CAN_TxMailbox2CompleteCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  ++can_dev.tx_msgs;
}

void HAL_CAN_RxFifo0FullCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  ++can_dev.rx_ovfs;
}

void HAL_CAN_RxFifo1FullCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  ++can_dev.rx_ovfs;
}

void HAL_CAN_SleepCallback( CAN_HandleTypeDef *hcan ){ UNUSED( hcan ); }
void HAL_CAN_WakeUpFromRxMsgCallback( CAN_HandleTypeDef *hcan ){ UNUSED( hcan ); }

void HAL_CAN_ErrorCallback( CAN_HandleTypeDef *hcan )
{
  //? Runs in the CAN interrupt; pcan_can_poll() reports the latched events from the main loop.
  uint32_t err = HAL_CAN_GetError( hcan );

  if ( err & ( HAL_CAN_ERROR_TX_TERR0 | HAL_CAN_ERROR_TX_TERR1 | HAL_CAN_ERROR_TX_TERR2 ) )
  {
    ++can_dev.tx_errs;
    can_dev.isr_err |= CAN_ERROR_FLAG_TX_ERR; // a mailbox failed to send
  }

  if( err & HAL_CAN_ERROR_BOF )
  {
    can_dev.isr_err |= CAN_ERROR_FLAG_BUSOFF; // controller is bus-off
  }

  if( err & ( HAL_CAN_ERROR_RX_FOV0 | HAL_CAN_ERROR_RX_FOV1 ) )
  {
    can_dev.isr_err |= CAN_ERROR_FLAG_RX_OVF; // the hardware RX FIFO dropped a frame
  }

  HAL_CAN_ResetError( hcan );
}

void CEC_CAN_IRQHandler( void )
{
  HAL_CAN_IRQHandler( &g_hcan ); // RX into the ring, TX completions and errors, all in interrupt context
  pcan_can_flush_tx(); // refill mailbox 0 as soon as its completion is handled
}
