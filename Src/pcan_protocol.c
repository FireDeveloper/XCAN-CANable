#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "pcan_can.h"
#include "pcan_usb.h"
#include "pcan_packet.h"
#include "pcan_led.h"
#include "pcan_timestamp.h"
#include "punker.h"

#define SJA1000_BASICCAN  0
#define SJA1000_PELICAN   1
static struct
{
  uint8_t bus_active;
  uint8_t ext_vcc_state;
  uint8_t led_mode;

  uint16_t last_timestamp_sync;
  uint16_t last_flush;
  struct
  {
    /* error handling related */
    uint8_t   err;
    uint8_t   ecc;
    uint8_t   rx_err;
    uint8_t   tx_err;
    uint8_t   bus_state; // CAN_BUS_STATE_* last reported to the host
    uint8_t   err_state_sent; // bus_state carried by the last error record; 0xFF forces the next one
    /* config */
    uint8_t   btr0;
    uint8_t   btr1;
    uint8_t   silient;
    uint8_t   loopback;
    uint8_t   err_mask;
    uint32_t  quartz_freq;
  }
  can;
  uint8_t   sja1000_shadow[6];
  uint8_t   sja1000_ic_mode;
  uint8_t   device_id;
  uint32_t  device_serial;
}
pcan_device =
{
  .device_id = 0xFF,
  .device_serial = 0xFFFFFFFF,

  .can = 
  {
    .quartz_freq = PCAN_USB_CRYSTAL_HZ,
    .loopback = 0,
  },
};

#define BLOCK_SIZE  (64)
#define HEADER_SIZE (2)
#define PCAN_MAX_RECORD_SIZE (BLOCK_SIZE*14)
typedef struct
{
  /* raw data array */
  uint8_t buffer[PCAN_MAX_RECORD_SIZE];
  /* ts counter, need to provide correct timestamp correction ( 2 or 1 byte ) */
  uint16_t ts_counter;
  uint16_t pos;
}
PCAN_RECORD_BUFFER_EX;

static uint8_t temp_data_buffer[PCAN_MAX_RECORD_SIZE] = { 0 };
static struct t_m2h_fsm data_fsm = 
{
  .state = 0,
  .ep_addr = PCAN_USB_EP_MSGIN,
  .pdbuf = temp_data_buffer,
  .dbsize = sizeof( temp_data_buffer ),
};

static PCAN_RECORD_BUFFER_EX pcan_records;

void pcan_sja1000_write( uint8_t reg_addr, uint8_t value )
{
  if( pcan_device.sja1000_ic_mode == SJA1000_PELICAN )
  {
    reg_addr &= 0x7F;
    switch( reg_addr )
    {
      case 6:
        pcan_device.sja1000_shadow[2] = value;
      break;
      case 7:
        pcan_device.sja1000_shadow[3] = value;
      break;
      case 14:
        pcan_device.sja1000_shadow[4] = value;
      break;
      case 15:
        pcan_device.sja1000_shadow[5] = ( value == 0xFF ) ? 0x7F: value;
      break;
      case 16:
        pcan_device.sja1000_shadow[0] = value;
      break;
      case 20:
        pcan_device.sja1000_shadow[1] = value;
      break;
      case 31:
        if( (value & 0x80 ) == 0 )
        {
          pcan_device.sja1000_ic_mode = SJA1000_BASICCAN;
        }
      break;
    }
  }
  else if( pcan_device.sja1000_ic_mode == SJA1000_BASICCAN )
  {
    reg_addr &= 0x1F;
    switch( reg_addr )
    {
      case 4:
        pcan_device.sja1000_shadow[0] = value;
      break;
      case 5:
        pcan_device.sja1000_shadow[1] = value;
      break;
      case 6:
        pcan_device.sja1000_shadow[2] = value;
      break;
      case 7:
        pcan_device.sja1000_shadow[3] = value;
      break;
      case 31:
        if( (value & 0x80 ) != 0 )
        {
          pcan_device.sja1000_ic_mode = SJA1000_PELICAN;
        }
      break;
    }
  }
}

uint8_t pcan_sja1000_read( uint8_t reg_addr )
{
  if( pcan_device.sja1000_ic_mode == SJA1000_PELICAN )
  {
    reg_addr &= 0x7F;
    switch( reg_addr )
    {
      case 0:
        return 0x01;
      case 6:
        return pcan_device.sja1000_shadow[2];
      case 7:
        return pcan_device.sja1000_shadow[3];
      case 14:
        return pcan_device.sja1000_shadow[4];
      case 15:
        return pcan_device.sja1000_shadow[5];
      case 16:
        return pcan_device.sja1000_shadow[0];
      case 20:
        return pcan_device.sja1000_shadow[1];
      case 31:
        return 0x80; /* PeliCAN mode */
      default:
        return 0;
    }
  }
  else if( pcan_device.sja1000_ic_mode == SJA1000_BASICCAN )
  {
    reg_addr &= 0x1F;
    switch( reg_addr )
    {
      case 0:
        return 0x01;
      case 4:
        return pcan_device.sja1000_shadow[0];
      case 5:
        return pcan_device.sja1000_shadow[1];
      case 6:
        return pcan_device.sja1000_shadow[2];
      case 7:
        return pcan_device.sja1000_shadow[3];
      case 31:
        return 0x00; /* BasicCAN mode */
      default:
        return 0;
    }
  }

  return 0;
}

void pcan_record_write_header( uint8_t *p )
{
  p[0] = 0x02; /* record header */
  p[1] = 0x00; /* record count */
}

void pcan_record_buffer_reset( PCAN_RECORD_BUFFER_EX *prec )
{
  pcan_record_write_header( prec->buffer );
  prec->ts_counter = 0;
  prec->pos = HEADER_SIZE;
}

/**
 * @brief Reserve room for one record in the USB record buffer.
 * @param[in,out] prec Record buffer; a new 64-byte block with its header opens when the current block cannot take @p size bytes.
 * @param[in]     size Bytes the record needs, at most BLOCK_SIZE - HEADER_SIZE.
 * @return Where the record goes, or NULL when @p size is too large or the buffer is full.
 * @note The record counts only once pcan_record_buffer_commit() runs.
 */
static uint8_t *pcan_record_buffer_request( PCAN_RECORD_BUFFER_EX *prec, uint16_t size )
{
  if( size > (BLOCK_SIZE-HEADER_SIZE) )
    return 0;
  uint16_t unused = BLOCK_SIZE - ( prec->pos & (BLOCK_SIZE-1) );
  //? pos on a block boundary means the previous record filled its block exactly, and the next block has no header yet.
  if( ( unused < size ) || ( unused == BLOCK_SIZE ) ) // open a new block when this one is short or full
  {
    /* align data to next slot bound */
    uint16_t next = ( prec->pos + (BLOCK_SIZE-1) ) & (~((uint16_t)(BLOCK_SIZE-1)));
    if( (next+HEADER_SIZE) >= PCAN_MAX_RECORD_SIZE )
      return 0;
    memset( &prec->buffer[next], 0x00, BLOCK_SIZE );
    pcan_record_write_header( &prec->buffer[next] );
    next += 2;
    prec->pos = next;
    /* reset ts_counter for next buffer */
    prec->ts_counter = 0;
  }

  if( ( prec->pos + size ) > PCAN_MAX_RECORD_SIZE )
    return (void*)0;

  return &prec->buffer[prec->pos];
}

void pcan_record_buffer_commit( PCAN_RECORD_BUFFER_EX *prec, uint16_t size )
{
  uint16_t pos = prec->pos&(~(BLOCK_SIZE-1));
  /* increse record count */
  ++prec->buffer[pos+1];
  prec->pos += size;
}

uint16_t pcan_record_buffer_flush( PCAN_RECORD_BUFFER_EX *prec )
{
  uint16_t ts_ticks = pcan_timestamp_ticks();
  int res, flush_size;

  if( prec->pos <= HEADER_SIZE )
  {
    /* try to send ZLP, original 800 */
    if( pcan_device.last_flush && (((uint16_t)(ts_ticks - pcan_device.last_flush)) >= PCAN_TICKS_FROM_US( 800 )) )
    {
      res = pcan_flush_data( &data_fsm, 0, 0 );
      if( res )
      {
        pcan_device.last_flush = 0;
      }
    }
    return 0;
  }

  flush_size = ( prec->pos + (BLOCK_SIZE-1) ) & (~((BLOCK_SIZE-1)));
  res = pcan_flush_data( &data_fsm, prec->buffer, flush_size );
  if( res )
  {
    pcan_record_buffer_reset( prec );
    pcan_device.last_flush = ts_ticks;
    return 1;
  }

  return 0;
}

/**
 * @brief Append a bus event record carrying the CAN error counters.
 * @param[in,out] prec  Record buffer the record is appended to.
 * @param[in]     flags PCAN_USB_ERR_CNT_INC when the bus state worsened, PCAN_USB_ERR_CNT_DEC when it recovered.
 * @return 1 when the record is appended, 0 when @p prec has no room for it.
 */
static uint8_t pcan_bus_event( PCAN_RECORD_BUFFER_EX *prec, uint8_t flags )
{
  uint8_t *ptr, sl, pos = 0;

  ptr = pcan_record_buffer_request( prec, 6 );
  if( !ptr )
    return 0; // no room for the record

  /* sl */
  sl = PCAN_USB_STATUSLEN_INTERNAL | 0x03;
  pack_u8( ptr, sl );
  ptr += 1;
  pos += 1;

  /* function */
  pack_u8( ptr, PCAN_USB_REC_BUSEVT );
  ptr += 1;
  pos += 1;

  /* number 0x80, 0x00 */
  pack_u8( ptr, flags );
  ptr += 1;
  pos += 1;

  /* ecc */
  pack_u8( ptr, pcan_device.can.ecc );
  ptr += 1;
  pos += 1;

  /* rx error counter */
  pack_u8( ptr, pcan_device.can.rx_err );
  ptr += 1;
  pos += 1;

  /* tx error counter */
  pack_u8( ptr, pcan_device.can.tx_err );
  ptr += 1;
  pos += 1;

  /* add new record */
  pcan_record_buffer_commit( prec, pos );
  return 1; // record appended
}

#define BUFFER_MINIMAL_GAP_SIZE (16u)
/**
 * @brief Append a received or echoed CAN frame to the USB record buffer.
 * @param[in,out] prec Record buffer; flushed to USB first when it has no room.
 * @param[in]     msg  Frame to record; a DLC above 8 is recorded as 8.
 * @note A frame that finds no room even after a flush is dropped and latches PCAN_USB_ERROR_RXQOVR.
 */
static void pcan_rx_can_frame( PCAN_RECORD_BUFFER_EX *prec, const can_message_t *msg )
{
  uint8_t *ptr, pos = 0;

  ptr = pcan_record_buffer_request( prec, BUFFER_MINIMAL_GAP_SIZE );
  /* wow ! too fast too furious !!! */
  if( !ptr )
  {
    if( !pcan_record_buffer_flush( prec ) )
    {
      pcan_device.can.err |= PCAN_USB_ERROR_RXQOVR;
      return;
    }
    ptr = pcan_record_buffer_request( prec, BUFFER_MINIMAL_GAP_SIZE );
    /* o_0 */
    if( !ptr )
      return;
  }
  /* recheck dlc for abnormal frames... */
  uint8_t len = msg->dlc > 8 ? 8: msg->dlc; // data bytes on the wire, a DLC of 9-15 carries 8
  uint8_t sl = len; // record DLC matches the bytes written

  if( msg->flags & CAN_FLAG_EXTID )
  {
    sl |= PCAN_USB_STATUSLEN_EXT_ID;
  }
  if( msg->flags & CAN_FLAG_RTR )
  {
    sl |= PCAN_USB_STATUSLEN_RTR;
  }

  pack_u8( ptr, sl );

  ptr += 1;
  pos += 1;

  if( sl & PCAN_USB_STATUSLEN_EXT_ID )
  {
    uint32_t id = ( msg->id & 0x1FFFFFFF ) << 3;
    if( msg->flags & CAN_FLAG_ECHO )
    {
      id |= 0x01;
    }

    pack_u32( ptr, id );

    ptr += 4;
    pos += 4;
  }
  else
  {
    uint16_t id = ( msg->id & 0x7FF ) << 5;
    if( msg->flags & CAN_FLAG_ECHO )
    {
      id |= 0x01;
    }
    pack_u16( ptr, id );

    ptr += 2;
    pos += 2;
  }
  
  /* timestamp to ticks, 1 tick is 42.666us */
  uint16_t timestamp16 = msg->timestamp;

  if( prec->ts_counter == 0 )
  {
    pack_u16( ptr, timestamp16 );
    ptr += 2;
    pos += 2;  
  }
  else
  {
    pack_u8( ptr, timestamp16&0xFF );
    ptr += 1;
    pos += 1;  
  }
  ++prec->ts_counter;

  if( !( sl & PCAN_USB_STATUSLEN_RTR ) )
  {
    memcpy( ptr, msg->data, len ); // copy the clamped payload

    ptr += len; // past the payload
    pos += len; // payload bytes written
  }

  //? The host drivers read the echo id byte only after a data frame's payload, so an RTR echo must not carry it.
  if( ( msg->flags & CAN_FLAG_ECHO ) && !( sl & PCAN_USB_STATUSLEN_RTR ) ) // echo id byte follows data frames only
  {
    pack_u8( ptr, msg->dummy );
    ptr += 1;
    pos += 1;
  }

  /* add new record */
  pcan_record_buffer_commit( prec, pos );
}

static void pcan_rx_message( can_message_t *msg )
{
  if( !pcan_device.bus_active )
    return;
  if( ( msg->flags & CAN_FLAG_ECHO ) == 0 )
  {
    pcan_led_set_mode( LED_CH0_RX, LED_MODE_BLINK_FAST, 237 );
  }

  pcan_rx_can_frame( &pcan_records, msg );
}

/**
 * @brief Latch CAN controller error events for the next error record.
 * @param[in] err    CAN_ERROR_FLAG_* events from the controller.
 * @param[in] rx_err Receive error counter.
 * @param[in] tx_err Transmit error counter.
 */
static void pcan_can_error( uint8_t err, uint8_t rx_err, uint8_t tx_err )
{
  pcan_device.can.rx_err = rx_err;
  pcan_device.can.tx_err = tx_err;

  if( err & CAN_ERROR_FLAG_BUSOFF )
  {
    pcan_device.can.err |= PCAN_USB_ERROR_BUS_OFF;
  }

  if( err & CAN_ERROR_FLAG_RX_OVF )
  {
    pcan_device.can.err |= PCAN_USB_ERROR_RXQOVR; // the controller's RX FIFO dropped frames
  }
}

/**
 * @brief Decode one CAN frame record from the host and queue it for transmission.
 * @param[in] ptr   Record bytes after its status/length byte.
 * @param[in] size  Bytes left in the USB packet from @p ptr on.
 * @param[in] flags The record's status/length byte.
 * @return Bytes left in the packet after this record; 0 when the bus is off or the record is truncated.
 * @note A frame that finds the TX FIFO full is dropped and latches PCAN_USB_ERROR_TXFULL; see pcan_protocol_data_ready().
 * @note A requested echo is sent only once the frame completed on the bus; a single-shot frame that fails is never echoed.
 */
static uint8_t pcan_decode_data_frame( uint8_t *ptr, uint16_t size, uint8_t flags  )
{
  can_message_t msg = { 0 };
  uint8_t rec_len = flags & PCAN_USB_STATUSLEN_DLC;
  /* return back frame to PC */
  uint8_t srr_flag = 0;
  uint8_t at_flag = 0; // host asked for a single-shot transmission

  if( !pcan_device.bus_active )
    return 0;

  if( flags & PCAN_USB_STATUSLEN_EXT_ID )
  {
    uint32_t id;
    if( size < sizeof( uint32_t ) )
      return 0;
    id = unpack_u32( ptr );
    srr_flag = id & PCAN_USB_TX_SRR; // echo requested
    at_flag = id & PCAN_USB_TX_AT; // single shot requested
    id >>= 3;

    msg.flags = CAN_FLAG_EXTID;
    msg.id = id;

    ptr += sizeof( uint32_t );
    size -= sizeof( uint32_t );
  }
  else
  {
    uint16_t id;
    if( size < sizeof( uint16_t ) )
      return 0;
    id = unpack_u16( ptr );
    srr_flag = id & PCAN_USB_TX_SRR; // echo requested
    at_flag = id & PCAN_USB_TX_AT; // single shot requested
    id >>= 5;

    msg.id = id;

    ptr += sizeof( uint16_t );
    size -= sizeof( uint16_t );
  }

  /* check for MAX DLC */
  msg.dlc = rec_len < 8 ? rec_len: 8;

  if( !( flags & PCAN_USB_STATUSLEN_RTR ) )
  {
    //? The host sends at most 8 data bytes, and an RTR record sends none, so only a data frame's payload is checked and skipped.
    if( size < msg.dlc )
      return 0; // truncated payload

    memcpy( msg.data, ptr, msg.dlc );

    ptr  += msg.dlc; // past the payload
    size -= msg.dlc; // payload bytes consumed
  }
  else
  {
    msg.flags |= CAN_FLAG_RTR;
  }

  /* self receive flag ? */
  if( srr_flag )
  {
    if( size < sizeof( uint8_t ) )
      return 0;
    
    msg.dummy = unpack_u8( ptr );

    ptr  += sizeof( uint8_t );
    size -= sizeof( uint8_t );
  }

  //- Carry the host's echo and single-shot requests with the queued frame
  if( srr_flag )
    msg.flags |= CAN_FLAG_ECHO; // echoed from HAL_CAN_TxMailbox0CompleteCallback() once the frame is on the bus
  if( at_flag )
    msg.flags |= CAN_FLAG_ONESHOT; // one attempt, no automatic retransmission

  msg.timestamp = pcan_timestamp_ticks();

  pcan_led_set_mode( LED_CH0_TX, LED_MODE_BLINK_FAST, 237 );

  //? pcan_protocol_data_ready() pauses the data endpoint before the FIFO can fill, so a full FIFO here means a packet slipped past that pause.
  if( pcan_can_send_message( &msg ) < 0 )
  {
    /* tx buffer overflow, drop all data */
    pcan_device.can.err |= PCAN_USB_ERROR_TXFULL;
    return size;
  }
  return size;
}

static void pcan_set_bitrate( uint8_t *ptr )
{
  /* decode BTR1 */
  volatile uint8_t tseg1 = (ptr[0] & 0xF) + 1;
  volatile uint8_t tseg2 = ((ptr[0]>>4) & 0x7) + 1;

  /* decode BTR0 */
  volatile uint16_t brp = (ptr[1] & 0x3f) + 1;
  volatile uint8_t sjw = ((ptr[1]>>6) & 0x3) + 1;

  uint32_t bitrate = (((PCAN_USB_CRYSTAL_HZ/2)/brp)/(1/*tq*/ + tseg1 + tseg2 ));
  (void)bitrate;
  
  pcan_can_set_bitrate( brp, tseg1, tseg2, sjw );
}

/**
 * @brief Append an error record: the latched error events plus the bus state last reported to the host.
 * @param[in,out] prec Record buffer the record is appended to.
 * @return 1 when the record is appended, 0 when @p prec has no room for it.
 * @note Only once the record is appended are the latched events in pcan_device.can.err cleared and the reported state saved in err_state_sent.
 */
static uint8_t pcan_error_event( PCAN_RECORD_BUFFER_EX *prec )
{
  uint8_t *ptr, n;

  //- Reserve room for the record
  ptr = pcan_record_buffer_request( prec, 3 ); // status, function and number bytes
  if( !ptr )
    return 0; // no room, the latched events wait for the next record

  //- Merge the bus state into the latched events
  //? PCAN-USB has no error-passive bit: PCAN_USB_ERROR_BUS_HEAVY covers both warning and passive.
  //? The host tells them apart by the error counters pcan_bus_event() sends.
  n = pcan_device.can.err; // events latched since the last record
  if( pcan_device.can.bus_state == CAN_BUS_STATE_OFF )
  {
    n |= PCAN_USB_ERROR_BUS_OFF; // controller is bus-off
  }
  else if( pcan_device.can.bus_state != CAN_BUS_STATE_ACTIVE )
  {
    n |= PCAN_USB_ERROR_BUS_HEAVY; // an error counter is at or above 96
  }

  //- Pack and commit the record
  pack_u8( &ptr[0], PCAN_USB_STATUSLEN_INTERNAL ); // internal record, no data bytes
  pack_u8( &ptr[1], PCAN_USB_REC_ERROR ); // function: error status
  pack_u8( &ptr[2], n ); // number: PCAN_USB_ERROR_* bits
  pcan_record_buffer_commit( prec, 3 ); // publish the record
  pcan_device.can.err = 0x00; // latched events are reported
  pcan_device.can.err_state_sent = pcan_device.can.bus_state; // the host now knows this state
  return 1; // record appended
}

/**
 * @brief Append a timesync record, followed by an error record when there is something to report.
 * @param[in,out] prec Record buffer the records are appended to.
 * @note The error record goes out only with latched events or a bus state the host has not been told yet.
 */
static void pcan_timesync_event( PCAN_RECORD_BUFFER_EX *prec )
{
  uint8_t sl = PCAN_USB_STATUSLEN_INTERNAL | 2 /* record data size */;
  uint8_t *ptr, pos = 0;

  ptr = pcan_record_buffer_request( prec, 5 );
  if( !ptr )
    return;
  
  uint16_t timestamp16 = pcan_timestamp_ticks();

  pack_u8( ptr, sl );
  ptr += 1;
  pos += 1;

  /* function */
  pack_u8( ptr, PCAN_USB_REC_TS );
  ptr += 1;
  pos += 1;

  /* number */
  pack_u8( ptr, 1 );
  ptr += 1;
  pos += 1;

  /* timestamp */
  pack_u16( ptr, timestamp16 );
  ptr += 2;
  pos += 2;

  /* add new record */
  pcan_record_buffer_commit( prec, pos );

  //? The Linux CAN Driver posts an error frame for every error record, so a record with nothing new would show up as noise once a second.
  if( pcan_device.can.err || ( pcan_device.can.bus_state != pcan_device.can.err_state_sent ) )
    pcan_error_event( prec ); // latched events, or a state change a full buffer kept from the host
}

/**
 * @brief Report a change of the CAN bus state to the host.
 * @param[in,out] prec Record buffer the records are appended to.
 * @note A bus event record that finds @p prec full is retried on the next call; a lost error record is repeated by the next pcan_timesync_event().
 */
static void pcan_bus_state_poll( PCAN_RECORD_BUFFER_EX *prec )
{
  can_bus_status_t status;
  uint8_t flags;

  //- Sample the controller state and error counters
  pcan_can_get_bus_status( &status ); // read the CAN error status register
  pcan_device.can.rx_err = status.rx_err; // receive error counter for pcan_bus_event()
  pcan_device.can.tx_err = status.tx_err; // transmit error counter for pcan_bus_event()
  if( status.state == pcan_device.can.bus_state )
    return; // state unchanged, nothing to report

  //- Send the error counters when the host asked for them
  //? The host reads the counters while it decodes the error record, so the bus event record has to come first.
  if( pcan_device.can.err_mask & ( PCAN_USB_ERR_RXERR | PCAN_USB_ERR_TXERR ) )
  {
    flags = ( status.state > pcan_device.can.bus_state ) ? PCAN_USB_ERR_CNT_INC : PCAN_USB_ERR_CNT_DEC; // a worse state means the counters rose
    if( !pcan_bus_event( prec, flags ) )
      return; // no room, retry on the next poll
  }

  //- Report the new state now instead of at the next timesync
  pcan_device.can.bus_state = status.state; // state the host is told about
  pcan_error_event( prec ); // send the new state
}

void pcan_protocol_process_command( uint8_t *ptr, uint16_t size )
{
  PCAN_USB_PARAM *cmd = (void*)ptr;

  if( size < PCAN_USB_MSG_HEADER_LEN )
    return;

  switch( cmd->number )
  {
    case PCAN_USB_EX0:
      switch( cmd->function )
      {
        /* set mass storage mode */
        case PCAN_USB_SETCAN2FLASH:
        break;
      }
    break;
    case PCAN_USB_EX3:
      switch( cmd->function )
      {
        /*  CAN silient control*/
        case PCAN_USB_SET_SILENT_MODE:
          pcan_device.can.silient = cmd->param[0];
          pcan_can_set_silent( pcan_device.can.silient );
        break;
      }
    break;
    case PCAN_USB_SET:
      switch( cmd->function )
      {
        /* BTR0 BTR1 */
        case PCAN_USB_CMD_BITRATE:
          pcan_device.can.btr0 = cmd->param[1];
          pcan_device.can.btr1 = cmd->param[0];
          pcan_set_bitrate( cmd->param );
        break;
        /* set CAN on/off*/
        case PCAN_USB_CMD_BUS:
          //? A failed start leaves the controller off the bus, so the channel counts as on only once the controller joined.
          pcan_device.bus_active = ( pcan_can_set_bus_active( cmd->param[0] ) == 0 ) && cmd->param[0]; // on only when the controller joined
          pcan_device.can.err = 0x00; // a new session starts without latched errors
          pcan_led_set_mode( LED_STAT, pcan_device.bus_active ? LED_MODE_BLINK_SLOW:LED_MODE_OFF, 0 );

          if( pcan_device.bus_active )
          {
            pcan_device.can.bus_state = CAN_BUS_STATE_ACTIVE; // host starts the channel error active
            pcan_device.can.err_state_sent = 0xFF; // the first timesync carries an error record
            /* provide first timesync event */
            pcan_device.last_timestamp_sync = pcan_timestamp_ticks();
            pcan_timesync_event( &pcan_records );
          }
          /* led state */
          pcan_led_set_mode( LED_CH0_TX, pcan_device.bus_active ? LED_MODE_OFF:LED_MODE_ON, 0 );
          pcan_led_set_mode( LED_CH0_RX, pcan_device.bus_active ? LED_MODE_OFF:LED_MODE_ON, 0 );
        break;
        /* set device id 0-255 */
        case PCAN_USB_CMD_DEVID:
          pcan_device.device_id = cmd->param[0];
        break;
        /* unknown windows driver call */
        case PCAN_USB_CMD_CFG:
        break;
        /* sja1000 reg write */
        case PCAN_USB_CMD_REGISTER:
          pcan_sja1000_write( cmd->param[0], cmd->param[1] );
        break;
        /* ext VCC on/off */
        case PCAN_USB_CMD_EXT_VCC:
          pcan_device.ext_vcc_state = cmd->param[0];
        break;
        /* error frame mask */
        case PCAN_USB_CMD_ERR_FR:
          //? The mask asks for bus event records (see pcan_bus_state_poll()); it never filters the error record.
          pcan_device.can.err_mask = cmd->param[0]; // PCAN_USB_ERR_* bits the host wants
        break;
        /* set led mode */
        case PCAN_USB_CMD_LED:
          pcan_device.led_mode = cmd->param[0];
        break;
      }
    break;
    case PCAN_USB_GET:
      switch( cmd->function )
      {
        /* BTR0 BTR1 */
        case PCAN_USB_CMD_BITRATE:
          cmd->param[1] = pcan_device.can.btr0;
          cmd->param[0] = pcan_device.can.btr1;
          pcan_usb_send_command_buffer( cmd, sizeof( PCAN_USB_PARAM ) );
        break;
        /* quarts freq in Mhz */
        case PCAN_USB_CMD_CLOCK:
        {
          cmd->param[0] = pcan_device.can.quartz_freq/1000000u;
          pcan_usb_send_command_buffer( cmd, sizeof( PCAN_USB_PARAM ) );
        }
        break;
        /* get device id */
        case PCAN_USB_CMD_DEVID:
        {
          /* default: 255 */
          cmd->param[0] = pcan_device.device_id;
          pcan_usb_send_command_buffer( cmd, sizeof( PCAN_USB_PARAM ) );
        }
        break;
        /* serial */
        case PCAN_USB_CMD_SN:
        {
          /* default: 4 bytes FFFFFFFF */
          cmd->param[0] = (pcan_device.device_serial>>0x18)&0xFF;
          cmd->param[1] = (pcan_device.device_serial>>0x10)&0xFF;
          cmd->param[2] = (pcan_device.device_serial>>0x08)&0xFF;
          cmd->param[3] = (pcan_device.device_serial>>0x00)&0xFF;
          pcan_usb_send_command_buffer( cmd, sizeof( PCAN_USB_PARAM ) );
        }
        break;
        /* sja1000 reg read */
        case PCAN_USB_CMD_REGISTER:
          cmd->param[1] = pcan_sja1000_read( cmd->param[0] );
          pcan_usb_send_command_buffer( cmd, sizeof( PCAN_USB_PARAM ) );
        break;
        /* get led */
        case PCAN_USB_CMD_LED:
          cmd->param[0] = pcan_device.led_mode;
          pcan_usb_send_command_buffer( cmd, sizeof( PCAN_USB_PARAM ) );
        break;
      }
    break;
    default:
      assert( 0 );
    break;
  }  
}

void pcan_protocol_process_data( uint8_t *ptr, uint16_t size )
{
  uint8_t msg_prefix, msg_rec_count;

  /* invalid frame header len ? */
  if( size < PCAN_USB_MSG_HEADER_LEN )
    return;

  msg_prefix = *ptr++;
  msg_rec_count = *ptr++;

  (void)msg_prefix; /* avoid warning */

  size -= PCAN_USB_MSG_HEADER_LEN;

  for( uint8_t i = 0; i < msg_rec_count; i++ )
  {
    uint16_t eated = 0, sl;

    if( size < 1 )
      return;

    sl = unpack_u8( ptr );
    ptr += sizeof( uint8_t );
    size -= sizeof( uint8_t );;

    /* status, error frame */
    if( sl & PCAN_USB_STATUSLEN_INTERNAL )
    {
      /* ??? abnormal, device support only CAN data frame */
      eated = 0;
    }
    /* regular CAN data frame */
    else
    {
      eated = pcan_decode_data_frame( ptr, size, sl );
    }

    /* ? error indicator */
    if( !eated )
      return;
    
    eated = size - eated;

    if( size < eated )
      return;

    ptr += eated;
    size -= eated;
  }
}

//? The smallest record is a status byte plus an 11-bit ID with no data, so a data packet holds at most this many records.
#define DATA_PACKET_RECORDS_MAX ( ( PCAN_USB_TX_BUFFER_SIZE - PCAN_USB_MSG_HEADER_LEN ) / 3u ) // 20 records in a 64-byte packet

uint8_t pcan_protocol_data_ready( void )
{
  //- Packets are dropped while the bus is off, so they never wait
  if( !pcan_device.bus_active )
    return 1; // accept and drop

  //- Room for every record the next packet can hold
  return pcan_can_tx_free() >= DATA_PACKET_RECORDS_MAX; // a full packet fits the TX FIFO
}

void pcan_protocol_init( void )
{
  pcan_device.sja1000_ic_mode = SJA1000_PELICAN;
  pcan_record_buffer_reset( &pcan_records );
  pcan_can_init();
  pcan_can_install_rx_callback( pcan_rx_message  );
  pcan_can_install_error_callback( pcan_can_error );
}

void pcan_protocol_poll( void )
{
  uint16_t ts_ms = pcan_timestamp_ticks();

  if( pcan_device.bus_active  )
  {
    pcan_bus_state_poll( &pcan_records ); // report bus state changes ahead of the periodic error record
    /* each ~1000 ms */
    if( ( ((uint16_t)( ts_ms - pcan_device.last_timestamp_sync )) >= PCAN_TICKS_FROM_US( 1000000u ) ) )
    {
      pcan_device.last_timestamp_sync = ts_ms;
      pcan_timesync_event( &pcan_records );
    }
  }
  pcan_record_buffer_flush( &pcan_records );

  pcan_can_poll();
}
