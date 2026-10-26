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

## Install

```bash
pip install git+https://github.com/LeStarch/fprime-cfs-cosmos-one-app-one-target-generator.git
```

The dictionary, wire-format, and cFS MID derivation logic comes from
`fprime-cosmos` (its `cfs` overlay, currently fprime-community/fprime-cosmos
PR #2). Tested against OpenC3 COSMOS 6.9.1 plugin validation.

## Checks

```bash
pip install -e '.[dev]'
pytest -q
ruff check .
```
