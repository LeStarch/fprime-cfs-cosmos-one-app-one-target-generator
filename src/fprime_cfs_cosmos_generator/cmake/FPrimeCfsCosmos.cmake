####
# FPrimeCfsCosmos.cmake:
#
# Runs `fprime-to-cfs-cosmos-target` on every F Prime deployment's topology dictionary as part of the build
# and offers `fprime_cfs_cosmos_merge()` to fold the resulting bundles into an existing cFS COSMOS plugin.
#
# Include it from any CMakeLists.txt once F Prime is loaded (ordering does not matter: the deployments are
# discovered after the whole tree has been processed):
#
#   execute_process(COMMAND fprime-to-cfs-cosmos-target --cmake-dir
#                   OUTPUT_VARIABLE FPRIME_CFS_COSMOS_CMAKE_DIR OUTPUT_STRIP_TRAILING_WHITESPACE)
#   include("${FPRIME_CFS_COSMOS_CMAKE_DIR}/FPrimeCfsCosmos.cmake")
#   fprime_cfs_cosmos_merge(PLUGIN_DIR "${CMAKE_SOURCE_DIR}/../my-cfs-cosmos-plugin" INTERFACE CFS_INTF)
#
# Every module that autocodes a `<DEPLOYMENT>TopologyDictionary.json` (a deployment, or the topology module of
# an F Prime app hosted in cFS) gets `${CMAKE_BINARY_DIR}/cfs-cosmos/<DEPLOYMENT>/<TARGET>/` and a
# `<DEPLOYMENT>_cfs_cosmos` target the module depends on (a normal build produces the bundles);
# `cfs_cosmos` builds them all.
# Extra generator arguments: FPRIME_CFS_COSMOS_ARGS (all deployments) and FPRIME_CFS_COSMOS_ARGS_<DEPLOYMENT>.
####
include_guard(GLOBAL)

# F Prime's info-cache/prescan sub-builds have no autocoder support: define the API so project files parse,
# but only attach generator/merge targets in the real build.
function(_fprime_cfs_cosmos_is_sub_build OUTPUT)
    if (DEFINED FPRIME_SUB_BUILD_TARGETS OR FPRIME_IS_SUB_BUILD)
        set("${OUTPUT}" TRUE PARENT_SCOPE)
    else()
        set("${OUTPUT}" FALSE PARENT_SCOPE)
    endif()
endfunction(_fprime_cfs_cosmos_is_sub_build)

find_program(FPRIME_CFS_COSMOS_GENERATOR fprime-to-cfs-cosmos-target)
find_program(FPRIME_CFS_COSMOS_MERGER fprime-cfs-cosmos-merge)
if (NOT FPRIME_CFS_COSMOS_GENERATOR OR NOT FPRIME_CFS_COSMOS_MERGER)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(FPRIME_CFS_COSMOS_GENERATOR "${Python3_EXECUTABLE}" -m fprime_cfs_cosmos_generator.cli)
    set(FPRIME_CFS_COSMOS_MERGER "${Python3_EXECUTABLE}" -m fprime_cfs_cosmos_generator.merge_cli)
endif()
set(FPRIME_CFS_COSMOS_ARGS "" CACHE STRING "Extra fprime-to-cfs-cosmos-target arguments for every deployment")
set(FPRIME_CFS_COSMOS_OUTPUT "${CMAKE_BINARY_DIR}/cfs-cosmos" CACHE PATH "Where deployment bundles are generated")

# Deployments register throughout the tree; attach the generator once the top-level directory is complete.
_fprime_cfs_cosmos_is_sub_build(FPRIME_CFS_COSMOS_SUB_BUILD)
if (NOT FPRIME_CFS_COSMOS_SUB_BUILD)
    cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL _fprime_cfs_cosmos_setup_deployments)
endif()

function(_fprime_cfs_cosmos_add_deployment MODULE)
    # The fpp autocoder records dictionaries it generates for a module in FPRIME_DICTIONARIES (dictionary target)
    get_target_property(DICTIONARIES "${MODULE}" FPRIME_DICTIONARIES)
    set(DICTIONARY "")
    foreach(GENERATED IN LISTS DICTIONARIES)
        if (GENERATED MATCHES ".*TopologyDictionary\\.json$")
            set(DICTIONARY "${GENERATED}")
        endif()
    endforeach()
    if (NOT DICTIONARY)
        return()
    endif()
    get_filename_component(DEPLOYMENT "${DICTIONARY}" NAME_WE)
    string(REGEX REPLACE "TopologyDictionary$" "" DEPLOYMENT "${DEPLOYMENT}")
    if (TARGET "${DEPLOYMENT}_cfs_cosmos")
        return()
    endif()
    set(OUTPUT_DIR "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}")
    set(STAMP "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}.stamp")
    add_custom_command(
        OUTPUT "${STAMP}"
        COMMAND ${FPRIME_CFS_COSMOS_GENERATOR} --dictionary "${DICTIONARY}" --output "${OUTPUT_DIR}"
            ${FPRIME_CFS_COSMOS_ARGS} ${FPRIME_CFS_COSMOS_ARGS_${DEPLOYMENT}}
        COMMAND "${CMAKE_COMMAND}" -E touch "${STAMP}"
        DEPENDS "${DICTIONARY}"
        COMMENT "Generating cFS COSMOS target bundle for ${DEPLOYMENT}"
        VERBATIM
    )
    add_custom_target("${DEPLOYMENT}_cfs_cosmos" DEPENDS "${STAMP}")
    add_dependencies("${MODULE}" "${DEPLOYMENT}_cfs_cosmos")
    add_dependencies(cfs_cosmos "${DEPLOYMENT}_cfs_cosmos")
    set_property(GLOBAL APPEND PROPERTY FPRIME_CFS_COSMOS_BUNDLE_DIRS "${OUTPUT_DIR}")
    set_property(GLOBAL APPEND PROPERTY FPRIME_CFS_COSMOS_TARGETS "${DEPLOYMENT}_cfs_cosmos")
    message(STATUS "cFS COSMOS bundle: ${DEPLOYMENT} (${MODULE}) -> ${OUTPUT_DIR}")
endfunction(_fprime_cfs_cosmos_add_deployment)

function(_fprime_cfs_cosmos_setup_deployments)
    if (NOT TARGET cfs_cosmos)
        add_custom_target(cfs_cosmos)
    endif()
    # The topology module (<Deployment>_Top) carries the dictionary both in plain F Prime builds and cFS-hosted apps.
    get_property(MODULES GLOBAL PROPERTY FPRIME_MODULES)
    foreach(MODULE IN LISTS MODULES)
        if (TARGET "${MODULE}")
            _fprime_cfs_cosmos_add_deployment("${MODULE}")
        endif()
    endforeach()
endfunction(_fprime_cfs_cosmos_setup_deployments)

####
# Function `fprime_cfs_cosmos_merge`:
#
# Adds a build target that merges generated deployment bundles into an existing cFS COSMOS plugin tree.
#
# - **PLUGIN_DIR:** (required) plugin source tree containing plugin.txt
# - **NAME:** target name (default `cfs_cosmos_merge`)
# - **INTERFACE:** INTERFACE name passed as `--interface`
# - **RESERVED_MANIFEST:** manifests passed as `--reserved-manifest` (repeatable)
# - **DEPLOYMENTS:** restrict to these deployment names (default: all)
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
    _fprime_cfs_cosmos_is_sub_build(SUB_BUILD)
    if (SUB_BUILD)
        return()
    endif()
    # Deferred-call arguments are re-evaluated when the call runs, so hand the settings over through global
    # properties and defer a single argument-free finalizer. It runs after _fprime_cfs_cosmos_setup_deployments
    # (deferred calls execute in registration order).
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
        message(WARNING "fprime_cfs_cosmos_merge(${NAME}): no deployment produced a cFS COSMOS bundle")
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
