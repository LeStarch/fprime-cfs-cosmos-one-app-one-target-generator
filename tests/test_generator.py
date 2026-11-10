import dataclasses
import json
from unittest.mock import patch

import pytest

from fprime_cfs_cosmos_generator import cfs, generator


def test_write_bundle_is_standalone(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    artifacts = generator.write_bundle(app, tmp_path, "AP", channelized=True)
    root = artifacts.directory
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) == [
        "lib/fprime_ap_msg_id.rb",
        "lib/protocol_ccsds_checksum_compat.rb",
        "lib/protocol_fprime_channel_splitter.rb",
        "manifest.json",
        "plugin.fragment.txt",
        "targets/AP/cmd_tlm/fprime_cmd.txt",
        "targets/AP/cmd_tlm/fprime_tlm.txt",
        "targets/AP/target.txt",
    ]


def test_target_file_names_are_relative_to_cmd_tlm(dictionary, tmp_path):
    """COSMOS joins <target>/cmd_tlm/<name> itself; a cmd_tlm/ prefix makes plugin install fail"""
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP").directory
    expected = "LANGUAGE ruby\nCOMMANDS fprime_cmd.txt\nTELEMETRY fprime_tlm.txt\n"
    assert (root / "targets/AP/target.txt").read_text() == expected


def test_fixed_id_parameters_are_hidden(dictionary):
    """FPRIME_DESCRIPTOR/FPRIME_OPCODE are fixed per command; COSMOS fills ID parameters itself"""
    lines = generator.emit_commands(cfs.cfs_app(dictionary), "AP").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("  APPEND_ID_PARAMETER "):
            block = lines[index + 1 : index + 3]
            assert "    HIDDEN" in block, line


def test_string_length_parameters_are_hidden(dictionary):
    """COSMOS fills a VARIABLE_BIT_SIZE length item itself when the string is written; Command Sender shows
    it as a locked parameter unless it is HIDDEN. The string parameter itself stays visible."""
    lines = generator.emit_commands(cfs.cfs_app(dictionary), "AP").splitlines()
    length = next(index for index, line in enumerate(lines) if line.startswith("  APPEND_PARAMETER arg1_LENGTH "))
    string = next(index for index, line in enumerate(lines) if line.startswith("  APPEND_PARAMETER arg1 0 STRING"))
    assert "    HIDDEN" in lines[length + 1 : string]
    following = lines[string + 1 :]
    block = following[: next(i for i, line in enumerate(following) if not line.startswith("    "))]
    assert "    HIDDEN" not in block


def test_plugin_fragment_uses_only_plugin_txt_keywords():
    """openc3 PluginModel accepts VARIABLE/TARGET/INTERFACE blocks only; e.g. VARIABLE_DESCRIPTION is rejected"""
    keywords = {
        line.split()[0]
        for line in generator.emit_plugin_fragment("AP").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert keywords == {"VARIABLE", "TARGET", "MAP_TARGET", "PROTOCOL"}


def test_exact_ruby_helper_contract(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP").directory
    commands = (root / "targets/AP/cmd_tlm/fprime_cmd.txt").read_text()
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    assert "<%= Ccsds.cmd(@CMD_MID_STR, 0, 6) %>" in commands
    assert "<%= Ccsds.cmd(@CMD_MID_STR, 0, 48) %>" in commands
    assert "<%= Ccsds.tlm(@PACKETS_TLM_MID_STR) %>" in telemetry
    assert "<%= Ccsds.tlm(@CHANNELS_TLM_MID_STR) %>" in telemetry
    assert "CCSDS_SPARE" not in telemetry
    assert "SPARE_FOR_64_ALIGN" not in telemetry
    assert "SUBPACKET" not in telemetry


def test_flat_telemetry_identifies_each_record_by_mid_descriptor_and_id(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP").directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    packet = telemetry[telemetry.index("TELEMETRY <%= @APP_PREFIX_STR %> Health ") :].split("\n\n")[0]
    assert "<%= Ccsds.tlm(@PACKETS_TLM_MID_STR) %>" in packet
    assert "APPEND_ID_ITEM FPRIME_DESCRIPTOR 16 UINT 32 " in packet
    assert "APPEND_ID_ITEM FPRIME_PACKET_ID 16 UINT 1 " in packet
    channel = telemetry[telemetry.index("TELEMETRY <%= @APP_PREFIX_STR %> CfsCore.cmdDisp.CommandsDispatched ") :]
    channel = channel.split("\n\n")[0]
    assert "<%= Ccsds.tlm(@CHANNELS_TLM_MID_STR) %>" in channel
    assert "APPEND_ID_ITEM FPRIME_DESCRIPTOR 16 UINT 1 " in channel
    assert "APPEND_ID_ITEM FPRIME_CHANNEL_ID " in channel
    assert "FPRIME_PACKETS" not in telemetry
    assert "FPRIME_CHANNELS" not in telemetry
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["telemetry_mode"] == "flat"
    assert manifest["shared_files"] == [
        "lib/protocol_ccsds_checksum_compat.rb",
        "lib/protocol_fprime_channel_splitter.rb",
    ]


def test_subpacketizer_mode_for_cosmos_6_10(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP", subpacketizer=True).directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    assert "  SUBPACKETIZER fprime_subpacketizer.rb" in telemetry
    assert "  SUBPACKET\n  SUBPACKET" not in telemetry
    assert telemetry.count("<%= Ccsds.tlm(") == 3  # packets parent, channels parent, events
    assert (root / "lib/fprime_subpacketizer.rb").exists()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["telemetry_mode"] == "subpacketizer"
    assert manifest["minimum_cosmos_version"] == "6.10.0"


def test_manifest_and_plugin_fragment(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP").directory
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["target"] == "AP"
    assert manifest["function_code"] == 0
    assert manifest["message_ids"] == {
        "command": "0x1800",
        "packetized_telemetry": "0x0820",
        "channelized_telemetry": "0x0801",
        "events": "0x0802",
    }
    fragment = (root / "plugin.fragment.txt").read_text()
    assert "VARIABLE fprime_ap_target_name AP" in fragment
    assert "MAP_TARGET <%= fprime_ap_target_name %>" in fragment
    assert "PROTOCOL WRITE ProtocolCcsdsChecksumCompat" in fragment


def test_generation_is_deterministic(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    first = generator.write_bundle(app, tmp_path / "first", "AP").directory
    second = generator.write_bundle(app, tmp_path / "second", "AP").directory
    first_files = {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second_files = {path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()}
    assert first_files == second_files


def test_no_channelized_output(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP", channelized=False).directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    manifest = json.loads((root / "manifest.json").read_text())
    assert "FPRIME_CHANNELS" not in telemetry
    assert "channelized_telemetry" not in manifest["message_ids"]
    # events are not channel telemetry: they stay regardless of the channelized flag
    assert "TELEMETRY <%= @APP_PREFIX_STR %> FPRIME_EVENTS " in telemetry
    assert "@EVENTS_TLM_MID_STR = FprimeMsgId_AP::EVENTS_TLM_MID" in telemetry
    assert manifest["message_ids"]["events"] == "0x0802"
    assert "EVENTS_TLM_MID" in (root / "lib/fprime_ap_msg_id.rb").read_text()


def test_reserved_manifest_rejects_target_and_mid_collisions(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    target_collision = tmp_path / "target.json"
    target_collision.write_text(json.dumps({"target": "AP", "message_ids": {}}))
    with pytest.raises(generator.GeneratorError, match="Target name AP"):
        generator.write_bundle(app, tmp_path / "out", "AP", reserved_manifests=(target_collision,))

    mid_collision = tmp_path / "mid.json"
    mid_collision.write_text(json.dumps({"target": "OTHER", "message_ids": {"ordinary_command": "0x1800"}}))
    with pytest.raises(generator.GeneratorError, match=r"0x1800.*ordinary_command"):
        generator.write_bundle(app, tmp_path / "out", "AP", reserved_manifests=(mid_collision,))


def test_checksum_runtime_updates_length_and_both_field_names():
    source = (generator.RUNTIME / generator.CHECKSUM_PROTOCOL).read_text()
    assert "['SEC_CRC', 'CCSDS_CHECKSUM']" in source
    assert "packet.buffer.bytesize - CCSDS_LENGTH_OFFSET" in source and "CCSDS_LENGTH_OFFSET = 7" in source
    assert "find_item(packet, LENGTH_NAMES, 32, 16)" in source and "find_item(packet, CHECKSUM_NAMES, 56, 8)" in source


def test_fprime_items_are_big_endian_regardless_of_processor_endian(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP").directory
    for name in ("fprime_cmd.txt", "fprime_tlm.txt"):
        for line in (root / "targets/AP/cmd_tlm" / name).read_text().splitlines():
            tokens = line.split()
            if tokens and tokens[0].startswith("APPEND_") and tokens[3] in {"INT", "UINT", "FLOAT"}:
                assert tokens[-1] == "BIG_ENDIAN", line


def test_fragment_declares_target_and_channel_splitter(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP").directory
    fragment = (root / "plugin.fragment.txt").read_text()
    assert "TARGET AP <%= fprime_ap_target_name %>" in fragment
    assert "  PROTOCOL READ ProtocolFprimeChannelSplitter" in fragment
    assert (root / "lib/protocol_fprime_channel_splitter.rb").exists()
    flat_no_channels = generator.write_bundle(app, tmp_path / "nc", "AP", channelized=False).directory
    assert "ProtocolFprimeChannelSplitter" not in (flat_no_channels / "plugin.fragment.txt").read_text()
    assert not (flat_no_channels / "lib/protocol_fprime_channel_splitter.rb").exists()


def test_target_names_are_uppercased_and_output_is_guarded(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    assert generator.write_bundle(app, tmp_path, "App_A").directory.name == "APP_A"
    (tmp_path / "PLAIN").mkdir()
    (tmp_path / "PLAIN" / "keep.txt").write_text("not generated")
    with pytest.raises(generator.GeneratorError, match="not a generated bundle"):
        generator.write_bundle(app, tmp_path, "PLAIN")
    assert (tmp_path / "PLAIN" / "keep.txt").exists()


def test_big_endian_covers_scalars_and_arrays_only():
    lines = [
        'APPEND_ITEM a 32 UINT "desc"',
        'APPEND_ARRAY_ITEM b 32 UINT 128 "desc"',
        'APPEND_PARAMETER c 16 INT MIN MAX 0 "desc"',
        'APPEND_ARRAY_PARAMETER d 32 FLOAT 64 "desc"',
        'APPEND_ITEM s 0 STRING "desc"',
        'APPEND_ITEM e 8 UINT "desc" LITTLE_ENDIAN',
        "    META FPRIME_TYPE U32Type",
    ]
    out = generator._big_endian(lines)
    assert out[0:4] == [line + " BIG_ENDIAN" for line in lines[0:4]]
    assert out[4:] == lines[4:]


def test_every_runtime_file_is_stamped_as_generated():
    from fprime_cfs_cosmos_generator import merge

    files = [path for path in generator.RUNTIME.rglob("*") if path.is_file() and not path.name.endswith(".pyc")]
    assert len(files) >= 7
    for path in files:
        assert path.name.endswith(".rb") and merge._generated(path), path.name


def _limits(data_type, bit_size, **bounds):
    from fprime_gds.common.models.serialize.numerical_types import F64Type, I32Type, U32Type
    from fprime_gds.common.templates.ch_template import ChTemplate

    from fprime_cfs_cosmos_generator import emit
    from fprime_cfs_cosmos_generator.items import CosmosItem

    type_object = {"UINT": U32Type, "INT": I32Type, "FLOAT": F64Type}[data_type]
    channel = ChTemplate(
        1,
        "ch",
        "comp",
        type_object,
        low_red=bounds.get("rl"),
        low_yellow=bounds.get("yl"),
        high_yellow=bounds.get("yh"),
        high_red=bounds.get("rh"),
    )
    lines = emit._channel_modifiers(channel, CosmosItem("ch", bit_size, data_type))
    limits = [line for line in lines if line.startswith("LIMITS")]
    return limits[0].removeprefix("LIMITS DEFAULT 1 ENABLED ") if limits else None


@pytest.mark.parametrize(
    ("data_type", "bit_size", "bounds", "expected"),
    [
        ("UINT", 32, {"rh": 1}, "-1e308 -1e308 1 1"),  # one-sided red high must not flag 0 as RED_LOW
        ("UINT", 8, {"yh": 5}, "-1e308 -1e308 5 1e308"),  # yellow only: above 5 is yellow, never red
        ("UINT", 16, {"rl": 2}, "2 2 1e308 1e308"),
        ("INT", 32, {"yl": -3}, "-1e308 -3 1e308 1e308"),
        ("INT", 16, {"rl": -10, "rh": 10}, "-10 -10 10 10"),
        ("FLOAT", 64, {"yl": -1.5, "yh": 1.5}, "-1e308 -1.5 1.5 1e308"),
        ("FLOAT", 32, {"rl": -2.0, "yl": -1.0, "yh": 1.0, "rh": 2.0}, "-2.0 -1.0 1.0 2.0"),
        ("UINT", 32, {}, None),
    ],
)
def test_limits_leave_unspecified_sides_open(data_type, bit_size, bounds, expected):
    assert _limits(data_type, bit_size, **bounds) == expected


def test_events_packet_is_the_opaque_record_identified_by_mid_and_descriptor(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    assert app.events_mid == 0x0802
    root = generator.write_bundle(app, tmp_path, "AP").directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    events = telemetry[telemetry.index("TELEMETRY <%= @APP_PREFIX_STR %> FPRIME_EVENTS ") :].split("\n\n")[0]
    # the wire layout the fprime-cosmos cosmos transport relies on: RECORD starts right after the descriptor
    assert events.splitlines() == [
        "TELEMETRY <%= @APP_PREFIX_STR %> FPRIME_EVENTS <%= Globals::Cfg.processor_endian %> "
        '"F Prime event record (opaque; decoded by fprime-gds)"',
        "  <%= Ccsds.tlm(@EVENTS_TLM_MID_STR) %>",
        '  APPEND_ID_ITEM FPRIME_DESCRIPTOR 16 UINT 2 "F Prime packet descriptor" BIG_ENDIAN',
        '    META FPRIME_TYPE "FwPacketDescriptorType"',
        '  APPEND_ITEM RECORD 0 BLOCK "Event id, time and arguments as serialized by F Prime"',
    ]
    assert "@EVENTS_TLM_MID_STR = FprimeMsgId_AP::EVENTS_TLM_MID" in telemetry
    assert "  EVENTS_TLM_MID = '0x0802'" in (root / "lib/fprime_ap_msg_id.rb").read_text()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["message_ids"]["events"] == "0x0802"
    subpacketized = generator.write_bundle(app, tmp_path / "sub", "AP", subpacketizer=True).directory
    sub_tlm = (subpacketized / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    sub_events = sub_tlm[sub_tlm.index("TELEMETRY <%= @APP_PREFIX_STR %> FPRIME_EVENTS ") :].split("\n\n")[0]
    assert "@EVENTS_TLM_MID_STR = FprimeMsgId_AP::EVENTS_TLM_MID" in sub_tlm
    assert sub_events.splitlines() == events.splitlines()  # a complete CCSDS packet, not a SUBPACKET
    assert json.loads((subpacketized / "manifest.json").read_text())["message_ids"]["events"] == "0x0802"


def test_events_packet_is_skipped_with_a_warning_without_a_log_apid(dictionary, tmp_path):
    with (
        patch.object(dictionary, "apids", {k: v for k, v in dictionary.apids.items() if k != "FW_PACKET_LOG"}),
        pytest.warns(UserWarning, match="FPRIME_EVENTS is not generated"),
    ):
        app = cfs.cfs_app(dictionary)
    assert app.events_mid is None
    assert "events" not in app.message_ids()
    root = generator.write_bundle(app, tmp_path, "AP").directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    assert "FPRIME_EVENTS" not in telemetry
    assert "EVENTS_TLM_MID" not in telemetry  # a preamble reference without the constant breaks plugin install
    assert "EVENTS_TLM_MID" not in (root / "lib/fprime_ap_msg_id.rb").read_text()
    assert "events" not in json.loads((root / "manifest.json").read_text())["message_ids"]


@pytest.mark.parametrize("name", ["fprime_events", "FPRIME_PACKETS", "Fprime_Channels"])
def test_reserved_packet_names_are_refused(dictionary, tmp_path, name):
    app = cfs.cfs_app(dictionary)
    packet = app.dictionary.packets[0]
    with (
        patch.object(packet, "get_name", return_value=name),
        pytest.raises(generator.GeneratorError, match="collides"),
    ):
        generator.write_bundle(app, tmp_path, "AP")
    assert not (tmp_path / "AP").exists()  # refused before anything is written: the output stays reusable
    assert generator.write_bundle(app, tmp_path, "AP").manifest_path.is_file()


def test_duplicate_message_ids_are_refused(dictionary, tmp_path):
    app = dataclasses.replace(cfs.cfs_app(dictionary), events_mid=cfs.cfs_app(dictionary).packets_mid)
    with pytest.raises(generator.GeneratorError, match="must be distinct"):
        generator.write_bundle(app, tmp_path, "AP")


def test_events_mid_without_a_log_packet_type_fails_loudly(dictionary, tmp_path):
    types = {k: v for k, v in dictionary.layout.packet_types.items() if k != "FW_PACKET_LOG"}
    with patch.object(dictionary, "layout", dataclasses.replace(dictionary.layout, packet_types=types)):
        with pytest.warns(UserWarning):
            app = dataclasses.replace(cfs.cfs_app(dictionary), events_mid=0x0802)  # the cfs_app() guard bypassed
        with pytest.raises(generator.GeneratorError, match="FW_PACKET_LOG"):
            generator.write_bundle(app, tmp_path, "AP")
    assert not (tmp_path / "AP" / "manifest.json").exists()


def test_events_packet_is_skipped_without_a_log_packet_type(dictionary, tmp_path):
    types = {k: v for k, v in dictionary.layout.packet_types.items() if k != "FW_PACKET_LOG"}
    with (
        patch.object(dictionary, "layout", dataclasses.replace(dictionary.layout, packet_types=types)),
        pytest.warns(UserWarning, match="FPRIME_EVENTS is not generated"),
    ):
        assert dictionary.layout.log_descriptor is None
        app = cfs.cfs_app(dictionary)
    assert app.events_mid is None
    assert "events" not in app.message_ids()
    root = generator.write_bundle(app, tmp_path, "AP").directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    assert "FPRIME_EVENTS" not in telemetry
    assert "EVENTS_TLM_MID" not in telemetry  # a preamble reference without the constant breaks plugin install


def test_events_apid_must_be_eleven_bits(dictionary):
    with (
        patch.object(dictionary, "apids", {**dictionary.apids, "FW_PACKET_LOG": 0x800}),
        pytest.raises(cfs.DictionaryError, match="FW_PACKET_LOG"),
    ):
        cfs.cfs_app(dictionary)


def test_reserved_manifest_rejects_the_events_mid(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    assert app.message_ids()["events"] == 0x0802
    manifest = tmp_path / "other.json"
    manifest.write_text(json.dumps({"target": "OTHER", "message_ids": {"command": "0x0802"}}))
    with pytest.raises(generator.GeneratorError, match="0x0802 \\(events\\)"):
        generator.write_bundle(app, tmp_path / "out", "AP", reserved_manifests=(manifest,))
