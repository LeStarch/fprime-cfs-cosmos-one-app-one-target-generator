"""Command-line entry point for merging generated bundles into an existing cFS COSMOS plugin tree."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .merge import MergeError, merge


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Merge generated F Prime app bundles into an existing cFS COSMOS plugin"
    )
    result.add_argument("--plugin", required=True, type=Path, help="Existing plugin source tree containing plugin.txt")
    result.add_argument(
        "bundles",
        nargs="+",
        type=Path,
        help="Generated bundle directories (each has manifest.json) or directories containing them",
    )
    result.add_argument("--interface", help="Name (or unique substring) of the INTERFACE that carries cFS UDP traffic")
    result.add_argument(
        "--reserved-manifest",
        action="append",
        default=[],
        type=Path,
        help="Additional manifest (e.g. ordinary cFS MID inventory) to include in the collision check; repeatable",
    )
    result.add_argument("--force", action="store_true", help="Replace existing target/lib files that differ")
    result.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    result.add_argument(
        "--no-inherit-ignores",
        action="store_true",
        help="Do not copy the plugin targets' IGNORE_PARAMETER/IGNORE_ITEM lines into the F Prime target.txt",
    )
    result.add_argument(
        "--no-compat-helpers",
        action="store_true",
        help="Do not install the packaged global_defs/cfs_ccsds/fsw_msg_id/fsw_config_param helpers into a "
        "nasa/cfs-cosmos-plugin style tree (existing generated copies are left as they are)",
    )
    result.add_argument(
        "--skip-target",
        action="store_true",
        help="Do not insert TARGET lines (the plugin declares targets itself, e.g. from a directory loop)",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        report = merge(
            arguments.plugin,
            arguments.bundles,
            arguments.interface,
            tuple(arguments.reserved_manifest),
            arguments.force,
            arguments.dry_run,
            arguments.skip_target,
            inherit_ignores=not arguments.no_inherit_ignores,
            compat_helpers=not arguments.no_compat_helpers,
        )
    except (MergeError, OSError, ValueError, KeyError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    for warning in report.warnings:
        print(f"[WARNING] {warning}", file=sys.stderr)
    print(("DRY RUN\n" if arguments.dry_run else "") + report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
