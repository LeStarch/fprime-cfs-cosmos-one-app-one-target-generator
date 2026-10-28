####
# autocoder/cfs_cosmos.cmake:
#
# F Prime autocoder producing the cFS COSMOS target bundle of a deployment. It consumes an empty flag file
# named `cfs-cosmos-flag` listed in the AUTOCODER_INPUTS of the module that owns the topology (the module
# whose fpp autocoding produces `<Deployment>TopologyDictionary.json`, i.e. `<Deployment>_Top`):
#
#   register_fprime_module(
#       AUTOCODER_INPUTS
#           "${CMAKE_CURRENT_LIST_DIR}/topology.fpp"
#           "${CMAKE_CURRENT_LIST_DIR}/cfs-cosmos-flag"
#       ...
#
# Running after the fpp autocoder in the same set, it takes the deployment name from that dictionary and runs
# `fprime-to-cfs-cosmos-target --dictionary <json> --output ${CMAKE_BINARY_DIR}/cfs-cosmos/<Deployment>`,
# yielding `<Deployment>_cfs_cosmos` (the module depends on it) and the global `cfs_cosmos` target.
# Extra generator arguments: FPRIME_CFS_COSMOS_ARGS and FPRIME_CFS_COSMOS_ARGS_<Deployment>.
####
include_guard()
include(autocoder/helpers)
autocoder_setup_for_individual_sources()

set(FPRIME_CFS_COSMOS_ARGS "" CACHE STRING "Extra fprime-to-cfs-cosmos-target arguments for every deployment")
set(FPRIME_CFS_COSMOS_OUTPUT "${CMAKE_BINARY_DIR}/cfs-cosmos" CACHE PATH "Where deployment bundles are generated")

####
# `cfs_cosmos_is_supported`:
#
# Handles files named `cfs-cosmos-flag`. Changes to the flag file trigger regeneration of the build.
####
function(cfs_cosmos_is_supported AC_INPUT_FILE)
    get_filename_component(FLAG_NAME "${AC_INPUT_FILE}" NAME)
    if (FLAG_NAME STREQUAL "cfs-cosmos-flag")
        set(IS_SUPPORTED TRUE PARENT_SCOPE)
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${AC_INPUT_FILE}")
    else()
        set(IS_SUPPORTED FALSE PARENT_SCOPE)
    endif()
endfunction(cfs_cosmos_is_supported)

####
# `cfs_cosmos_setup_autocode`:
#
# Attaches the generator command for one flag file. The dictionary comes from the fpp autocoder's declared
# outputs for the same module (`fpp_AUTOCODER_GENERATED_OTHER`), so fpp must run before this autocoder.
####
function(cfs_cosmos_setup_autocode MODULE_NAME AC_INPUT_FILE)
    get_target_property(FPP_OTHER "${MODULE_NAME}" fpp_AUTOCODER_GENERATED_OTHER)
    # fprime < 4.5 lists fpp-to-dict's <System>SystemDictionary.json as an OUTPUT of the fpp-to-cpp command (nasa/fprime#6273);
    # if the dictionary is built before the C++ autocode, make takes that command as up to date and never generates the
    # *Ac.hpp files. Depending on the C++ outputs as well orders the codegen ahead of the dictionary.
    get_target_property(FPP_SOURCES "${MODULE_NAME}" fpp_AUTOCODER_GENERATED_BUILD_SOURCES)
    if (NOT FPP_SOURCES)
        set(FPP_SOURCES "")
    endif()
    get_target_property(DICTIONARIES "${MODULE_NAME}" FPRIME_DICTIONARIES)
    set(DICTIONARY "")
    foreach(CANDIDATE IN LISTS FPP_OTHER DICTIONARIES)
        if (CANDIDATE MATCHES ".*TopologyDictionary\\.json$")
            set(DICTIONARY "${CANDIDATE}")
        endif()
    endforeach()
    if (NOT DICTIONARY)
        message(FATAL_ERROR "[cfs_cosmos] ${AC_INPUT_FILE}: module ${MODULE_NAME} autocodes no <Deployment>TopologyDictionary.json; "
                            "list the flag file in the topology module's AUTOCODER_INPUTS")
    endif()
    get_filename_component(DEPLOYMENT "${DICTIONARY}" NAME)
    string(REGEX REPLACE "TopologyDictionary\\.json$" "" DEPLOYMENT "${DEPLOYMENT}")
    if (NOT FPRIME_CFS_COSMOS_GENERATOR)
        find_program(FPRIME_CFS_COSMOS_GENERATOR fprime-to-cfs-cosmos-target)
        if (NOT FPRIME_CFS_COSMOS_GENERATOR)
            find_package(Python3 COMPONENTS Interpreter REQUIRED)
            set(FPRIME_CFS_COSMOS_GENERATOR "${Python3_EXECUTABLE}" -m fprime_cfs_cosmos_generator.cli)
        endif()
    endif()
    set(OUTPUT_DIR "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}")
    set(STAMP "${FPRIME_CFS_COSMOS_OUTPUT}/${DEPLOYMENT}.stamp")
    add_custom_command(
        OUTPUT "${STAMP}"
        COMMAND ${FPRIME_CFS_COSMOS_GENERATOR} --dictionary "${DICTIONARY}" --output "${OUTPUT_DIR}"
            ${FPRIME_CFS_COSMOS_ARGS} ${FPRIME_CFS_COSMOS_ARGS_${DEPLOYMENT}}
        COMMAND "${CMAKE_COMMAND}" -E touch "${STAMP}"
        DEPENDS ${FPP_SOURCES} "${DICTIONARY}" "${AC_INPUT_FILE}"
        COMMENT "Generating cFS COSMOS target bundle for ${DEPLOYMENT}"
        VERBATIM
    )
    if (NOT TARGET cfs_cosmos)
        add_custom_target(cfs_cosmos)
    endif()
    add_custom_target("${DEPLOYMENT}_cfs_cosmos" DEPENDS "${STAMP}")
    add_dependencies("${MODULE_NAME}" "${DEPLOYMENT}_cfs_cosmos")
    add_dependencies(cfs_cosmos "${DEPLOYMENT}_cfs_cosmos")
    set_property(GLOBAL APPEND PROPERTY FPRIME_CFS_COSMOS_BUNDLE_DIRS "${OUTPUT_DIR}")
    set_property(GLOBAL APPEND PROPERTY FPRIME_CFS_COSMOS_TARGETS "${DEPLOYMENT}_cfs_cosmos")
    set_property(GLOBAL APPEND PROPERTY FPRIME_CFS_COSMOS_DEPLOYMENTS "${DEPLOYMENT}")
    set_property(GLOBAL PROPERTY "FPRIME_CFS_COSMOS_OWNER_${DEPLOYMENT}" "${MODULE_NAME}")
    set(AUTOCODER_GENERATED_OTHER "${STAMP}" PARENT_SCOPE)
endfunction(cfs_cosmos_setup_autocode)
