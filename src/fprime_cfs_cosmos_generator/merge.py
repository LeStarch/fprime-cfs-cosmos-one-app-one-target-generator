"""Merge generated F Prime application bundles into an existing cFS COSMOS plugin source tree.

The plugin tree is the external package: `plugin.txt`, `lib/`, `targets/<APP>/`. Ordinary cFS targets are
never touched. Each bundle contributes its target directory, its per-target message-id Ruby file, the
shared runtime files (byte-identical across bundles), plugin-scope `VARIABLE` lines, and `MAP_TARGET` /
`PROTOCOL` lines inside one existing `INTERFACE` block. Running the merge twice is a no-op.
"""

from __future__ import annotations

import filecmp
import json
import os
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
    def variable_lines(self) -> list[str]:
        return [line for line in self.plugin_lines if line.startswith("VARIABLE")]

    @property
    def target_lines(self) -> list[str]:
        return [line for line in self.plugin_lines if line.startswith("TARGET ")]

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


def _bundle_files(manifest: dict, directory: Path) -> list[str]:
    """Manifest-listed files, restricted to the bundle's own targets/<T> and lib/*.rb"""
    target = manifest.get("target", "")
    files = list(manifest.get("target_files", [])) + list(manifest.get("shared_files", []))
    for relative in files:
        parts = Path(relative).parts
        in_target = parts == ("targets", target)
        in_lib = len(parts) == 2 and parts[0] == "lib" and parts[1].endswith(".rb")
        if Path(relative).is_absolute() or ".." in parts or not (in_target or in_lib):
            raise MergeError(f"{directory}: manifest lists unexpected file {relative!r}")
        if not (directory / relative).exists():
            raise MergeError(f"{directory}: manifest lists missing file {relative}")
        if any(path.is_symlink() for path in [directory / relative, *(directory / relative).rglob("*")]):
            raise MergeError(f"{directory}: symbolic links are not allowed in a bundle ({relative})")
    return files


def load_bundle(directory: Path) -> Bundle:
    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILE
    fragment_path = directory / FRAGMENT_FILE
    for required in (manifest_path, fragment_path, directory / "targets", directory / "lib"):
        if not required.exists():
            raise MergeError(f"{directory}: not a generated bundle, missing {required.name}")
    manifest = json.loads(manifest_path.read_text())
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(manifest.get("target", ""))):
        raise MergeError(f"{directory}: manifest target name {manifest.get('target')!r} is invalid")
    _bundle_files(manifest, directory)
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
        if target is not None:
            if target.upper() in targets:
                raise MergeError(f"target {target} is provided by both {targets[target.upper()]} and {origin}")
            targets[target.upper()] = origin
        for kind, mid in manifest.get("message_ids", {}).items():
            try:
                key = f"{int(str(mid), 0):#06x}"
            except ValueError as error:
                raise MergeError(f"{origin}: message id {kind}={mid!r} is not a number") from error
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
        exact = [(index, match) for index, match in matches if match.group("name") == name]
        matches = exact or [(index, match) for index, match in matches if name in match.group("name")]
    if len(matches) == 1:
        return matches[0][0]
    names = ", ".join(match.group("name") for _index, match in matches) or "none"
    hint = f"matching {name!r}" if name else "(use --interface to choose)"
    raise MergeError(f"{PLUGIN_FILE}: expected exactly one INTERFACE {hint}, found: {names}")


def _target_insert_index(lines: list[str], interface_start: int) -> int:
    """TARGET must precede the INTERFACE that maps it (plugin.txt deploys in file order). An indented
    INTERFACE sits inside an ERB block, so insert before the block's top-level opening line instead."""
    if not lines[interface_start][0].isspace():
        return interface_start
    index = interface_start
    while index > 0 and (not lines[index - 1].strip() or lines[index - 1][0].isspace()):
        index -= 1
    return max(index - 1, 0)


def merge_plugin_text(
    text: str, bundles: list[Bundle], interface: str | None, skip_target: bool = False
) -> tuple[str, list[str], str]:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
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

    def new_lines(selector) -> list[str]:
        result: list[str] = []
        for bundle in bundles:
            for line in selector(bundle):
                if line not in existing_plugin and line not in result:
                    result.append(line)
        return result

    target_lines = [] if skip_target else new_lines(lambda bundle: bundle.target_lines)
    variable_lines = new_lines(lambda bundle: bundle.variable_lines)
    variable_indices = [
        index for index, line in enumerate(lines[:start]) if line.startswith(("VARIABLE ", "VARIABLE_DESCRIPTION"))
    ]
    insert_at = variable_indices[-1] + 1 if variable_indices else _target_insert_index(lines, start)
    lines[insert_at:insert_at] = variable_lines + target_lines
    plugin_additions = variable_lines + target_lines
    return newline.join(lines) + newline, additions + plugin_additions, interface_name


def _same_tree(source: Path, destination: Path) -> bool:
    if source.is_file():
        return destination.is_file() and filecmp.cmp(source, destination, shallow=False)
    if not destination.is_dir():
        return False
    source_names = sorted(path.relative_to(source) for path in source.rglob("*"))
    destination_names = sorted(path.relative_to(destination) for path in destination.rglob("*"))
    if source_names != destination_names:
        return False
    return all(_same_tree(source / name, destination / name) for name in source_names if (source / name).is_file())


def _generated(path: Path) -> bool:
    """Only files/directories this tool produced may be replaced by --force"""
    if path.is_dir():
        return (path / "cmd_tlm" / "fprime_cmd.txt").is_file()
    try:
        return path.read_text().startswith("# Generated F Prime cFS")
    except (OSError, UnicodeDecodeError):
        return False


def _place(source: Path, destination: Path, label: str, force: bool, write: bool, report: MergeReport) -> None:
    if destination.exists():
        if _same_tree(source, destination):
            report.unchanged.append(label)
            return
        if not force:
            raise MergeError(f"{destination} exists with different content (use --force to replace)")
        if not _generated(destination):
            raise MergeError(
                f"{destination} is not generated F Prime content; refusing to replace it even with --force"
            )
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
    skip_target: bool = False,
) -> MergeReport:
    plugin = Path(plugin)
    plugin_file = plugin / PLUGIN_FILE
    if not plugin_file.is_file():
        raise MergeError(f"{plugin}: no {PLUGIN_FILE}; expected an existing cFS COSMOS plugin source tree")
    bundles = [load_bundle(path) for path in bundle_dirs]
    if not bundles:
        raise MergeError("at least one bundle is required")
    check_collisions(bundles, reserved_manifests)

    new_text, additions, interface_name = merge_plugin_text(
        plugin_file.read_bytes().decode(), bundles, interface, skip_target
    )

    def place_all(write: bool) -> MergeReport:
        report = MergeReport(plugin, interface_name, plugin_additions=additions)
        shared_seen: dict[str, Path] = {}
        for bundle in bundles:
            for relative in bundle.manifest.get("target_files", []):
                source, label = bundle.directory / relative, f"{bundle.target}: {relative}"
                _place(source, plugin / relative, label, force, write, report)
            for relative in bundle.manifest.get("shared_files", []):
                source, label = bundle.directory / relative, f"{bundle.target}: {relative}"
                if relative in shared_seen:
                    if not filecmp.cmp(source, shared_seen[relative], shallow=False):
                        raise MergeError(f"shared file {relative} differs between {shared_seen[relative]} and {source}")
                    continue
                shared_seen[relative] = source
                _place(source, plugin / relative, label, force, write, report)
        return report

    report = place_all(write=False)
    if dry_run:
        return report
    report = place_all(write=True)
    if additions:
        temporary = plugin_file.with_name(plugin_file.name + ".fprime-merge.tmp")
        temporary.write_bytes(new_text.encode())
        os.replace(temporary, plugin_file)
    return report
