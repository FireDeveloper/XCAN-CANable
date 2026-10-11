#include <assert.h>
#include <stm32f0xx_hal.h>

#define TIM_BUS_FREQ (48000000)

void pcan_timestamp_init( void )
{

  switch( TIM_BUS_FREQ )
  {
    case 48000000:
      /* TIM3 on APB1 bus */
      __HAL_RCC_TIM3_CLK_ENABLE();

      TIM3->PSC = (2048-1); /* => tick = 42.666uS */
      //? PSC is preloaded; without an update event TIM3 counts unscaled until its first overflow.
      TIM3->EGR = TIM_EGR_UG; // load the prescaler now and restart the count
      /* set clock division to zero: */
      TIM3->CR1 &= (uint16_t)(~TIM_CR1_CKD);
      TIM3->CR1 |= TIM_CLOCKDIVISION_DIV1;
      /* enable timer */
      TIM3->CR1 |= TIM_CR1_CEN;
    break;
    default:
      assert( 0 );
    break;
  }
}

uint16_t pcan_timestamp_millis( void )
{
  return (HAL_GetTick()&0xFFFF);
}

uint16_t pcan_timestamp_ticks( void )
{
  /* 1 pcan tick => 42.666 us */
  return TIM3->CNT;
}
