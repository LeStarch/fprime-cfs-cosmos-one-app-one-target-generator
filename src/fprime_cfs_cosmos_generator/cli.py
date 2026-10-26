"""Command-line entry point for standalone per-application target generation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from fprime_cosmos.dictionary import DictionaryError

from .generator import GeneratorError, build_bundle


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Generate one standalone cFS COSMOS target for an F Prime app")
    result.add_argument("--dictionary", required=True, type=Path, help="F Prime JSON topology dictionary")
    result.add_argument("--output", "-o", required=True, type=Path, help="Parent directory for the generated bundle")
    result.add_argument("--target-name", help="COSMOS target name; defaults to the deployment name in uppercase")
    result.add_argument("--packet-set-name", help="Packet set to use when the dictionary defines more than one")
    result.add_argument(
        "--reserved-manifest",
        action="append",
        default=[],
        type=Path,
        help="Manifest from another generated app or ordinary cFS ID inventory; repeat to reject collisions",
    )
    result.add_argument("--no-channelized", action="store_true", help="Do not generate channelized telemetry")
    result.add_argument(
        "--subpacketizer",
        action="store_true",
        help="Emit SUBPACKETIZER/SUBPACKET telemetry (COSMOS 6.10+); default is flat packets for every COSMOS 6.x",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        artifacts = build_bundle(
            arguments.dictionary,
            arguments.output,
            arguments.target_name,
            arguments.packet_set_name,
            not arguments.no_channelized,
            tuple(arguments.reserved_manifest),
            arguments.subpacketizer,
        )
    except (DictionaryError, GeneratorError, OSError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print(f"Bundle written: {artifacts.directory}")
    print(f"Manifest written: {artifacts.manifest_path}")
    return 0
