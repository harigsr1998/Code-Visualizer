# Path B: ESP-IDF project that builds ../sketch_multi with arduino-esp32 as a
# managed component. One lock file per chip, so switching targets doesn't
# rewrite a shared dependencies.lock.
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# Must be a build property (a plain CMake variable is ignored); set it after
# project.cmake and before project().
idf_build_set_property(DEPENDENCIES_LOCK "${CMAKE_CURRENT_LIST_DIR}/dependencies.lock.${IDF_TARGET}")
project(spike_idf_arduino)
