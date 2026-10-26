import json

import pytest
from fprime_cosmos import cfs

from fprime_cfs_cosmos_generator import generator


def test_write_bundle_is_standalone(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    artifacts = generator.write_bundle(app, tmp_path, "AP", channelized=True)
    root = artifacts.directory
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) == [
        "lib/fprime_msg_id.rb",
        "lib/protocol_ccsds_checksum_compat.rb",
        "manifest.json",
        "plugin.fragment.txt",
        "targets/AP/cmd_tlm/fprime_cmd.txt",
        "targets/AP/cmd_tlm/fprime_tlm.txt",
        "targets/AP/target.txt",
    ]


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
    assert manifest["shared_files"] == ["lib/protocol_ccsds_checksum_compat.rb"]


def test_subpacketizer_mode_for_cosmos_6_10(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    root = generator.write_bundle(app, tmp_path, "AP", subpacketizer=True).directory
    telemetry = (root / "targets/AP/cmd_tlm/fprime_tlm.txt").read_text()
    assert "  SUBPACKETIZER fprime_subpacketizer.rb" in telemetry
    assert "  SUBPACKET\n  SUBPACKET" not in telemetry
    assert telemetry.count("<%= Ccsds.tlm(") == 2
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
    assert "packet.buffer.bytesize - 7" in source
