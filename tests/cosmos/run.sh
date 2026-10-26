#!/usr/bin/env bash
# Load a generated bundle into the real openc3 gem (COSMOS 6.9.1 by default) without a running COSMOS:
# parses the ERB cmd/tlm definitions against stub cFS helpers, identifies a packet and a channel, builds a
# command and checks the checksum protocol. Usage: tests/cosmos/run.sh <bundle_dir> <TARGET> [cosmos_version]
set -euo pipefail
bundle=$(realpath "$1"); target=$2; version=${3:-6.9.1}
here=$(cd "$(dirname "$0")" && pwd)
docker run --rm -v "$bundle:/b:ro" -v "$here/stubs:/stubs:ro" -v "$here/load_test.rb:/load_test.rb:ro" \
  "openc3inc/openc3-cosmos-cmd-tlm-api:$version" ruby /load_test.rb /b "$target" 2>&1 | grep -v 'redis\|connection_pool\|store_\|^\s*from'
