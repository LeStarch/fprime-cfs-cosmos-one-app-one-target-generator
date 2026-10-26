"""Merge generated F Prime application bundles into an existing cFS COSMOS plugin source tree.

The plugin tree is the external package: `plugin.txt`, `lib/`, `targets/<APP>/`. Ordinary cFS targets are
never touched. Each bundle contributes its target directory, its per-target message-id Ruby file, the
shared runtime files (byte-identical across bundles), plugin-scope `VARIABLE` lines, and `MAP_TARGET` /
`PROTOCOL` lines inside one existing `INTERFACE` block. Running the merge twice is a no-op.
"""

from __future__ import annotations

import filecmp
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

FRAGMENT_FILE = "plugin.fragment.txt"
MANIFEST_FILE = "manifest.json"
PLUGIN_FILE = "plugin.txt"
INTERFACE_LINE = re.compile(r"^(?P<indent>\s*)INTERFACE\s+(?P<name>(?:<%.*?%>|\S)+)")


class MergeError(Exception):
    pass


@dataclass
class Bundle:
    directory: Path
    manifest: dict
    plugin_lines: list[str]
    interface_lines: list[str]

    @property
    def target(self) -> str:
        return self.manifest["target"]


@dataclass
class MergeReport:
    plugin: Path
    interface: str
    copied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    plugin_additions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Plugin: {self.plugin}", f"Interface: {self.interface}"]
        lines.extend(f"  copied    {item}" for item in self.copied)
        lines.extend(f"  unchanged {item}" for item in self.unchanged)
        lines.extend(f"  plugin.txt + {line.strip()}" for line in self.plugin_additions)
        return "\n".join(lines)


def load_bundle(directory: Path) -> Bundle:
    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILE
    fragment_path = directory / FRAGMENT_FILE
    for required in (manifest_path, fragment_path, directory / "targets", directory / "lib"):
        if not required.exists():
            raise MergeError(f"{directory}: not a generated bundle, missing {required.name}")
    manifest = json.loads(manifest_path.read_text())
    plugin_lines: list[str] = []
    interface_lines: list[str] = []
    for line in fragment_path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        (interface_lines if line[0].isspace() else plugin_lines).append(line.strip())
    return Bundle(directory, manifest, plugin_lines, interface_lines)


def check_collisions(bundles: list[Bundle], reserved_manifests: tuple[Path, ...] = ()) -> None:
    owners: dict[str, str] = {}
    targets: dict[str, str] = {}
    entries = [(bundle.manifest, str(bundle.directory)) for bundle in bundles]
    entries.extend((json.loads(Path(path).read_text()), str(path)) for path in reserved_manifests)
    for manifest, origin in entries:
        target = manifest.get("target")
        if target in targets:
            raise MergeError(f"target {target} is provided by both {targets[target]} and {origin}")
        targets[target] = origin
        for kind, mid in manifest.get("message_ids", {}).items():
            key = f"{int(str(mid), 0):#06x}"
            if key in owners:
                raise MergeError(f"message id {key} ({kind}) in {origin} collides with {owners[key]}")
            owners[key] = f"{origin} ({target} {kind})"


def _block_end(lines: list[str], start: int) -> int:
    """Index one past the last line of the INTERFACE block starting at `start`"""
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def select_interface(lines: list[str], name: str | None) -> int:
    matches = [(index, match) for index, line in enumerate(lines) if (match := INTERFACE_LINE.match(line))]
    if name:
        matches = [(index, match) for index, match in matches if name in match.group("name")]
    if len(matches) == 1:
        return matches[0][0]
    names = ", ".join(match.group("name") for _index, match in matches) or "none"
    hint = f"matching {name!r}" if name else "(use --interface to choose)"
    raise MergeError(f"{PLUGIN_FILE}: expected exactly one INTERFACE {hint}, found: {names}")


def merge_plugin_text(text: str, bundles: list[Bundle], interface: str | None) -> tuple[str, list[str], str]:
    lines = text.splitlines()
    start = select_interface(lines, interface)
    interface_name = INTERFACE_LINE.match(lines[start]).group("name")
    end = _block_end(lines, start)
    block = lines[start + 1 : end]
    existing_block = {line.strip() for line in block}
    sample = next((line for line in block if line.strip().startswith("MAP_TARGET")), None)
    indent = (
        sample[: len(sample) - len(sample.lstrip())]
        if sample
        else " " * (len(lines[start]) - len(lines[start].lstrip()) + 4)
    )
    additions: list[str] = []
    for bundle in bundles:
        for line in bundle.interface_lines:
            if line not in existing_block and (indent + line) not in additions:
                additions.append(indent + line)
    lines[end:end] = additions

    existing_plugin = {line.strip() for line in lines if line and not line[0].isspace()}
    plugin_additions: list[str] = []
    for bundle in bundles:
        for line in bundle.plugin_lines:
            if line not in existing_plugin and line not in plugin_additions:
                plugin_additions.append(line)
    variable_indices = [
        index for index, line in enumerate(lines) if line.startswith(("VARIABLE ", "VARIABLE_DESCRIPTION"))
    ]
    insert_at = variable_indices[-1] + 1 if variable_indices else 0
    lines[insert_at:insert_at] = plugin_additions
    return "\n".join(lines) + "\n", additions + plugin_additions, interface_name


def _same_tree(source: Path, destination: Path) -> bool:
    if source.is_file():
        return destination.is_file() and filecmp.cmp(source, destination, shallow=False)
    comparison = filecmp.dircmp(source, destination)
    if comparison.left_only or comparison.right_only or comparison.diff_files or comparison.funny_files:
        return False
    return all(_same_tree(source / name, destination / name) for name in comparison.common_dirs)


def _place(source: Path, destination: Path, label: str, force: bool, write: bool, report: MergeReport) -> None:
    if destination.exists():
        if _same_tree(source, destination):
            report.unchanged.append(label)
            return
        if not force:
            raise MergeError(f"{destination} exists with different content (use --force to replace)")
        if write:
            shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
    if not write:
        report.copied.append(label)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination) if source.is_dir() else shutil.copy2(source, destination)
    report.copied.append(label)


def merge(
    plugin: Path,
    bundle_dirs: list[Path],
    interface: str | None = None,
    reserved_manifests: tuple[Path, ...] = (),
    force: bool = False,
    dry_run: bool = False,
) -> MergeReport:
    plugin = Path(plugin)
    plugin_file = plugin / PLUGIN_FILE
    if not plugin_file.is_file():
        raise MergeError(f"{plugin}: no {PLUGIN_FILE}; expected an existing cFS COSMOS plugin source tree")
    bundles = [load_bundle(path) for path in bundle_dirs]
    if not bundles:
        raise MergeError("at least one bundle is required")
    check_collisions(bundles, reserved_manifests)
    for bundle in bundles:
        for relative in bundle.manifest.get("target_files", []) + bundle.manifest.get("shared_files", []):
            if not (bundle.directory / relative).exists():
                raise MergeError(f"{bundle.directory}: manifest lists missing file {relative}")

    new_text, additions, interface_name = merge_plugin_text(plugin_file.read_text(), bundles, interface)
    report = MergeReport(plugin, interface_name, plugin_additions=additions)
    shared_seen: dict[str, Path] = {}
    for bundle in bundles:
        for relative in bundle.manifest.get("target_files", []):
            source, label = bundle.directory / relative, f"{bundle.target}: {relative}"
            _place(source, plugin / relative, label, force, not dry_run, report)
        for relative in bundle.manifest.get("shared_files", []):
            source, label = bundle.directory / relative, f"{bundle.target}: {relative}"
            if relative in shared_seen:
                if not filecmp.cmp(source, shared_seen[relative], shallow=False):
                    raise MergeError(f"shared file {relative} differs between {shared_seen[relative]} and {source}")
                continue
            shared_seen[relative] = source
            _place(source, plugin / relative, label, force, not dry_run, report)
    if dry_run:
        return report
    if additions:
        plugin_file.write_text(new_text)
    return report
