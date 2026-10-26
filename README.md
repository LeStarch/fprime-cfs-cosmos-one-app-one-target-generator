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
plugin's existing `VARIABLE`s, and appends `MAP_TARGET <%= ... %>` plus a single
`PROTOCOL WRITE ProtocolCcsdsChecksumCompat` to the end of the chosen `INTERFACE` block. The interface is
the only `INTERFACE` in `plugin.txt` or the one selected by `--interface`. Nothing else in the plugin is
touched; ordinary cFS targets stay as they are. Target names and message IDs are checked for collisions
across all bundles and `--reserved-manifest` files before anything is written. Re-running the merge is a
no-op; an existing target with different content is an error unless `--force` is given.

## Telemetry modes

- **flat** (default): every F Prime packet and channel is its own `TELEMETRY` packet carrying the
  `Ccsds.tlm` header plus `FPRIME_DESCRIPTOR`/`FPRIME_PACKET_ID` (or `FPRIME_CHANNEL_ID`) ID items.
  Works on every COSMOS 6.x; matches `CfsTlmFramer`, which wraps one F Prime com packet per CCSDS packet.
- **`--subpacketizer`**: one parent packet per MID with a `SUBPACKETIZER` and one `SUBPACKET` per
  F Prime packet/channel. Requires COSMOS 6.10.0 or newer (`SUBPACKET` is not a keyword in 6.9.1).

`tests/cosmos/run.sh <bundle> <TARGET> [version]` loads a bundle into the real `openc3` gem in Docker
(6.9.1 by default) using stub `global_defs`/`cfs_ccsds` helpers that reproduce the inline header contract,
identifies a packet and a channel, builds a command and checks the checksum protocol.

## Install

```bash
pip install git+https://github.com/LeStarch/fprime-cfs-cosmos-one-app-one-target-generator.git
```

The dictionary, wire-format, and cFS MID derivation logic comes from
`fprime-cosmos` (its `cfs` overlay, currently fprime-community/fprime-cosmos
PR #2). Generated bundles load and decode against the OpenC3 6.9.1 gem (`tests/cosmos/run.sh`).

## Checks

```bash
pip install -e '.[dev]'
pytest -q
ruff check .
```
