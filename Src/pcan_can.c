#include <stm32f0xx_hal.h>
#include <string.h>
#include <assert.h>
#include "pcan_can.h"
#include "pcan_timestamp.h"
#include "pcan_varian.h"

#define CAN_TX_FIFO_SIZE (100)
#define CAN_TX_MAILBOXES (3) // bxCAN transmit mailboxes
#define CAN_RX_RING_SIZE (128) // frames and echoes the CAN interrupt can queue ahead of the main loop
#define CAN_RX_RESERVE (64) // RX ring slots kept for frames from other nodes while echoes wait for USB
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
  volatile uint32_t tx_tail; // next slot pcan_can_flush_tx() loads into a mailbox
  //? MCR.TXFP completes the mailboxes in the order they were loaded, while the HAL reports them in mailbox order, so tx_order keeps the load order.
  can_message_t tx_echo[CAN_TX_MAILBOXES]; // frame in each mailbox, echoed to the host once it completes
  uint8_t tx_order[CAN_TX_MAILBOXES]; // loaded mailboxes, oldest first
  volatile uint8_t tx_loaded; // entries in tx_order: mailboxes loaded and not retired yet
  uint8_t tx_done; // bit per mailbox whose request ended, waiting for pcan_can_tx_retire()
  uint8_t tx_ok; // bit per mailbox whose frame was acknowledged
  uint8_t tx_echo_req; // bit per mailbox whose frame the host asked to have echoed
  //? The CAN interrupt fills the ring and the main loop empties it, so each index has a single writer.
  can_message_t rx_ring[CAN_RX_RING_SIZE]; // received frames and echoes waiting for the main loop
  volatile uint32_t rx_head; // next slot the interrupt fills
  volatile uint32_t rx_tail; // next slot the main loop reads
  volatile uint8_t isr_err; // CAN_ERROR_FLAG_* events the interrupt latched for the main loop
  volatile uint8_t isr_bus_err; // CAN_LEC_* of the last bus error, with CAN_BUS_ERR_RX
  int (*rx_cb)( const can_message_t * );
  void (*can_err_cb)( uint8_t err, uint8_t bus_err, uint8_t rx_err, uint8_t tx_err );
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
  //? With several mailboxes loaded, identifier priority would reorder the host's frames; MCR.TXFP sends them in load order.
  g_hcan.Init.TransmitFifoPriority = ENABLE; // mailboxes go out in the order they were loaded

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
  //? Refilling the mailboxes from their completion interrupt also sends queued frames back to back.
  HAL_NVIC_SetPriority( CEC_CAN_IRQn, 1, 0 ); // below SysTick
  HAL_NVIC_EnableIRQ( CEC_CAN_IRQn ); // CEC_CAN_IRQHandler() serves the controller and refills the TX mailboxes
}

/**
 * @brief Abort every TX mailbox; each one still reports its end through the HAL's TX callbacks.
 */
static void pcan_can_tx_abort( void )
{
  //? HAL_CAN_AbortTxRequest() sets ABRQ with a read-modify-write of TSR, which writes pending RQCP flags back as 1 and so clears those completions.
  g_hcan.Instance->TSR = CAN_TSR_ABRQ0 | CAN_TSR_ABRQ1 | CAN_TSR_ABRQ2; // an RQCP flag ignores a written 0
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
  //? The interrupt loads the TX mailboxes and switches MCR.NART, so it must not run between the HAL's read-modify-writes of MCR.
  HAL_NVIC_DisableIRQ( CEC_CAN_IRQn ); // its events wait until the controller is settled

  //- Reconfigure in init mode
  //? Loaded mailboxes keep their requests through init mode, so their frames go out with the new settings once the controller restarts.
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

  //- Split each host time quantum in two when segment 1 is a single quantum
  //? With a 1-quantum segment 1 (BTR1 10h at 1 Mbit/s) bxCAN went bus-off on this board, while the same timing in half-length quanta works.
  uint32_t split = ( ( tseg1 == 1 ) && ( tseg2 <= 4 ) ) ? 2u : 1u; // bxCAN quanta per host quantum
  uint32_t sjw_split = ( sjw * split > 4u ) ? 4u : sjw * split; // jump width in bxCAN quanta, at most 4

  /* CAN bus freq is 48 */
  g_hcan.Init.Prescaler = brp * 6 / split; // 48 MHz clocks per bxCAN quantum

  g_hcan.Init.SyncJumpWidth = sjw_table[sjw_split - 1];
  g_hcan.Init.TimeSeg1 = tseg1_table[( tseg1 + 1 ) * split - 2]; // sync and segment 1 keep their length, so the sample point stays
  g_hcan.Init.TimeSeg2 = tseg2_table[tseg2 * split - 1];

  pcan_can_reinit(); // apply the timing, back on the bus when it was on
}

void pcan_can_install_rx_callback( int (*cb)( const can_message_t * ) )
{
  can_dev.rx_cb = cb;
}

void pcan_can_install_error_callback( void (*cb)( uint8_t err, uint8_t bus_err, uint8_t rx_err, uint8_t tx_err ) )
{
  can_dev.can_err_cb = cb;
}

/**
 * @brief Write a frame into an empty TX mailbox and request its transmission.
 * @param[in] mb    Mailbox index, 0 to CAN_TX_MAILBOXES - 1.
 * @param[in] p_msg Frame to send.
 * @note Writes the registers directly: the vendored HAL_CAN_AddTxMessage() is patched to use mailbox 0 only.
 */
static void pcan_can_load_mailbox( uint32_t mb, const can_message_t *p_msg )
{
  CAN_TxMailBox_TypeDef *p_box = &g_hcan.Instance->sTxMailBox[mb]; // the mailbox's registers
  const uint8_t *p_data = p_msg->data; // payload, first byte first on the wire
  uint32_t tir;

  //- Identifier and frame type
  if( p_msg->flags & CAN_FLAG_EXTID )
    tir = ( ( p_msg->id & 0x1FFFFFFFu ) << CAN_TI0R_EXID_Pos ) | CAN_TI0R_IDE; // 29-bit identifier
  else
    tir = ( p_msg->id & 0x7FFu ) << CAN_TI0R_STID_Pos; // 11-bit identifier
  if( p_msg->flags & CAN_FLAG_RTR )
    tir |= CAN_TI0R_RTR; // remote frame

  //- Length and payload, then the request
  p_box->TDTR = p_msg->dlc & CAN_TDT0R_DLC; // data length code, no time stamp in the frame
  p_box->TDLR = (uint32_t)p_data[0] | ( (uint32_t)p_data[1] << 8 ) | ( (uint32_t)p_data[2] << 16 ) | ( (uint32_t)p_data[3] << 24 ); // bytes 0-3
  p_box->TDHR = (uint32_t)p_data[4] | ( (uint32_t)p_data[5] << 8 ) | ( (uint32_t)p_data[6] << 16 ) | ( (uint32_t)p_data[7] << 24 ); // bytes 4-7
  p_box->TIR = tir | CAN_TI0R_TXRQ; // request transmission once the mailbox holds the whole frame
}

/**
 * @brief Collect the TX mailboxes that hold a frame not retired yet.
 * @return Bit per mailbox, bit 0 for mailbox 0.
 */
static uint8_t pcan_can_tx_loaded_mask( void )
{
  uint8_t mask = 0;

  for( uint32_t i = 0; i < can_dev.tx_loaded; ++i )
    mask |= 1u << can_dev.tx_order[i]; // one bit per loaded mailbox
  return mask;
}

/**
 * @brief Count the free slots in the RX ring.
 * @return Frames or echoes the CAN interrupt can still queue.
 */
static uint32_t pcan_can_rx_free( void )
{
  //? pcan_can_rx_push() keeps one slot empty so a full ring differs from an empty one.
  return ( can_dev.rx_tail + CAN_RX_RING_SIZE - can_dev.rx_head - 1u ) % CAN_RX_RING_SIZE; // slots the interrupt can still fill
}

/**
 * @brief Move queued frames into the free TX mailboxes, oldest first.
 * @note Does nothing while the controller is stopped, so a closed channel never loads a mailbox.
 * @note Switches automatic retransmission to match a frame's CAN_FLAG_ONESHOT only with no mailbox loaded, as MCR.NART covers every pending frame.
 * @note Keeps each loaded frame so pcan_can_tx_retire() can echo it, and holds back frames that ask for an echo while the RX ring is nearly full.
 * @note Runs in the CAN interrupt only, after each event the HAL handled; pcan_can_poll() raises the interrupt for frames queued while no mailbox was loaded.
 */
static void pcan_can_flush_tx( void )
{
  //- Hold queued frames while the controller is stopped
  if( HAL_CAN_GetState( &g_hcan ) != HAL_CAN_STATE_LISTENING )
    return; // bus off, frames stay queued

  while( can_dev.tx_head != can_dev.tx_tail ) // frames waiting
  {
    __DMB(); // read the slot only after seeing the head that published it
    const can_message_t *p_msg = &can_dev.tx_fifo[can_dev.tx_tail]; // oldest queued frame

    //- Find a mailbox that is empty and retired
    //? A mailbox reads empty as soon as its frame ends; reloading it before pcan_can_tx_retire() ran would overwrite that frame's echo.
    uint32_t free_boxes = ( ( g_hcan.Instance->TSR & CAN_TSR_TME ) >> CAN_TSR_TME_Pos ) & ~pcan_can_tx_loaded_mask(); // bit per usable mailbox
    if( !free_boxes )
      return; // every mailbox holds a frame; the next completion calls this again

    //- Send no faster than USB carries the echoes away
    //? Echoes queue in the RX ring until the host reads them, so outrunning USB would overflow the ring and drop frames from other nodes.
    if( ( p_msg->flags & CAN_FLAG_ECHO ) && ( pcan_can_rx_free() <= CAN_RX_RESERVE ) )
      return; // pcan_can_poll() restarts the mailboxes once the ring drains

    //- Match automatic retransmission to the frame
    FunctionalState retransmit = ( p_msg->flags & CAN_FLAG_ONESHOT ) ? DISABLE : ENABLE; // one-shot frames get a single attempt
    if( retransmit != g_hcan.Init.AutoRetransmission )
    {
      if( can_dev.tx_loaded )
        return; // MCR.NART would change the frames already loaded; their completions call this again
      g_hcan.Init.AutoRetransmission = retransmit; // HAL_CAN_Init() re-applies it after a re-init
      if( retransmit == ENABLE )
        CLEAR_BIT( g_hcan.Instance->MCR, CAN_MCR_NART ); // retransmit until acknowledged
      else
        SET_BIT( g_hcan.Instance->MCR, CAN_MCR_NART ); // one attempt only
    }

    //- Track the frame, then load it
    uint32_t mb = ( free_boxes & 1u ) ? 0u : ( ( free_boxes & 2u ) ? 1u : 2u ); // lowest usable mailbox
    uint8_t bit = 1u << mb; // its bit in the masks
    can_dev.tx_echo[mb] = *p_msg; // kept until the frame ends
    if( p_msg->flags & CAN_FLAG_ECHO )
      can_dev.tx_echo_req |= bit; // echo only what the host asked for
    else
      can_dev.tx_echo_req &= ~bit; // no echo
    can_dev.tx_done &= ~bit; // no completion yet
    can_dev.tx_order[can_dev.tx_loaded] = mb; // newest last
    ++can_dev.tx_loaded; // one more mailbox in flight
    pcan_can_load_mailbox( mb, p_msg ); // request transmission

    /* update fifo index */
    uint32_t tail = can_dev.tx_tail+1;
    if( tail == CAN_TX_FIFO_SIZE )
      tail = 0;
    __DMB(); // finish reading the slot before the main loop may refill it
    can_dev.tx_tail = tail; // release the slot
  }
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
 * @brief Drop every frame still queued for transmission and forget the loaded mailboxes.
 * @note Call it with the CAN interrupt masked, after aborting the mailboxes; it also resets the index pcan_can_flush_tx() advances.
 */
static void pcan_can_tx_fifo_clear( void )
{
  can_dev.tx_head = 0; // empty the software TX FIFO
  can_dev.tx_tail = 0; // read index follows the write index
  can_dev.tx_loaded = 0; // pcan_can_tx_done() ignores the aborted mailboxes' completions
  can_dev.tx_done = 0; // no completion waits for retirement
  can_dev.tx_echo_req = 0; // an aborted frame is never echoed
}

int pcan_can_set_bus_active( uint16_t mode )
{
  int result = 0; // bus left, or joined

  //- Keep the CAN interrupt out while the controller changes
  HAL_NVIC_DisableIRQ( CEC_CAN_IRQn ); // as in pcan_can_reinit(), the HAL's MCR writes must not interleave with it

  //- Leave the bus and drop pending frames, also before a restart
  pcan_can_tx_abort(); // cancel pending mailbox requests
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
 * @brief Retire ended TX mailboxes in the order they were loaded, counting and echoing the acknowledged frames.
 * @note Runs in the CAN interrupt; stops at the oldest mailbox still pending.
 */
static void pcan_can_tx_retire( void )
{
  while( can_dev.tx_loaded && ( can_dev.tx_done & ( 1u << can_dev.tx_order[0] ) ) ) // the oldest loaded mailbox has ended
  {
    uint32_t mb = can_dev.tx_order[0]; // oldest loaded mailbox
    uint8_t bit = 1u << mb; // its bit in the masks
    uint8_t ok = can_dev.tx_ok & bit; // frame acknowledged
    uint8_t echo = can_dev.tx_echo_req & bit; // host asked for its echo

    //- Drop it from the load order
    can_dev.tx_order[0] = can_dev.tx_order[1]; // the next oldest moves up
    can_dev.tx_order[1] = can_dev.tx_order[2];
    --can_dev.tx_loaded; // one mailbox fewer in flight
    can_dev.tx_done &= ~bit; // completion handled
    can_dev.tx_ok &= ~bit;
    can_dev.tx_echo_req &= ~bit;

    //- Count and echo an acknowledged frame
    if( !ok )
      continue; // aborted, or a single-shot frame that failed: never echoed
    ++can_dev.tx_msgs; // frame acknowledged on the bus
    if( echo )
    {
      can_dev.tx_echo[mb].timestamp = pcan_timestamp_ticks(); // completion time, not queue time
      pcan_can_rx_push( &can_dev.tx_echo[mb] ); // the main loop records it like a received frame with the echo flag
    }
  }
}

/**
 * @brief Record the end of a TX mailbox's request, then retire whatever is next in load order.
 * @param[in] mb Mailbox index, 0 to CAN_TX_MAILBOXES - 1.
 * @param[in] ok Non-zero when the frame was acknowledged, zero when it was aborted or its single attempt failed.
 * @note Runs in the CAN interrupt; a mailbox pcan_can_tx_fifo_clear() already forgot is ignored.
 */
static void pcan_can_tx_done( uint32_t mb, uint8_t ok )
{
  uint8_t bit = 1u << mb; // the mailbox's bit in the masks

  //- Ignore a completion nobody waits for
  if( !( pcan_can_tx_loaded_mask() & bit ) )
    return; // an abort from before the last bus-on or bus-off

  //- Record the outcome and retire in load order
  can_dev.tx_done |= bit; // request ended
  if( ok )
    can_dev.tx_ok |= bit; // acknowledged
  else
    can_dev.tx_ok &= ~bit; // aborted or failed
  pcan_can_tx_retire(); // retire every mailbox whose turn has come
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
  //- Hand the frames and echoes the interrupt queued to the protocol, while it takes them
  while( can_dev.rx_tail != can_dev.rx_head )
  {
    __DMB(); // read the slot only after seeing the head that published it
    can_message_t msg = can_dev.rx_ring[can_dev.rx_tail]; // copy before releasing the slot
    if( can_dev.rx_cb && !can_dev.rx_cb( &msg ) )
      break; // USB buffer full: the frame stays queued for the next poll
    __DMB(); // finish the copy before the interrupt may refill the slot
    can_dev.rx_tail = ( can_dev.rx_tail + 1u ) % CAN_RX_RING_SIZE; // release the slot
  }

  //- Report the events the interrupt latched, with the controller's counters
  __disable_irq(); // fetch and clear without losing an event the interrupt adds meanwhile
  uint8_t errs = can_dev.isr_err; // events since the last poll
  uint8_t bus_err = can_dev.isr_bus_err; // the last bus error among them
  can_dev.isr_err = 0; // start collecting again
  __enable_irq();
  if( errs && can_dev.can_err_cb )
  {
    can_bus_status_t status;

    pcan_can_get_bus_status( &status ); // read the controller's REC and TEC
    can_dev.can_err_cb( errs, bus_err, status.rx_err, status.tx_err ); // report the events with the hardware counters
  }

  //- Start the mailboxes for frames queued while none was loaded
  //? Only the CAN interrupt loads the mailboxes, and with none loaded no completion interrupt comes.
  uint8_t started = ( HAL_CAN_GetState( &g_hcan ) == HAL_CAN_STATE_LISTENING ); // controller on the bus
  uint8_t room = ( pcan_can_rx_free() > CAN_RX_RESERVE ); // pcan_can_flush_tx() would load a frame that asks for an echo
  if( started && room && !can_dev.tx_loaded && ( can_dev.tx_head != can_dev.tx_tail ) )
    HAL_NVIC_SetPendingIRQ( CEC_CAN_IRQn ); // CEC_CAN_IRQHandler() loads the queued frames
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
  pcan_can_tx_done( 0, 1 ); // frame acknowledged
}

void HAL_CAN_TxMailbox1CompleteCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  pcan_can_tx_done( 1, 1 ); // frame acknowledged
}

void HAL_CAN_TxMailbox2CompleteCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  pcan_can_tx_done( 2, 1 ); // frame acknowledged
}

void HAL_CAN_TxMailbox0AbortCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  pcan_can_tx_done( 0, 0 ); // an aborted frame is never echoed
}

void HAL_CAN_TxMailbox1AbortCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  pcan_can_tx_done( 1, 0 ); // an aborted frame is never echoed
}

void HAL_CAN_TxMailbox2AbortCallback( CAN_HandleTypeDef *hcan )
{
  UNUSED( hcan );
  pcan_can_tx_done( 2, 0 ); // an aborted frame is never echoed
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

  //- Retire mailboxes whose request ended after a failed attempt
  //? The HAL reports those as errors and calls no TX callback: a single-shot frame that failed, or an abort after a failed retry.
  if( err & ( HAL_CAN_ERROR_TX_ALST0 | HAL_CAN_ERROR_TX_TERR0 ) )
    pcan_can_tx_done( 0, 0 ); // mailbox 0 gave up
  if( err & ( HAL_CAN_ERROR_TX_ALST1 | HAL_CAN_ERROR_TX_TERR1 ) )
    pcan_can_tx_done( 1, 0 ); // mailbox 1 gave up
  if( err & ( HAL_CAN_ERROR_TX_ALST2 | HAL_CAN_ERROR_TX_TERR2 ) )
    pcan_can_tx_done( 2, 0 ); // mailbox 2 gave up

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

  //- Keep the last bus error for pcan_can_poll()
  //? The HAL clears ESR.LEC before this callback runs, so the error type comes from its error bits, listed here in CAN_LEC_* order from 1.
  static const uint32_t lec_errors[] = { HAL_CAN_ERROR_STF, HAL_CAN_ERROR_FOR, HAL_CAN_ERROR_ACK, HAL_CAN_ERROR_BR, HAL_CAN_ERROR_BD, HAL_CAN_ERROR_CRC };
  uint8_t lec = 0; // CAN_LEC_* code, 0 without a bus error
  for( uint8_t i = 0; i < sizeof( lec_errors ) / sizeof( lec_errors[0] ); ++i )
    if( err & lec_errors[i] )
      lec = i + 1u; // the HAL reports one error type per event
  if( lec )
  {
    uint8_t receiving = ( ( hcan->Instance->TSR & CAN_TSR_TME ) == CAN_TSR_TME ); // no mailbox pending, so not the transmitter
    can_dev.isr_bus_err = lec | ( receiving ? CAN_BUS_ERR_RX : 0 ); // overwritten by the next bus error
    can_dev.isr_err |= CAN_ERROR_FLAG_BUS_ERR; // reported with the next poll
  }

  HAL_CAN_ResetError( hcan );
}

void CEC_CAN_IRQHandler( void )
{
  HAL_CAN_IRQHandler( &g_hcan ); // RX into the ring, TX completions and errors, all in interrupt context
  pcan_can_flush_tx(); // refill the mailboxes the HAL's callbacks just retired
}
