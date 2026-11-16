# Compiles an Arduino sketch folder in place (no copies), like the Arduino IDE:
# every .c/.cpp in the sketch folder and below (incl. src/), plus the .ino via
# sketch_main.cpp. Pick the sketch with -DSPIKE_SKETCH_DIR=<folder>.
if(NOT DEFINED SPIKE_SKETCH_DIR)
  set(SPIKE_SKETCH_DIR "${CMAKE_CURRENT_LIST_DIR}/../../sketch_multi")
endif()
get_filename_component(SKETCH_DIR "${SPIKE_SKETCH_DIR}" ABSOLUTE)
get_filename_component(SKETCH_NAME "${SKETCH_DIR}" NAME)

file(GLOB_RECURSE SKETCH_SRCS "${SKETCH_DIR}/*.cpp" "${SKETCH_DIR}/*.c")

idf_component_register(
  SRCS "sketch_main.cpp" ${SKETCH_SRCS}
  INCLUDE_DIRS "." "${SKETCH_DIR}")

target_compile_definitions(${COMPONENT_LIB} PRIVATE
  SPIKE_BUILD_PATH="esp-idf"
  SPIKE_SKETCH_INO="${SKETCH_NAME}.ino")
