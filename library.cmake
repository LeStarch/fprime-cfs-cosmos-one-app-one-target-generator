####
# library.cmake:
#
# F Prime library manifest. Add this repository to `library_locations` in settings.ini (or
# -DFPRIME_LIBRARY_LOCATIONS=...): the `cfs_cosmos` autocoder (cmake/autocoder/cfs_cosmos.cmake) joins the
# build and `fprime_cfs_cosmos_merge()` becomes available to project CMakeLists.txt files.
#
# Library manifests are included after the framework modules, when `register_fprime_build_autocoder` refuses
# new registrations, so the autocoder is appended to the registry directly in that case. Projects may instead
# call `register_fprime_build_autocoder("<this dir>/cmake/autocoder/cfs_cosmos.cmake" FALSE)` themselves before
# `include(FPrime-Code)`.
####
set(FPRIME_CFS_COSMOS_AUTOCODER "${CMAKE_CURRENT_LIST_DIR}/cmake/autocoder/cfs_cosmos.cmake")
get_property(_FPRIME_CFS_COSMOS_DETECTION_STARTED GLOBAL PROPERTY MODULE_DETECTION SET)
get_property(_FPRIME_CFS_COSMOS_AUTOCODERS GLOBAL PROPERTY FPRIME_AUTOCODER_TARGET_LIST)
if (NOT _FPRIME_CFS_COSMOS_DETECTION_STARTED)
    register_fprime_build_autocoder("${FPRIME_CFS_COSMOS_AUTOCODER}" FALSE)
elseif (NOT FPRIME_CFS_COSMOS_AUTOCODER IN_LIST _FPRIME_CFS_COSMOS_AUTOCODERS)
    include("${FPRIME_CFS_COSMOS_AUTOCODER}")
    set_property(GLOBAL APPEND PROPERTY FPRIME_AUTOCODER_TARGET_LIST "${FPRIME_CFS_COSMOS_AUTOCODER}")
endif()
include("${CMAKE_CURRENT_LIST_DIR}/cmake/FPrimeCfsCosmos.cmake")
