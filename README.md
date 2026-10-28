# F Prime cFS COSMOS Target Generator

Generates one standalone COSMOS target bundle per invocation for an F Prime
application hosted inside cFS. The generator does not read or modify the
destination cFS COSMOS plugin.

```bash
fprime-to-cfs-cosmos-target \
  --dictionary FPrimeDeploymentTopologyDictionary.json \
  --output build/generated \
  --target-name FPRIME_APP \
  --reserved-manifest build/generated/OTHER_APP/manifest.json
```

The output contains:

```text
FPRIME_APP/
├── targets/FPRIME_APP/
│   ├── target.txt
│   └── cmd_tlm/
├── lib/
├── plugin.fragment.txt
└── manifest.json
```

The external build places these files in its plugin tree, merges the plugin
fragment, deduplicates byte-identical shared runtime files, and performs final
cross-application MID collision checks using the manifests. The shared write
protocol updates the CCSDS length from the serialized command and writes the
cFS XOR checksum through either `SEC_CRC` or `CCSDS_CHECKSUM`.

## Merge bundles into an existing plugin

```bash
fprime-cfs-cosmos-merge --plugin path/to/cfs-cosmos-plugin \
  build/generated/FPRIME_APP build/generated/OTHER_APP \
  [--interface CFS_INTF] [--reserved-manifest ordinary_cfs_mids.json] [--dry-run] [--force]
```

For each bundle the merge copies `targets/<APP>/` and `lib/fprime_<app>_msg_id.rb`, places the shared
runtime files once (they must be byte-identical across bundles), appends the `VARIABLE` lines after the
plugin's existing `VARIABLE`s, inserts `TARGET <APP> <%= ... %>` immediately before the chosen `INTERFACE`
(`--skip-target` if the plugin declares targets itself), and appends `MAP_TARGET <%= ... %>` plus a single
`PROTOCOL WRITE ProtocolCcsdsChecksumCompat` (and `PROTOCOL READ ProtocolFprimeChannelSplitter` for flat
channelized bundles) to the end of that `INTERFACE` block. The interface is
the only `INTERFACE` in `plugin.txt` or the one selected by `--interface`. Nothing else in the plugin is
touched; ordinary cFS targets stay as they are. Target names and message IDs are checked for collisions
across all bundles and `--reserved-manifest` files before anything is written. Re-running the merge is a
no-op; an existing target with different content is an error unless `--force` is given, and `--force` only
ever replaces content this tool generated. All conflicts are detected before anything is written.

The F Prime commands share the plugin's CCSDS header helpers, so the merge appends the `IGNORE_PARAMETER` /
`IGNORE_ITEM` lines found in the plugin's hand-written `targets/*/target.txt` to each F Prime `target.txt`:
Command Sender and Packet Viewer then hide the same header fields for F Prime targets as for the cFS ones.
`--no-inherit-ignores` (CMake: `NO_INHERIT_IGNORES`) turns this off.

## Wire-format notes

- F Prime serializes big-endian whatever the cFS processor endianness is, so every F Prime item carries an
  explicit `BIG_ENDIAN`; only the `Ccsds.*` header items follow `Globals::Cfg.processor_endian`.
- `Ccsds.cmd(mid, fc, n)` receives the **maximum** F Prime command body (descriptor + opcode + arguments at
  their maximum serialized size, e.g. `2 + 40` for a `String_40`). `ProtocolCcsdsChecksumCompat` rewrites
  `CCSDS_LENGTH` from the bytes actually serialized before computing the checksum, so variable-length
  strings go out with the correct length.
- A string argument is emitted as `<arg>_LENGTH` (`FwSizeStoreType`, `HIDDEN`) plus `<arg>` with
  `VARIABLE_BIT_SIZE`; COSMOS writes the length itself when the string is set, so only the string is shown.

## CMake integration (F Prime autocoder)

This repository is an F Prime library. In a cFS mission the intended wiring is (verified on fprime_cfs_reference with
a fresh `make prep`, full `fprime_app` build):

1. `<mission>_defs/mission_build_custom.cmake` lists the F Prime libraries:
   `list(APPEND FPRIME_LIBRARY_LOCATIONS "${CMAKE_CURRENT_LIST_DIR}/../libs/fprime_cfs" "${CMAKE_CURRENT_LIST_DIR}/../libs/fprime_cfs_cosmos")`
   (fprime_cfs's `mission_build.cmake` exports the variable to the arch builds; `mission_build_custom.cmake` runs in the
   mission-level cmake process, the arch build is a separate process).
2. fprime_cfs's `arch_build.cmake` loads the F Prime build system with the framework code off (`find_package(FPrime)`).
3. This repository, cloned as `libs/fprime_cfs_cosmos` and listed in `MISSION_GLOBAL_APPLIST` after `fprime_cfs`, has
   its `arch_build.cmake` included next by cFS `prepare()`: it calls `register_fprime_build_autocoder` for
   `cmake/autocoder/cfs_cosmos.cmake` while the registration window is still open. Its placeholder `CMakeLists.txt`
   builds nothing.
4. The normal CMake flow includes the F Prime code once (the mission's `fprime` module / fprime_cfs's CMakeLists).

With that in place an empty `cfs-cosmos-flag` in a topology module's `AUTOCODER_INPUTS` is all an app needs.

fprime-util projects add it to `library_locations` in `settings.ini` instead. Any other build can set the
`FPRIME_LIBRARY_LOCATIONS` CMake variable itself, for a cFS mission in the mission defs, which every app's configure
reads:

```cmake
# <mission>_defs/arch_build_custom.cmake
list(APPEND FPRIME_LIBRARY_LOCATIONS "${CMAKE_CURRENT_LIST_DIR}/../libs/fprime-cfs-cosmos")
```

(or `make prep PREP_OPTS=-DFPRIME_LIBRARY_LOCATIONS=/abs/path`; F Prime caches the value on the first configure,
so start from a fresh build directory). An app-level `arch_build.cmake` also runs early enough, but cFS includes it
inside the `prepare()` function, so the value must go through the cache (and be absolute, or it duplicates on
every reconfigure once F Prime rewrites the cache entry):

```cmake
# apps/<app>/arch_build.cmake
get_filename_component(_CFS_COSMOS_LIB "${CMAKE_CURRENT_LIST_DIR}/../../libs/fprime-cfs-cosmos" ABSOLUTE)
list(APPEND FPRIME_LIBRARY_LOCATIONS "${_CFS_COSMOS_LIB}")
list(REMOVE_DUPLICATES FPRIME_LIBRARY_LOCATIONS)
set(FPRIME_LIBRARY_LOCATIONS "${FPRIME_LIBRARY_LOCATIONS}" CACHE STRING "F Prime library locations" FORCE)
``` Then either register the `cfs_cosmos` autocoder
(`cmake/autocoder/cfs_cosmos.cmake`) from the CMakeLists.txt that loads F Prime, before the framework code is
included:

```cmake
set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)
find_package(FPrime REQUIRED PATHS "${FPRIME_FRAMEWORK_PATH}")
register_fprime_build_autocoder(autocoder/cfs_cosmos FALSE)
fprime_setup_included_code()
```

(In a cFS mission this is the first F Prime project in the build, e.g. `libs/fprime_cfs/CMakeLists.txt`.)
F Prime includes `library.cmake` only after the framework modules, when `register_fprime_build_autocoder`
refuses new registrations (tracked in https://github.com/nasa/fprime/issues/6272), so the manifest cannot register
the autocoder itself; it provides `fprime_cfs_cosmos_merge()` and warns when the autocoder is missing.

Alternatively, skip the global registration and call the autocoder explicitly from the topology module's
CMakeLists.txt, after `register_fprime_module` (which has already run fpp for the module, so the dictionary is
known). `run_ac_set` is not valid in F Prime's sub-builds, hence the guard:

```cmake
register_fprime_module(AUTOCODER_INPUTS ... "${CMAKE_CURRENT_LIST_DIR}/cfs-cosmos-flag" ...)
if (NOT FPRIME_IS_SUB_BUILD)
    run_ac_set(FPrimeDeployment_Top autocoder/cfs_cosmos)  # the module registered above
endif()
```

Both routes need the repository on `library_locations` (its `cmake/` directory is then on `CMAKE_MODULE_PATH`,
which is what makes the short `autocoder/cfs_cosmos` form resolve) and produce the same targets.

The autocoder consumes an empty flag file named `cfs-cosmos-flag` listed in the `AUTOCODER_INPUTS` of the
module whose fpp autocoding produces `<Deployment>TopologyDictionary.json` (the `<Deployment>_Top` module):

```cmake
register_fprime_module(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/topology.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/cfs-cosmos-flag"
    ...
)
```

It runs after fpp in the same autocoder set, reads the dictionary fpp declared and writes the bundle to
`${CMAKE_BINARY_DIR}/cfs-cosmos/<Deployment>/<TARGET>/` through a `<Deployment>_cfs_cosmos` target the module
depends on (`cfs_cosmos` builds them all). Extra generator flags: cache variables `FPRIME_CFS_COSMOS_ARGS` and
`FPRIME_CFS_COSMOS_ARGS_<Deployment>`, e.g. `-DFPRIME_CFS_COSMOS_ARGS=--no-channelized`.

```cmake
fprime_cfs_cosmos_merge(PLUGIN_DIR "${CMAKE_SOURCE_DIR}/../my-cfs-cosmos-plugin"
                        # INTERFACE CFS_INTF          # as --interface; only needed with several INTERFACE lines
                        # DEPLOYMENTS FPrimeDeployment  # default: every flag file
                        # MANUAL NO_FORCE SKIP_TARGET DRY_RUN ALL  RESERVED_MANIFEST a.json b.json
                        )
```

runs `fprime-cfs-cosmos-merge` on those bundles as a post-build step of each deployment's topology module, i.e.
building the deployment (`make fprime_app`, `fprime-util build`) merges the plugin whenever a bundle changed
(stamp-driven; re-running the merge on unchanged bundles is a no-op). A changed dictionary replaces the
target files the tool generated earlier (`--force` is passed by default; `NO_FORCE` turns it off); files it did not
generate are never replaced, so hand edits inside a generated target fail the merge instead of being lost. `MANUAL` disables the hook and leaves only
the `cfs_cosmos_merge` (or `NAME`) target; `ALL` adds it to the default build. The INTERFACE is matched on the raw
`plugin.txt` text, so ERB names such as `CFS-<%= cfs_cpu_index %>_INTF` work and need no argument. Verified on the
fprime_cfs_reference cFS mission build (`FPrimeDeployment` and `GdsBridge` both use APID 0x000 there, so merge
them separately or give one a different `ComCfg.Apid`).

### fprime < 4.5: first-build ordering

fprime releases before the fix for [nasa/fprime#6273](https://github.com/nasa/fprime/issues/6273) list fpp-to-dict's
`<System>SystemDictionary.json` as an output of the fpp-to-cpp command, so a build that produces the dictionary before the
C++ autocode never generates the topology's `*Ac.hpp` files (first build fails, second passes). Because this autocoder makes
the topology module depend on the bundle, which depends on the dictionary, it would trigger that order deterministically;
it therefore also depends on the module's fpp C++ outputs, which orders the codegen first. On fixed fprime versions the extra
dependencies are redundant and harmless.

## Telemetry modes

- **flat** (default): every F Prime packet and channel is its own `TELEMETRY` packet carrying the
  `Ccsds.tlm` header plus `FPRIME_DESCRIPTOR`/`FPRIME_PACKET_ID` (or `FPRIME_CHANNEL_ID`) ID items.
  Works on every COSMOS 6.x. `TlmPacketizer` packets arrive one per CCSDS packet. `TlmChan` packs several
  channel records into one CCSDS packet, so the bundle ships `ProtocolFprimeChannelSplitter`, a `READ`
  protocol (wired by the fragment) that splits such a packet into one COSMOS packet per record, each with a
  copy of the cFS header and descriptor.
- **`--subpacketizer`**: one parent packet per MID with a `SUBPACKETIZER` and one `SUBPACKET` per
  F Prime packet/channel. Requires COSMOS 6.10.0 or newer (`SUBPACKET` is not a keyword in 6.9.1).

`tests/cosmos/run.sh <bundle> <TARGET> [version]` loads a bundle into the real `openc3` gem in Docker
(6.9.1 by default) using stub `global_defs`/`cfs_ccsds` helpers that reproduce the inline header contract,
identifies a packet and a channel, builds a command and checks the checksum protocol.

## Install

```bash
pip install git+https://github.com/LeStarch/fprime-cfs-cosmos-one-app-one-target-generator.git
```

The dictionary is read with the fprime-gds JSON loaders (`fprime-gds>=4.2.0`, the first release with
`Dictionaries.load_dictionaries_into_config`); the loose bound resolves against whatever `fprime-gds` an F Prime
project's `requirements.txt` already pins. One dictionary is loaded per invocation. Generated bundles load and
decode against the OpenC3 6.9.1 gem (`tests/cosmos/run.sh`).

## Checks

```bash
pip install -e '.[dev]'
pytest -q
ruff check .
```
