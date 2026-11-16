# Compiles the SAME files as path A, straight from the sketch folder (no copies).
set(SKETCH_DIR "${CMAKE_CURRENT_LIST_DIR}/../../sketch_multi")

idf_component_register(
  SRCS "sketch_main.cpp"
       "${SKETCH_DIR}/counter.cpp"
       "${SKETCH_DIR}/src/mathutil/fib.cpp"
  INCLUDE_DIRS "." "${SKETCH_DIR}")

target_compile_definitions(${COMPONENT_LIB} PRIVATE SPIKE_BUILD_PATH="esp-idf")
