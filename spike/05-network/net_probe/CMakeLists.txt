# Spike step 5 network probe (firmware zone: permissive licence, MIT/Apache-2.0 to be decided).
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(net_probe)
