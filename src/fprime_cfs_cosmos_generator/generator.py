"""Generate an independent COSMOS target bundle for one F Prime cFS application."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from . import cfs, emit
from .dictionary import FprimeDictionary

RUNTIME = resources.files("fprime_cfs_cosmos_generator") / "runtime"
TARGET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
SUBPACKETIZER = "fprime_subpacketizer.rb"
CHECKSUM_PROTOCOL = "protocol_ccsds_checksum_compat.rb"
CHANNEL_SPLITTER = "protocol_fprime_channel_splitter.rb"
COMMANDS_FILE = "fprime_cmd.txt"
TELEMETRY_FILE = "fprime_tlm.txt"
MESSAGE_IDS_FILE = "fprime_{target}_msg_id.rb"
MESSAGE_IDS_MODULE = "FprimeMsgId_{target}"
EVENTS_PACKET = "FPRIME_EVENTS"  # packet and item names are the wire contract with fprime-cosmos's cosmos transport
EVENT_RECORD_ITEM = "RECORD"
RESERVED_PACKETS = frozenset({EVENTS_PACKET, "FPRIME_PACKETS", "FPRIME_CHANNELS"})


class GeneratorError(Exception):
    """The dictionary or requested output cannot form a standalone target bundle."""


@dataclass(frozen=True)
class BundleArtifacts:
    directory: Path
    target_name: str
    manifest_path: Path


def _ruby_string(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def message_ids_file(target_name: str) -> str:
    """Per-target Ruby file so several F Prime apps can share one plugin lib directory"""
    return MESSAGE_IDS_FILE.format(target=target_name.lower())


def _preamble(target_name: str, identifiers: dict[str, str]) -> list[str]:
    lines = [
        "<%",
        "  require 'global_defs'",
        "  require 'cfs_ccsds'",
        "  require 'fsw_msg_id'",
        "  require 'fsw_config_param'",
        f"  require '{message_ids_file(target_name)[:-3]}'",
        "",
        f"  @APP_PREFIX_STR = {_ruby_string(target_name)}",
    ]
    module = MESSAGE_IDS_MODULE.format(target=target_name)
    lines.extend(f"  @{name} = {module}::{constant}" for name, constant in identifiers.items())
    lines.extend(["", "%>", ""])
    return lines


NUMERIC_TYPES = {"INT", "UINT", "FLOAT"}
ITEM_KEYWORDS = (
    "APPEND_ITEM",
    "APPEND_ID_ITEM",
    "APPEND_ARRAY_ITEM",
    "APPEND_PARAMETER",
    "APPEND_ID_PARAMETER",
    "APPEND_ARRAY_PARAMETER",
)


def _big_endian(lines: list[str]) -> list[str]:
    """F Prime serializes big-endian regardless of the cFS processor endianness the packet defaults to"""
    result = []
    for line in lines:
        tokens = line.split()
        numeric = len(tokens) > 3 and tokens[0] in ITEM_KEYWORDS and tokens[3] in NUMERIC_TYPES
        if numeric and not tokens[-1].endswith("_ENDIAN"):
            line = f"{line.rstrip()} BIG_ENDIAN"
        result.append(line)
    return result


def _payload_bytes(command, app: cfs.CfsApp) -> int:
    bits = app.dictionary.layout.descriptor_bits + app.dictionary.layout.opcode_bits
    bits += sum(arg_type.getMaxSize() * 8 for _name, _description, arg_type in command.get_args())
    if bits % 8:
        raise GeneratorError(f"{command.get_full_name()}: F Prime command payload is not byte aligned ({bits} bits)")
    return bits // 8


def emit_commands(app: cfs.CfsApp, target_name: str) -> str:
    lines = _preamble(target_name, {"CMD_MID_STR": "CMD_MID"})
    for command in app.dictionary.commands:
        lines.append(
            f"COMMAND <%= @APP_PREFIX_STR %> {emit.full_name(command)} "
            f"<%= Globals::Cfg.processor_endian %> {emit.quote(command.get_description())}"
        )
        lines.append(f"  <%= Ccsds.cmd(@CMD_MID_STR, {app.function_code}, {_payload_bytes(command, app)}) %>")
        lines.extend(_big_endian(emit.emit_command(command, app.dictionary.layout)[1:]))
        lines.append("")
    return "\n".join(lines)


def _telemetry_header(name: str, mid_variable: str, description: str) -> list[str]:
    return [
        f"TELEMETRY <%= @APP_PREFIX_STR %> {name} <%= Globals::Cfg.processor_endian %> {emit.quote(description)}",
        f"  <%= Ccsds.tlm(@{mid_variable}) %>",
    ]


def _telemetry_parent(
    name: str,
    mid_variable: str,
    description: str,
    block_name: str,
    block_description: str,
    descriptor: list[str] = (),
) -> list[str]:
    """COSMOS 6.10+ only: the F Prime records are a block that the subpacketizer resolves"""
    lines = _telemetry_header(name, mid_variable, description)
    lines.extend(descriptor)
    lines.append(f"  SUBPACKETIZER {SUBPACKETIZER}")
    lines.append(f"  APPEND_ITEM {block_name} 0 BLOCK {emit.quote(block_description)}")
    lines.append("    HIDDEN")
    return lines


def _packet_description(packet) -> str:
    return f"F Prime packetized telemetry {packet.get_name()}"


def _telemetry_packet(packet, app: cfs.CfsApp, subpacketizer: bool) -> list[str]:
    """One F Prime packet: a subpacket (6.10+) or a complete CCSDS packet identified by MID, descriptor, packet id"""
    body = _big_endian(emit.emit_packet(packet, app.dictionary.layout, cfs.TIME_CONVERSION)[1:])
    if subpacketizer:
        header = _telemetry_header(emit.full_name(packet), "PACKETS_TLM_MID_STR", _packet_description(packet))[:1]
        return header + ["  SUBPACKET"] + body
    return _telemetry_header(emit.full_name(packet), "PACKETS_TLM_MID_STR", _packet_description(packet)) + body


def _telemetry_channel(channel, app: cfs.CfsApp, subpacketizer: bool) -> list[str]:
    """One F Prime channel: a subpacket (6.10+) or a complete CCSDS packet identified by MID, descriptor, channel id"""
    layout = app.dictionary.layout
    body = _big_endian(emit.emit_channel(channel, layout, cfs.TIME_CONVERSION))
    if subpacketizer:
        header = _telemetry_header(emit.full_name(channel), "CHANNELS_TLM_MID_STR", channel.get_ch_desc())[:1]
        return header + body[1:]
    descriptor = _big_endian(emit._item_lines(emit._descriptor_item(layout), layout.telemetry_descriptor))
    return (
        _telemetry_header(emit.full_name(channel), "CHANNELS_TLM_MID_STR", channel.get_ch_desc())
        + descriptor
        + body[2:]
    )


def _events_packet(app: cfs.CfsApp) -> list[str]:
    """The F Prime event record (id, time, arguments) as one opaque block, identified by the events MID and the log
    descriptor; the fprime-gds event decoder (through the fprime-cosmos ``cosmos`` data transport) decodes it exactly,
    so no event text is reconstructed in COSMOS. Without it nasa's ID-less file packets swallow the event packets."""
    layout = app.dictionary.layout
    if layout.log_descriptor is None:
        raise GeneratorError(f"{app.name}: events_mid is set but the packet-type enumeration has no FW_PACKET_LOG")
    description = "F Prime event record (opaque; decoded by fprime-gds)"
    record_description = emit.quote("Event id, time and arguments as serialized by F Prime")
    lines = _telemetry_header(EVENTS_PACKET, "EVENTS_TLM_MID_STR", description)
    lines.extend(_big_endian(emit._item_lines(emit._descriptor_item(layout), layout.log_descriptor)))
    lines.append(f"  APPEND_ITEM {EVENT_RECORD_ITEM} 0 BLOCK {record_description}")
    return lines


def emit_telemetry(app: cfs.CfsApp, target_name: str, channelized: bool = True, subpacketizer: bool = False) -> str:
    """Flat mode (default) works on every COSMOS 6.x; subpacketizer mode needs COSMOS 6.10+ (SUBPACKET keyword)"""
    identifiers = {"PACKETS_TLM_MID_STR": "PACKETS_TLM_MID"}
    if channelized:
        identifiers["CHANNELS_TLM_MID_STR"] = "CHANNELS_TLM_MID"
    if app.events_mid is not None:
        identifiers["EVENTS_TLM_MID_STR"] = "EVENTS_TLM_MID"
    blocks = ["\n".join(_preamble(target_name, identifiers)).rstrip()]
    if app.events_mid is not None:
        blocks.append("\n".join(_events_packet(app)))
    if subpacketizer:
        blocks.append(
            "\n".join(
                _telemetry_parent(
                    "FPRIME_PACKETS",
                    "PACKETS_TLM_MID_STR",
                    "F Prime packetized telemetry",
                    "PACKET",
                    "F Prime telemetry packet",
                )
            )
        )
    blocks.extend("\n".join(_telemetry_packet(packet, app, subpacketizer)) for packet in app.dictionary.packets)
    if channelized:
        if subpacketizer:
            blocks.append(
                "\n".join(
                    _telemetry_parent(
                        "FPRIME_CHANNELS",
                        "CHANNELS_TLM_MID_STR",
                        "F Prime channelized telemetry",
                        "CHANNELS",
                        "F Prime channel records (id, time, value)...",
                        _big_endian(
                            emit._item_lines(
                                emit._descriptor_item(app.dictionary.layout),
                                app.dictionary.layout.telemetry_descriptor,
                            )
                        ),
                    )
                )
            )
        blocks.extend("\n".join(_telemetry_channel(channel, app, subpacketizer)) for channel in app.dictionary.channels)
    return "\n\n".join(blocks) + "\n"


def emit_message_ids(app: cfs.CfsApp, target_name: str, channelized: bool = True) -> str:
    lines = [
        f"# Generated F Prime cFS message identifiers for {target_name}. Do not edit.",
        f"module {MESSAGE_IDS_MODULE.format(target=target_name)}",
        f"  CMD_MID = '{app.command_mid:#06x}'",
        f"  PACKETS_TLM_MID = '{app.packets_mid:#06x}'",
    ]
    if channelized:
        lines.append(f"  CHANNELS_TLM_MID = '{app.channels_mid:#06x}'")
    if app.events_mid is not None:
        lines.append(f"  EVENTS_TLM_MID = '{app.events_mid:#06x}'")
    lines.extend(["end", ""])
    return "\n".join(lines)


def emit_target() -> str:
    return "\n".join(
        [
            "LANGUAGE ruby",
            f"COMMANDS {COMMANDS_FILE}",
            f"TELEMETRY {TELEMETRY_FILE}",
            "",
        ]
    )


def emit_plugin_fragment(target_name: str, channelized: bool = True, subpacketizer: bool = False) -> str:
    variable = f"fprime_{target_name.lower()}_target_name"
    protocols = ["  PROTOCOL WRITE ProtocolCcsdsChecksumCompat"]
    if channelized and not subpacketizer:
        protocols.append("  PROTOCOL READ ProtocolFprimeChannelSplitter")
    return "\n".join(
        [
            "# Merge the VARIABLE at plugin scope and the TARGET before the shared INTERFACE.",
            f"VARIABLE {variable} {target_name}",
            f"TARGET {target_name} <%= {variable} %>",
            "",
            "# Merge these lines under the existing shared UDP INTERFACE.",
            f"  MAP_TARGET <%= {variable} %>",
            *protocols,
            "",
        ]
    )


def _runtime_files(channelized: bool, subpacketizer: bool) -> tuple[str, ...]:
    files = [CHECKSUM_PROTOCOL]
    if subpacketizer:
        files.append(SUBPACKETIZER)
    elif channelized:
        files.append(CHANNEL_SPLITTER)
    return tuple(files)


def _copy_runtime(output: Path, channelized: bool, subpacketizer: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name in _runtime_files(channelized, subpacketizer):
        with resources.as_file(RUNTIME / name) as source:
            shutil.copy2(source, output / name)


def _manifest(
    app: cfs.CfsApp, target_name: str, channelized: bool, subpacketizer: bool, dictionary: FprimeDictionary
) -> dict:
    mids = {"command": app.command_mid, "packetized_telemetry": app.packets_mid}
    if channelized:
        mids["channelized_telemetry"] = app.channels_mid
    if app.events_mid is not None:
        mids["events"] = app.events_mid
    return {
        "schema_version": 1,
        "target": target_name,
        "application": app.name,
        "dictionary": dictionary.path.name,
        "dictionary_sha256": hashlib.sha256(dictionary.path.read_bytes()).hexdigest(),
        "function_code": app.function_code,
        "telemetry_mode": "subpacketizer" if subpacketizer else "flat",
        "minimum_cosmos_version": "6.10.0" if subpacketizer else "6.0.0",
        "message_ids": {name: f"{mid:#06x}" for name, mid in mids.items()},
        "target_files": [f"targets/{target_name}", f"lib/{message_ids_file(target_name)}"],
        "shared_files": [f"lib/{name}" for name in _runtime_files(channelized, subpacketizer)],
        "required_helpers": ["global_defs", "cfs_ccsds", "fsw_msg_id", "fsw_config_param"],
    }


def _check_packet_names(app: cfs.CfsApp) -> None:
    """A dictionary packet named like one of the bundle's own packets would define it twice in COSMOS"""
    for packet in app.dictionary.packets:
        if emit.full_name(packet).upper() in RESERVED_PACKETS:
            raise GeneratorError(f"Dictionary packet {packet.get_name()!r} collides with the bundle's reserved packet")


def _check_reserved(app: cfs.CfsApp, target_name: str, channelized: bool, manifests: tuple[Path, ...]) -> None:
    requested = app.message_ids(channelized)
    if len(set(requested.values())) != len(requested):
        raise GeneratorError(f"{app.name}: command, telemetry and event message identifiers must be distinct")
    for path in manifests:
        try:
            reserved = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise GeneratorError(f"Cannot read reserved manifest {path}: {error}") from error
        if reserved.get("target") == target_name:
            raise GeneratorError(f"Target name {target_name} is already reserved by {path}")
        for reserved_kind, value in reserved.get("message_ids", {}).items():
            try:
                reserved_mid = int(value, 0) if isinstance(value, str) else int(value)
            except (TypeError, ValueError) as error:
                raise GeneratorError(f"Invalid message id {value!r} in reserved manifest {path}") from error
            for requested_kind, requested_mid in requested.items():
                if requested_mid == reserved_mid:
                    raise GeneratorError(
                        f"Message id {requested_mid:#06x} ({requested_kind}) is already reserved as "
                        f"{reserved_kind} by {path}"
                    )


def write_bundle(
    app: cfs.CfsApp,
    output: Path,
    target_name: str | None = None,
    channelized: bool = True,
    reserved_manifests: tuple[Path, ...] = (),
    subpacketizer: bool = False,
) -> BundleArtifacts:
    target_name = (target_name or app.name).upper()
    if not TARGET_NAME.fullmatch(target_name):
        raise GeneratorError(f"Target name {target_name!r} must contain only letters, digits, and underscores")
    _check_reserved(app, target_name, channelized, reserved_manifests)
    _check_packet_names(app)
    output = Path(output) / target_name
    if output.exists():
        if not (output / "manifest.json").is_file():
            raise GeneratorError(f"{output} exists and is not a generated bundle; refusing to replace it")
        shutil.rmtree(output)
    cmd_tlm = output / "targets" / target_name / "cmd_tlm"
    cmd_tlm.mkdir(parents=True)
    (cmd_tlm.parent / "target.txt").write_text(emit_target())
    (cmd_tlm / COMMANDS_FILE).write_text(emit_commands(app, target_name))
    (cmd_tlm / TELEMETRY_FILE).write_text(emit_telemetry(app, target_name, channelized, subpacketizer))
    lib = output / "lib"
    _copy_runtime(lib, channelized, subpacketizer)
    (lib / message_ids_file(target_name)).write_text(emit_message_ids(app, target_name, channelized))
    (output / "plugin.fragment.txt").write_text(emit_plugin_fragment(target_name, channelized, subpacketizer))
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(app, target_name, channelized, subpacketizer, app.dictionary), indent=2) + "\n"
    )
    return BundleArtifacts(output, target_name, manifest_path)


def build_bundle(
    dictionary_path: str | Path,
    output: str | Path,
    target_name: str | None = None,
    packet_set_name: str | None = None,
    channelized: bool = True,
    reserved_manifests: tuple[Path, ...] = (),
    subpacketizer: bool = False,
) -> BundleArtifacts:
    dictionary = FprimeDictionary(dictionary_path, packet_set_name)
    return write_bundle(
        cfs.cfs_app(dictionary), Path(output), target_name, channelized, reserved_manifests, subpacketizer
    )
