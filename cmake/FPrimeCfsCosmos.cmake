####
# FPrimeCfsCosmos.cmake:
#
# `fprime_cfs_cosmos_merge()`: a build target that merges the bundles produced by the `cfs_cosmos` autocoder
# (see autocoder/cfs_cosmos.cmake) into an existing cFS COSMOS plugin tree with `fprime-cfs-cosmos-merge`.
# Included by library.cmake; call the function from any CMakeLists.txt:
#
#   fprime_cfs_cosmos_merge(PLUGIN_DIR "${CMAKE_SOURCE_DIR}/../my-cfs-cosmos-plugin" INTERFACE CFS_INTF)
#
# The target (`cfs_cosmos_merge` or NAME) is finished once the top-level directory has been processed, so the
# call may precede the deployments. Build it with `make cfs_cosmos_merge` or pass ALL.
####
include_guard(GLOBAL)

find_program(FPRIME_CFS_COSMOS_MERGER fprime-cfs-cosmos-merge)
if (NOT FPRIME_CFS_COSMOS_MERGER)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(FPRIME_CFS_COSMOS_MERGER "${Python3_EXECUTABLE}" -m fprime_cfs_cosmos_generator.merge_cli)
endif()
set(FPRIME_CFS_COSMOS_OUTPUT "${CMAKE_BINARY_DIR}/cfs-cosmos" CACHE PATH "Where deployment bundles are generated")

####
# Function `fprime_cfs_cosmos_merge`:
#
# - **PLUGIN_DIR:** (required) plugin source tree containing plugin.txt
# - **NAME:** target name (default `cfs_cosmos_merge`)
# - **INTERFACE:** INTERFACE name passed as `--interface`
# - **RESERVED_MANIFEST:** manifests passed as `--reserved-manifest` (repeatable)
# - **DEPLOYMENTS:** restrict to these deployment names (default: all flag files seen)
# - **SKIP_TARGET**, **FORCE**, **DRY_RUN:** forwarded flags
# - **ALL:** make the merge part of the default build
####
function(fprime_cfs_cosmos_merge)
    cmake_parse_arguments(MERGE "SKIP_TARGET;FORCE;DRY_RUN;ALL" "PLUGIN_DIR;NAME;INTERFACE" "RESERVED_MANIFEST;DEPLOYMENTS" ${ARGN})
    if (NOT MERGE_PLUGIN_DIR)
        message(FATAL_ERROR "fprime_cfs_cosmos_merge requires PLUGIN_DIR")
    endif()
    if (NOT MERGE_NAME)
        set(MERGE_NAME cfs_cosmos_merge)
    endif()
    if (FPRIME_IS_SUB_BUILD)
        return()
    endif()
    set(FLAGS)
    foreach(FLAG IN ITEMS SKIP_TARGET FORCE DRY_RUN)
        if (MERGE_${FLAG})
            string(TOLOWER "${FLAG}" LOWER)
            string(REPLACE "_" "-" LOWER "${LOWER}")
            list(APPEND FLAGS "--${LOWER}")
        endif()
    endforeach()
    if (MERGE_INTERFACE)
        list(APPEND FLAGS --interface "${MERGE_INTERFACE}")
    endif()
    foreach(MANIFEST IN LISTS MERGE_RESERVED_MANIFEST)
        list(APPEND FLAGS --reserved-manifest "${MANIFEST}")
    endforeach()
    set(ALL_KEYWORD "")
    if (MERGE_ALL)
        set(ALL_KEYWORD ALL)
    endif()
    # Deferred-call arguments are re-evaluated when the call runs, so hand the settings over through global
    # properties and defer one argument-free finalizer to the end of the top-level directory.
    set_property(GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${MERGE_NAME}_PLUGIN_DIR" "${MERGE_PLUGIN_DIR}")
    set_property(GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${MERGE_NAME}_ALL" "${ALL_KEYWORD}")
    set_property(GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${MERGE_NAME}_FLAGS" "${FLAGS}")
    set_property(GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${MERGE_NAME}_DEPLOYMENTS" "${MERGE_DEPLOYMENTS}")
    set_property(GLOBAL APPEND PROPERTY FPRIME_CFS_COSMOS_MERGE_NAMES "${MERGE_NAME}")
    get_property(SCHEDULED GLOBAL PROPERTY FPRIME_CFS_COSMOS_MERGE_SCHEDULED)
    if (NOT SCHEDULED)
        set_property(GLOBAL PROPERTY FPRIME_CFS_COSMOS_MERGE_SCHEDULED TRUE)
        cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL _fprime_cfs_cosmos_merge_finalize_all)
    endif()
endfunction(fprime_cfs_cosmos_merge)

function(_fprime_cfs_cosmos_merge_finalize_all)
    get_property(NAMES GLOBAL PROPERTY FPRIME_CFS_COSMOS_MERGE_NAMES)
    foreach(NAME IN LISTS NAMES)
        _fprime_cfs_cosmos_merge_finalize("${NAME}")
    endforeach()
endfunction(_fprime_cfs_cosmos_merge_finalize_all)

function(_fprime_cfs_cosmos_merge_finalize NAME)
    get_property(PLUGIN_DIR GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${NAME}_PLUGIN_DIR")
    get_property(ALL_KEYWORD GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${NAME}_ALL")
    get_property(FLAGS GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${NAME}_FLAGS")
    get_property(DEPLOYMENTS GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${NAME}_DEPLOYMENTS")
    get_property(BUNDLE_DIRS GLOBAL PROPERTY FPRIME_CFS_COSMOS_BUNDLE_DIRS)
    get_property(BUNDLE_TARGETS GLOBAL PROPERTY FPRIME_CFS_COSMOS_TARGETS)
    if (DEPLOYMENTS)
        set(BUNDLE_DIRS)
        set(BUNDLE_TARGETS)
        foreach(DEPLOYMENT IN LISTS DEPLOYMENTS)
            list(APPEND BUNDLE_DIRS "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}")
            list(APPEND BUNDLE_TARGETS "${DEPLOYMENT}_cfs_cosmos")
        endforeach()
    endif()
    if (NOT BUNDLE_DIRS)
        message(WARNING "fprime_cfs_cosmos_merge(${NAME}): no module lists a <Deployment>.cfs-cosmos flag file")
        return()
    endif()
    if (NOT ALL_KEYWORD STREQUAL "ALL")
        set(ALL_KEYWORD)
    endif()
    add_custom_target("${NAME}" ${ALL_KEYWORD}
        COMMAND ${FPRIME_CFS_COSMOS_MERGER} --plugin "${PLUGIN_DIR}" ${FLAGS} ${BUNDLE_DIRS}
        DEPENDS ${BUNDLE_TARGETS}
        COMMENT "Merging cFS COSMOS bundles into ${PLUGIN_DIR}"
        VERBATIM
    )
endfunction(_fprime_cfs_cosmos_merge_finalize)
