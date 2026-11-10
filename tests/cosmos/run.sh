#!/usr/bin/env bash
# Load a generated bundle into the real openc3 gem (COSMOS 7.5.1 by default) without a running COSMOS:
# renders the ERB cmd/tlm definitions with the shipped nasa/cfs-cosmos-plugin compatibility helpers
# (runtime/compat: cFE 7 16-byte telemetry header), identifies packets, channels and (when the bundle has it)
# the FPRIME_EVENTS packet, runs the channel splitter and builds a command through the checksum protocol.
#   tests/cosmos/run.sh <bundle_dir> <TARGET> [cosmos_version] [--header12] [--nasa <cfs-cosmos-plugin dir>] [--image REF]
#   --header12  render with the 12-byte header stub (tests/cosmos/stubs/header12) instead of the shipped helper
#   --nasa DIR  also compare the shipped helpers' header items with DIR/targets/CFS/lib/cfs_packet_headers.rb
#   --image REF use this image instead of openc3inc/openc3-cosmos-cmd-tlm-api:<version> (e.g. a digest-pinned ref)
set -euo pipefail
[ $# -ge 2 ] || { sed -n '2,9p' "$0" >&2; exit 2; }
bundle=$(realpath "$1"); target=$2; shift 2
version=7.5.1; flavour=header16; nasa=""; image=""
while [ $# -gt 0 ]; do
  case "$1" in
    --header12) flavour=header12 ;;
    --nasa) [ $# -ge 2 ] || { echo "--nasa needs a plugin directory" >&2; exit 2; }; nasa=$(realpath "$2"); shift ;;
    --image) [ $# -ge 2 ] || { echo "--image needs an image reference" >&2; exit 2; }; image=$2; shift ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) version=$1 ;;
  esac
  shift
done
[ -n "$image" ] || image="openc3inc/openc3-cosmos-cmd-tlm-api:$version"
here=$(cd "$(dirname "$0")" && pwd)
compat=$(cd "$here/../../src/fprime_cfs_cosmos_generator/runtime/compat" && pwd)
mounts=(-v "$bundle:/b:ro" -v "$here/stubs:/stubs:ro" -v "$compat:/compat:ro" -v "$here/load_test.rb:/load_test.rb:ro")
[ -n "$nasa" ] && mounts+=(-v "$nasa:/nasa:ro")
docker run --rm "${mounts[@]}" "$image" \
  ruby /load_test.rb /b "$target" "$flavour" "${nasa:+/nasa}" 2>&1 \
  | grep -v 'gems/redis\|gems/connection_pool\|store_queued.rb\|store_autoload.rb\|^\s*from /usr/lib/ruby'
