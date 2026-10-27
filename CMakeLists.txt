####
# CMakeLists.txt: placeholder for cFS mission builds
#
# When this repository is listed in a cFS mission's `MISSION_GLOBAL_APPLIST`, cFS adds it with `add_subdirectory`;
# it builds nothing. The F Prime side is `library.cmake` (loaded by F Prime through FPRIME_LIBRARY_LOCATIONS, which
# `arch_build.cmake` sets) and the autocoder in `cmake/autocoder/cfs_cosmos.cmake`.
####
cmake_minimum_required(VERSION 3.16)
project(fprime_cfs_cosmos NONE)
