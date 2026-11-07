"""Merge generated F Prime application bundles into an existing cFS COSMOS plugin source tree.

The plugin tree is the external package: `plugin.txt`, `lib/`, `targets/<APP>/`. Ordinary cFS targets are
never touched. Each bundle contributes its target directory, its per-target message-id Ruby file, the
shared runtime files (byte-identical across bundles), plugin-scope `VARIABLE` lines, and `MAP_TARGET` /
`PROTOCOL` lines inside one existing `INTERFACE` block. Running the merge twice is a no-op.

Plugins laid out like nasa/cfs-cosmos-plugin (`cfs_packet_headers.rb` under `lib/` or `targets/*/lib/`) do not
ship the `global_defs` / `cfs_ccsds` / `fsw_msg_id` / `fsw_config_param` helpers the bundles require; the merge
installs the packaged compatibility helpers (`runtime/compat/`) into `lib/` unless hand-written ones exist.
"""

from __future__ import annotations

import difflib
import filecmp
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

FRAGMENT_FILE = "plugin.fragment.txt"
MANIFEST_FILE = "manifest.json"
PLUGIN_FILE = "plugin.txt"
INTERFACE_LINE = re.compile(r"^(?P<indent>\s*)INTERFACE\s+(?P<name>(?:<%.*?%>|\S)+)")
ERB_LOOP_LINE = re.compile(r"<%[^%]*?\b(?:each|each_with_index|times|upto|downto|step|loop|while|until|for)\b")
GENERATED_MARKER = "# Generated F Prime cFS"
MAP_TARGET = "MAP_TARGET "
BUNDLE_MAP_TARGET = re.compile(r"^MAP_TARGET\s+<%=\s*fprime_[a-z0-9_]+_target_name\s*%>$")
WRITE_PROTOCOL = "PROTOCOL WRITE "
COMPAT_HELPERS = ("global_defs", "cfs_ccsds", "fsw_msg_id", "fsw_config_param")
COMPAT_DETECTION_FILE = "cfs_packet_headers.rb"
COMPAT_RUNTIME = resources.files(__package__) / "runtime" / "compat"


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
    plugin_moves: list[str] = field(default_factory=list)
    compat_skipped: str | None = None
    compat_kept: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Plugin: {self.plugin}", f"Interface: {self.interface}"]
        lines.extend(f"  copied    {item}" for item in self.copied)
        lines.extend(f"  unchanged {item}" for item in self.unchanged)
        lines.extend(f"  kept      {item}" for item in self.compat_kept)
        if self.compat_skipped:
            lines.append(f"  compat helpers not installed: {self.compat_skipped}")
        lines.extend(f"  {PLUGIN_FILE} + {line.strip()}" for line in self.plugin_additions)
        lines.extend(f"  {PLUGIN_FILE} ~ {line.strip()} (moved)" for line in self.plugin_moves)
        lines.extend(f"  WARNING   {warning}" for warning in self.warnings)
        return "\n".join(lines)


@dataclass
class PluginText:
    """Result of merging the bundles' fragment lines into plugin.txt"""

    text: str
    additions: list[str]
    interface: str
    moves: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __iter__(self):
        return iter((self.text, self.additions, self.interface))


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


def _expand_bundle_dirs(paths: list[Path]) -> list[Path]:
    """A bundle directory, or a directory that holds bundle directories (e.g. a build's cfs-cosmos/<DEPLOYMENT>)"""
    result: list[Path] = []
    for path in paths:
        path = Path(path)
        if (path / MANIFEST_FILE).is_file():
            result.append(path)
            continue
        children = (
            sorted(child for child in path.iterdir() if (child / MANIFEST_FILE).is_file()) if path.is_dir() else []
        )
        if not children:
            raise MergeError(f"{path}: not a generated bundle and contains none")
        result.extend(children)
    return result


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


def _enclosing_erb_loop(lines: list[str], interface_start: int) -> str | None:
    """The top-level ERB line opening the block that holds an indented INTERFACE, when it is a loop"""
    if not lines[interface_start][0].isspace():
        return None
    opening = lines[_target_insert_index(lines, interface_start)].strip()
    return opening if ERB_LOOP_LINE.search(opening) else None


def _continuation_count(lines: list[str], start: int, end: int) -> int:
    """Lines of the INTERFACE statement itself that continue with a trailing `&`"""
    count = 0
    index = start
    while index < end - 1 and lines[index].rstrip().endswith("&"):
        count += 1
        index += 1
    return count


def _moved_lines(before: list[str], after: list[str]) -> list[str]:
    """Lines present in both sequences that are not part of their longest common subsequence"""
    common_before = [line for line in before if line in after]
    common_after = [line for line in after if line in before]
    matcher = difflib.SequenceMatcher(None, common_before, common_after, autojunk=False)
    stable = {line for block in matcher.get_matching_blocks() for line in common_after[block.b : block.b + block.size]}
    return [line for line in common_after if line not in stable]


def merge_plugin_text(text: str, bundles: list[Bundle], interface: str | None, skip_target: bool = False) -> PluginText:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = select_interface(lines, interface)
    interface_name = INTERFACE_LINE.match(lines[start]).group("name")
    end = _block_end(lines, start)
    continuation = _continuation_count(lines, start, end)
    block = lines[start + 1 : end]
    statement, body = block[:continuation], block[continuation:]
    existing_block = {line.strip() for line in body}
    sample = next((line for line in body if line.strip().startswith(MAP_TARGET)), None)
    indent = (
        sample[: len(sample) - len(sample.lstrip())]
        if sample
        else " " * (len(lines[start]) - len(lines[start].lstrip()) + 4)
    )
    map_targets: list[str] = []
    write_protocols: list[str] = []
    others: list[str] = []
    for bundle in bundles:
        for line in bundle.interface_lines:
            if line.startswith(MAP_TARGET):
                bucket = map_targets
            elif line.startswith(WRITE_PROTOCOL):
                bucket = write_protocols
            else:
                bucket = others
            if line not in bucket:
                bucket.append(line)
    # Bundle MAP_TARGET lines go before the plugin's own: nasa/cfs-cosmos-plugin's file packets have no ID items,
    # so a CFS target mapped first identifies (and swallows) every F Prime packet. Existing bundle mappings keep
    # their relative order, new ones follow them.
    def bundle_map(line: str) -> bool:
        return line.strip() in map_targets or bool(BUNDLE_MAP_TARGET.match(line.strip()))

    kept = [line for line in body if line.strip() not in write_protocols and not bundle_map(line)]
    existing_maps = [line.strip() for line in body if bundle_map(line)]
    map_group = existing_maps + [line for line in map_targets if line not in existing_maps]
    first_map = next((index for index, line in enumerate(kept) if line.strip().startswith(MAP_TARGET)), None)
    first_protocol = next((index for index, line in enumerate(kept) if line.strip().startswith("PROTOCOL ")), len(kept))
    map_at = first_map if first_map is not None else min(first_protocol, len(kept))
    kept = kept[:map_at] + [indent + line for line in map_group] + kept[map_at:]
    # COSMOS runs WRITE protocols in reverse declaration order (Interface#add_protocol unshifts them), so the
    # F Prime protocol that finalizes the CCSDS length/checksum goes before the plugin's own WRITE protocols.
    first_protocol = next((index for index, line in enumerate(kept) if line.strip().startswith("PROTOCOL ")), len(kept))
    new_body = (
        kept[:first_protocol]
        + [indent + line for line in write_protocols]
        + kept[first_protocol:]
        + [indent + line for line in others if line not in existing_block]
    )
    new_block = statement + new_body
    stripped_before = [line.strip() for line in block]
    stripped_after = [line.strip() for line in new_block]
    additions = [line for line in new_block if line.strip() not in stripped_before]
    moves = [] if new_block == block else _moved_lines(stripped_before, stripped_after)
    lines[start + 1 : end] = new_block

    warnings: list[str] = []
    loop = _enclosing_erb_loop(lines, start)
    if loop is not None and map_targets:
        targets = ", ".join(bundle.target for bundle in bundles)
        warnings.append(
            f"{PLUGIN_FILE}: INTERFACE {interface_name} is inside the ERB loop {loop!r}; the MAP_TARGET lines for "
            f"{targets} render once per iteration while each TARGET is declared once, so the loop must run a "
            f"single iteration (e.g. $cfs_fleet_globals_num_cfs_targets = 1) or the plugin will fail to install"
        )

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
    variable_indices = [index for index, line in enumerate(lines[:start]) if line.startswith("VARIABLE ")]
    insert_at = variable_indices[-1] + 1 if variable_indices else _target_insert_index(lines, start)
    lines[insert_at:insert_at] = variable_lines + target_lines
    plugin_additions = variable_lines + target_lines
    return PluginText(newline.join(lines) + newline, additions + plugin_additions, interface_name, moves, warnings)


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
        return path.read_text().startswith(GENERATED_MARKER)
    except (OSError, UnicodeDecodeError):
        return False


IGNORE_KEYWORDS = ("IGNORE_PARAMETER ", "IGNORE_ITEM ")
IGNORE_HEADER = "# Inherited from the plugin's existing targets by fprime-cfs-cosmos-merge"


def inherited_ignore_lines(plugin: Path, bundle_targets: set[str]) -> list[str]:
    """IGNORE_PARAMETER/IGNORE_ITEM lines of the plugin's hand-written targets. The F Prime commands share the
    plugin's CCSDS header helpers, so they hide the same header fields in Command Sender/Packet Viewer."""
    lines: list[str] = []
    for target_file in sorted(plugin.glob("targets/*/target.txt")):
        if target_file.parent.name.upper() in bundle_targets or _generated(target_file.parent):
            continue
        for raw in target_file.read_text().splitlines():
            line = " ".join(raw.split())
            if line.startswith(IGNORE_KEYWORDS) and line not in lines:
                lines.append(line)
    return lines


def _stage_target(source: Path, staging: Path, ignores: list[str]) -> Path:
    staged = staging / source.name
    shutil.copytree(source, staged)
    target_file = staged / "target.txt"
    text = target_file.read_text()
    target_file.write_text(text + "\n" + "\n".join([IGNORE_HEADER, *ignores]) + "\n")
    return staged


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


def required_helpers(bundles: list[Bundle]) -> list[str]:
    """Union of the bundles' manifest `required_helpers` (manifests predating the field need the four defaults)"""
    names: list[str] = []
    for bundle in bundles:
        for name in bundle.manifest.get("required_helpers", COMPAT_HELPERS):
            if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)):
                raise MergeError(f"{bundle.directory}: manifest required helper {name!r} is not a plain file name")
            if name not in names:
                names.append(name)
    return names


def helper_files(plugin: Path, name: str) -> list[Path]:
    """Where plugin.txt's ERB can `require` a helper from: the plugin lib/ and every targets/*/lib/"""
    candidates = [plugin / "lib" / f"{name}.rb", *plugin.glob(f"targets/*/lib/{name}.rb")]
    return sorted(path for path in candidates if path.is_file())


def compat_detected(plugin: Path) -> bool:
    return bool(helper_files(plugin, COMPAT_DETECTION_FILE[:-3]))


@dataclass
class CompatPlan:
    install: list[tuple[Path, Path, str]] = field(default_factory=list)  # (source, destination, label)
    kept: list[str] = field(default_factory=list)
    skipped: str | None = None
    warnings: list[str] = field(default_factory=list)


def plan_compat_helpers(plugin: Path, names: list[str], enabled: bool) -> CompatPlan:
    """Decide which packaged compatibility helpers to install into `plugin/lib`, failing on ambiguous trees"""
    plan = CompatPlan()
    if not enabled:
        plan.skipped = "--no-compat-helpers"
        plan.kept = [
            str(path.relative_to(plugin)) for name in names for path in helper_files(plugin, name) if _generated(path)
        ]
        return plan
    if not compat_detected(plugin):
        plan.skipped = f"no {COMPAT_DETECTION_FILE} under lib/ or targets/*/lib/ (not a nasa/cfs-cosmos-plugin layout)"
        return plan
    foreign: dict[str, list[Path]] = {}
    generated: dict[str, list[Path]] = {}
    missing: list[str] = []
    for name in names:
        files = helper_files(plugin, name)
        ours = [path for path in files if _generated(path)]
        theirs = [path for path in files if not _generated(path)]
        if ours and theirs:
            raise MergeError(
                f"helper {name}.rb exists both as generated F Prime content ({', '.join(map(str, ours))}) and as a "
                f"hand-written file ({', '.join(map(str, theirs))}); remove one"
            )
        if theirs:
            foreign[name] = theirs
        elif ours:
            generated[name] = ours
        else:
            missing.append(name)
    if foreign and (missing or generated):
        present = ", ".join(str(path) for paths in foreign.values() for path in paths)
        absent = ", ".join(f"{name}.rb" for name in [*missing, *generated])
        raise MergeError(
            f"the plugin provides only part of the helpers the bundles require: hand-written {present}; "
            f"not hand-written: {absent}. Provide all of them or remove the hand-written ones"
        )
    if foreign:
        plan.skipped = "hand-written helpers present: " + ", ".join(
            str(path) for paths in foreign.values() for path in paths
        )
        return plan
    for name in [*missing, *generated]:
        source = COMPAT_RUNTIME / f"{name}.rb"
        if not source.is_file():
            plan.warnings.append(
                f"required helper {name}.rb is not provided by the plugin and this tool has no compatibility "
                f"version of it; the generated cmd/tlm files will not render until it exists under lib/"
            )
            continue
        destinations = generated.get(name) or [plugin / "lib" / f"{name}.rb"]
        for destination in destinations:
            plan.install.append((Path(str(source)), destination, f"compat: {destination.relative_to(plugin)}"))
    return plan


def missing_helper_warnings(plugin: Path, names: list[str], planned: list[Path]) -> list[str]:
    """Helpers neither present in the tree nor about to be installed"""
    planned_set = {path.resolve() for path in planned}
    return [
        f"required helper {name}.rb not found under lib/ or targets/*/lib/; the generated cmd/tlm files will fail to "
        f"render (pass a plugin that provides it or let the merge install the compatibility helpers)"
        for name in names
        if not helper_files(plugin, name) and (plugin / "lib" / f"{name}.rb").resolve() not in planned_set
    ]


def merge(
    plugin: Path,
    bundle_dirs: list[Path],
    interface: str | None = None,
    reserved_manifests: tuple[Path, ...] = (),
    force: bool = False,
    dry_run: bool = False,
    skip_target: bool = False,
    inherit_ignores: bool = True,
    compat_helpers: bool = True,
) -> MergeReport:
    plugin = Path(plugin)
    plugin_file = plugin / PLUGIN_FILE
    if not plugin_file.is_file():
        raise MergeError(f"{plugin}: no {PLUGIN_FILE}; expected an existing cFS COSMOS plugin source tree")
    bundles = [load_bundle(path) for path in _expand_bundle_dirs(bundle_dirs)]
    if not bundles:
        raise MergeError("at least one bundle is required")
    check_collisions(bundles, reserved_manifests)

    plugin_text = merge_plugin_text(plugin_file.read_bytes().decode(), bundles, interface, skip_target)
    new_text, additions, interface_name = plugin_text
    compat = plan_compat_helpers(plugin, required_helpers(bundles), compat_helpers)
    warnings = plugin_text.warnings + compat.warnings
    warnings += missing_helper_warnings(plugin, required_helpers(bundles), [item[1] for item in compat.install])

    ignores = inherited_ignore_lines(plugin, {bundle.target.upper() for bundle in bundles}) if inherit_ignores else []
    staged: dict[str, Path] = {}

    def place_all(write: bool) -> MergeReport:
        report = MergeReport(
            plugin,
            interface_name,
            plugin_additions=additions,
            plugin_moves=plugin_text.moves,
            compat_skipped=compat.skipped,
            compat_kept=list(compat.kept),
            warnings=list(warnings),
        )
        shared_seen: dict[str, Path] = {}
        for bundle in bundles:
            for relative in bundle.manifest.get("target_files", []):
                source, label = bundle.directory / relative, f"{bundle.target}: {relative}"
                if ignores and relative == f"targets/{bundle.target}":
                    if relative not in staged:
                        staged[relative] = _stage_target(source, staging, ignores)
                    source = staged[relative]
                _place(source, plugin / relative, label, force, write, report)
            for relative in bundle.manifest.get("shared_files", []):
                source, label = bundle.directory / relative, f"{bundle.target}: {relative}"
                if relative in shared_seen:
                    if not filecmp.cmp(source, shared_seen[relative], shallow=False):
                        raise MergeError(f"shared file {relative} differs between {shared_seen[relative]} and {source}")
                    continue
                shared_seen[relative] = source
                _place(source, plugin / relative, label, force, write, report)
        for source, destination, label in compat.install:
            _place(source, destination, label, force, write, report)
        return report

    with tempfile.TemporaryDirectory(prefix="fprime-cfs-cosmos-merge-") as staging_name:
        staging = Path(staging_name)
        report = place_all(write=False)
        if dry_run:
            return report
        report = place_all(write=True)
    if new_text != plugin_file.read_bytes().decode():
        temporary = plugin_file.with_name(plugin_file.name + ".fprime-merge.tmp")
        temporary.write_bytes(new_text.encode())
        os.replace(temporary, plugin_file)
    return report
