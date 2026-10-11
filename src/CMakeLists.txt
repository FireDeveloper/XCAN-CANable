#? XCAN firmware for the DykbRadio CANable V1.0 (STM32F072C8T6): configure through a preset, whose gcc-arm-none-eabi.cmake keeps CMake off the host compiler.

cmake_minimum_required(VERSION 3.22) # presets that name a toolchain file
project(xcan_canable C ASM) # firmware sources and the startup file

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release) # the image CI publishes
endif()

#? CMake defaults Release to -O3; the published image is built for size, and the debug image stays steppable in a debugger.
set(CMAKE_C_FLAGS_RELEASE "-Os") # published image
set(CMAKE_C_FLAGS_DEBUG "-Og -g3") # debug image
set(CMAKE_ASM_FLAGS_RELEASE "") # the startup file takes no optimization level
set(CMAKE_ASM_FLAGS_DEBUG "-g3") # line information for the startup file
set(CMAKE_ASM_FLAGS "${CMAKE_ASM_FLAGS} -x assembler-with-cpp") # run the startup file through the C preprocessor
set(CMAKE_C_STANDARD 99) # -std=c99
set(CMAKE_C_STANDARD_REQUIRED ON) # never fall back to another standard
set(CMAKE_C_EXTENSIONS OFF) # c99, not gnu99

#? The link order follows this list, sources first and the startup file last.
add_executable(xcan_canable # firmware image, xcan_canable.elf
  Src/main.c
  Src/usbd_conf.c
  Src/usbd_desc.c
  Src/pcan_usb.c
  Src/pcan_can.c
  Src/pcan_led.c
  Src/pcan_protocol.c
  Src/pcan_timestamp.c
  Src/system_stm32f0xx.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_ll_usb.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_pcd.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_pcd_ex.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_rcc.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_rcc_ex.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_gpio.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_cortex.c
  Drivers/STM32F0xx_HAL_Driver/Src/stm32f0xx_hal_can.c
  Middlewares/ST/STM32_USB_Device_Library/Core/Src/usbd_core.c
  Middlewares/ST/STM32_USB_Device_Library/Core/Src/usbd_ctlreq.c
  Middlewares/ST/STM32_USB_Device_Library/Core/Src/usbd_ioreq.c
  startup_stm32f072xb.s
)
set_target_properties(xcan_canable PROPERTIES SUFFIX ".elf" LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/STM32F072C8Tx_FLASH.ld") # relink when the memory layout changes

target_compile_definitions(xcan_canable PRIVATE USE_HAL_DRIVER STM32F072xB NDEBUG) # HAL configuration, device header, asserts off
target_include_directories(xcan_canable PRIVATE # header search order
  Src
  Drivers/STM32F0xx_HAL_Driver/Inc
  Middlewares/ST/STM32_USB_Device_Library/Core/Inc
  Drivers/CMSIS/Device/ST/STM32F0xx/Include
  Drivers/CMSIS/Include
)
target_compile_options(xcan_canable PRIVATE -mcpu=cortex-m0 -mthumb -Wall -fno-common -fdata-sections -ffunction-sections) # Cortex-M0 Thumb code, a section per function for --gc-sections
target_compile_options(xcan_canable PRIVATE $<$<COMPILE_LANGUAGE:C>:-Wpedantic$<SEMICOLON>-Wextra>) # extra warnings for C only

target_link_options(xcan_canable PRIVATE -mcpu=cortex-m0 -mthumb -specs=nano.specs) # newlib-nano for this core
target_link_options(xcan_canable PRIVATE "-T${CMAKE_CURRENT_SOURCE_DIR}/STM32F072C8Tx_FLASH.ld") # memory layout, with the USB device descriptor at its fixed address
target_link_options(xcan_canable PRIVATE "-Wl,-Map=$<TARGET_FILE_DIR:xcan_canable>/xcan_canable.map,--cref" -Wl,--gc-sections) # link map, unused sections dropped
target_link_libraries(xcan_canable PRIVATE c m nosys) # C and math library, system calls stubbed

add_custom_command(TARGET xcan_canable POST_BUILD # images next to the .elf
  COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:xcan_canable> $<TARGET_FILE_DIR:xcan_canable>/xcan_canable.hex
  COMMAND ${CMAKE_OBJCOPY} -O binary -S $<TARGET_FILE:xcan_canable> $<TARGET_FILE_DIR:xcan_canable>/xcan_canable.bin
  COMMAND ${CMAKE_SIZE} $<TARGET_FILE:xcan_canable>
  BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/xcan_canable.hex" "${CMAKE_CURRENT_BINARY_DIR}/xcan_canable.bin"
  VERBATIM
)
