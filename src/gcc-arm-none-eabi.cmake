#? The tools are named without a path because each build supplies its own on PATH: CI installs Ubuntu's gcc-arm-none-eabi, the STM32Cube extension its bundle.

set(CMAKE_SYSTEM_NAME Generic) # bare metal, no operating system
set(CMAKE_SYSTEM_PROCESSOR arm) # Cortex-M0

set(CMAKE_C_COMPILER arm-none-eabi-gcc) # C sources
set(CMAKE_ASM_COMPILER arm-none-eabi-gcc) # the startup file, through the C preprocessor
set(CMAKE_OBJCOPY arm-none-eabi-objcopy) # .hex and .bin images from the .elf
set(CMAKE_SIZE arm-none-eabi-size) # memory use after each link

#? CMake checks a compiler by linking a test program, which cannot link without the firmware's linker script.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY) # check the compiler without linking
