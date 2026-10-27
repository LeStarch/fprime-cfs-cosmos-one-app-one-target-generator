####
# library.cmake:
#
# F Prime library manifest. Add this repository to `library_locations` in settings.ini (or
# -DFPRIME_LIBRARY_LOCATIONS=...) and register the `cfs_cosmos` autocoder from the project CMakeLists.txt
# before the framework code is included, which is the intended F Prime order (as TestDeploymentsProject does):
#
#   set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)
#   find_package(FPrime REQUIRED PATHS "${FPRIME_FRAMEWORK_PATH}")
#   register_fprime_build_autocoder(autocoder/cfs_cosmos FALSE)
#   fprime_setup_included_code()   # or include(FPrime-Code) / add_subdirectory of the framework
#
# Library manifests themselves are included after the framework modules, when registration is refused
# (nasa/fprime#6272 tracks a library setup hook). This manifest therefore only registers when it
# happens to run early, warns when the autocoder is missing, and provides `fprime_cfs_cosmos_merge()`.
####
set(FPRIME_CFS_COSMOS_AUTOCODER "${CMAKE_CURRENT_LIST_DIR}/cmake/autocoder/cfs_cosmos.cmake")
get_property(_FPRIME_CFS_COSMOS_DETECTION_STARTED GLOBAL PROPERTY MODULE_DETECTION SET)
get_property(_FPRIME_CFS_COSMOS_AUTOCODERS GLOBAL PROPERTY FPRIME_AUTOCODER_TARGET_LIST)
set(_FPRIME_CFS_COSMOS_REGISTERED FALSE)
foreach(_FPRIME_CFS_COSMOS_ENTRY IN LISTS _FPRIME_CFS_COSMOS_AUTOCODERS)
    if (_FPRIME_CFS_COSMOS_ENTRY MATCHES "autocoder/cfs_cosmos(\\.cmake)?$")
        set(_FPRIME_CFS_COSMOS_REGISTERED TRUE)
    endif()
endforeach()
if (_FPRIME_CFS_COSMOS_REGISTERED)
    # Registered by the project before the framework code: nothing to do
elseif (NOT _FPRIME_CFS_COSMOS_DETECTION_STARTED)
    register_fprime_build_autocoder("${FPRIME_CFS_COSMOS_AUTOCODER}" FALSE)
else()
    message(WARNING "[cfs_cosmos] autocoder not registered; cfs-cosmos-flag files will not be handled. Add\n"
                    "    set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)\n"
                    "    find_package(FPrime ...)\n"
                    "    register_fprime_build_autocoder(autocoder/cfs_cosmos FALSE)\n"
                    "    fprime_setup_included_code()\n"
                    "to the project CMakeLists.txt that loads F Prime.")
endif()
include("${CMAKE_CURRENT_LIST_DIR}/cmake/FPrimeCfsCosmos.cmake")
