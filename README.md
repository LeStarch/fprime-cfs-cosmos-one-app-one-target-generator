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

## Wire-format notes

- F Prime serializes big-endian whatever the cFS processor endianness is, so every F Prime item carries an
  explicit `BIG_ENDIAN`; only the `Ccsds.*` header items follow `Globals::Cfg.processor_endian`.
- `Ccsds.cmd(mid, fc, n)` receives the **maximum** F Prime command body (descriptor + opcode + arguments at
  their maximum serialized size, e.g. `2 + 40` for a `String_40`). `ProtocolCcsdsChecksumCompat` rewrites
  `CCSDS_LENGTH` from the bytes actually serialized before computing the checksum, so variable-length
  strings go out with the correct length.

## CMake integration (F Prime autocoder)

This repository is an F Prime library: add it to `library_locations` in `settings.ini` (or
`-DFPRIME_LIBRARY_LOCATIONS=...`) and register the `cfs_cosmos` autocoder (`cmake/autocoder/cfs_cosmos.cmake`)
from the CMakeLists.txt that loads F Prime, before the framework code is included:

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
                        INTERFACE CFS_INTF            # optional, as --interface
                        DEPLOYMENTS FPrimeDeployment  # optional, default: every flag file
                        # SKIP_TARGET FORCE DRY_RUN ALL  RESERVED_MANIFEST a.json b.json
                        )
```

adds `cfs_cosmos_merge` (or `NAME`), which runs `fprime-cfs-cosmos-merge` on those bundles; build it with
`make cfs_cosmos_merge` / `fprime-util build --target cfs_cosmos_merge`, or pass `ALL`. Verified on the
fprime_cfs_reference cFS mission build (`FPrimeDeployment` and `GdsBridge` both use APID 0x000 there, so merge
them separately or give one a different `ComCfg.Apid`).

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
