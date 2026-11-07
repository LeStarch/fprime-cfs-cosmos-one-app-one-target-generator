####
# FPrimeCfsCosmos.cmake:
#
# `fprime_cfs_cosmos_merge()`: a build target that merges the bundles produced by the `cfs_cosmos` autocoder
# (see autocoder/cfs_cosmos.cmake) into an existing cFS COSMOS plugin tree with `fprime-cfs-cosmos-merge`.
# Included by library.cmake; call the function from any CMakeLists.txt:
#
#   fprime_cfs_cosmos_merge(PLUGIN_DIR "${CMAKE_SOURCE_DIR}/../my-cfs-cosmos-plugin")
#
# The merge runs as a post-build step of every deployment that produced a bundle: building the deployment (its
# topology module) merges the bundles into the plugin whenever one of them changed. Pass MANUAL to get only the
# `cfs_cosmos_merge` (or NAME) target instead. The target is finished once the top-level directory has been
# processed, so the call may precede the deployments.
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
# - **INTERFACE:** INTERFACE name passed as `--interface` (only needed when plugin.txt has several INTERFACE lines)
# - **RESERVED_MANIFEST:** manifests passed as `--reserved-manifest` (repeatable)
# - **DEPLOYMENTS:** restrict to these deployment names (default: all flag files seen)
# - **SKIP_TARGET**, **DRY_RUN:** forwarded flags
# - **NO_COMPAT_HELPERS:** pass `--no-compat-helpers` (do not install the nasa/cfs-cosmos-plugin helper adapters)
# - **NO_INHERIT_IGNORES:** do not copy the plugin's IGNORE_PARAMETER/IGNORE_ITEM lines into the F Prime target.txt
# - **NO_FORCE:** do not pass `--force`; by default the build replaces bundle content it generated earlier so a
#   changed dictionary updates the plugin (hand-written files are never replaced, with or without --force)
# - **ALL:** make the merge part of the default build (`make` with no target)
# - **MANUAL:** do not hook the merge into the deployments' builds; run it via the target only
####
function(fprime_cfs_cosmos_merge)
    cmake_parse_arguments(MERGE "SKIP_TARGET;FORCE;NO_FORCE;DRY_RUN;ALL;MANUAL;NO_INHERIT_IGNORES;NO_COMPAT_HELPERS" "PLUGIN_DIR;NAME;INTERFACE" "RESERVED_MANIFEST;DEPLOYMENTS" ${ARGN})
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
    if (NOT MERGE_NO_FORCE)
        set(MERGE_FORCE TRUE)
    endif()
    if (MERGE_NO_INHERIT_IGNORES)
        list(APPEND FLAGS --no-inherit-ignores)
    endif()
    if (MERGE_NO_COMPAT_HELPERS)
        list(APPEND FLAGS --no-compat-helpers)
    endif()
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
    set_property(GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${MERGE_NAME}_MANUAL" "${MERGE_MANUAL}")
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
    get_property(MANUAL GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${NAME}_MANUAL")
    get_property(DEPLOYMENTS GLOBAL PROPERTY "FPRIME_CFS_COSMOS_MERGE_${NAME}_DEPLOYMENTS")
    if (NOT DEPLOYMENTS)
        get_property(DEPLOYMENTS GLOBAL PROPERTY FPRIME_CFS_COSMOS_DEPLOYMENTS)
    endif()
    if (NOT DEPLOYMENTS)
        message(WARNING "fprime_cfs_cosmos_merge(${NAME}): no module lists a cfs-cosmos-flag file")
        return()
    endif()
    set(BUNDLE_DIRS)
    set(BUNDLE_STAMPS)
    set(BUNDLE_TARGETS)
    set(OWNERS)
    foreach(DEPLOYMENT IN LISTS DEPLOYMENTS)
        list(APPEND BUNDLE_DIRS "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}")
        list(APPEND BUNDLE_STAMPS "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}.stamp")
        list(APPEND BUNDLE_TARGETS "${DEPLOYMENT}_cfs_cosmos")
        get_property(OWNER GLOBAL PROPERTY "FPRIME_CFS_COSMOS_OWNER_${DEPLOYMENT}")
        if (OWNER)
            list(APPEND OWNERS "${OWNER}")
        endif()
    endforeach()
    if (NOT ALL_KEYWORD STREQUAL "ALL")
        set(ALL_KEYWORD)
    endif()
    # Stamp-driven so the merge re-runs only when a bundle changed, and is a post-build step of the deployments'
    # topology modules (which already depend on their bundles) unless MANUAL was given. The bundle stamps are
    # OUTPUTs of other directories, so the Makefile generator needs the target-level dependencies to order them.
    set(STAMP "${FPRIME_CFS_COSMOS_OUTPUT}/${NAME}.stamp")
    add_custom_command(
        OUTPUT "${STAMP}"
        COMMAND ${FPRIME_CFS_COSMOS_MERGER} --plugin "${PLUGIN_DIR}" ${FLAGS} ${BUNDLE_DIRS}
        COMMAND "${CMAKE_COMMAND}" -E touch "${STAMP}"
        DEPENDS ${BUNDLE_TARGETS} ${BUNDLE_STAMPS}
        COMMENT "Merging cFS COSMOS bundles into ${PLUGIN_DIR}"
        VERBATIM
    )
    add_custom_target("${NAME}" ${ALL_KEYWORD} DEPENDS "${STAMP}")
    if (NOT MANUAL)
        foreach(OWNER IN LISTS OWNERS)
            add_dependencies("${OWNER}" "${NAME}")
        endforeach()
    endif()
endfunction(_fprime_cfs_cosmos_merge_finalize)
