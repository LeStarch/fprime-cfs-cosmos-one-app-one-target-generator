####
# arch_build.cmake: self-registration as an F Prime library in a cFS mission build
#
# cFS includes `<module>/arch_build.cmake` for every module in the mission app list (`prepare()` in
# cfe/cmake/arch_build.cmake) before any module directory is added. Listing this repository in the mission's
# `MISSION_GLOBAL_APPLIST` therefore puts it on `FPRIME_LIBRARY_LOCATIONS` without further configuration.
# `prepare()` is a function, so the value is exported through the cache; the path is absolute because F Prime
# rewrites the cache entry to absolute paths and a relative entry would be re-appended on every reconfigure.
####
get_filename_component(_FPRIME_CFS_COSMOS_LIBRARY "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
list(APPEND FPRIME_LIBRARY_LOCATIONS "${_FPRIME_CFS_COSMOS_LIBRARY}")
list(REMOVE_DUPLICATES FPRIME_LIBRARY_LOCATIONS)
set(FPRIME_LIBRARY_LOCATIONS "${FPRIME_LIBRARY_LOCATIONS}" CACHE STRING "F Prime library locations" FORCE)
message(STATUS "[cfs_cosmos] registered ${_FPRIME_CFS_COSMOS_LIBRARY} on FPRIME_LIBRARY_LOCATIONS")
