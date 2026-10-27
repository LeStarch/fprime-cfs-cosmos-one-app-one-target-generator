import json
import textwrap

import pytest

from fprime_cfs_cosmos_generator import cfs, generator, merge

PLUGIN = textwrap.dedent(
    """\
    VARIABLE cfs_endianness LITTLE_ENDIAN
    VARIABLE_DESCRIPTION "Processor endianness"

    <% include_tlm = true %>
    INTERFACE CFS_INTF udp_interface.rb host 1234 1235 nil nil 128 10.0 nil
        MAP_TARGET TO
        MAP_TARGET SCH
        PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil

    TARGET CFS_FLEET CFS_FLEET
    """
)


@pytest.fixture
def two_bundles(dictionary, tmp_path):
    app = cfs.cfs_app(dictionary)
    first = generator.write_bundle(app, tmp_path / "gen", "APP_A").directory
    second = generator.write_bundle(app, tmp_path / "gen", "APP_B").directory
    manifest = json.loads((second / "manifest.json").read_text())
    manifest["message_ids"] = {"command": "0x1900", "packetized_telemetry": "0x0920", "channelized_telemetry": "0x0901"}
    (second / "manifest.json").write_text(json.dumps(manifest))
    return first, second


@pytest.fixture
def plugin(tmp_path):
    root = tmp_path / "plugin"
    (root / "lib").mkdir(parents=True)
    (root / "targets" / "TO").mkdir(parents=True)
    (root / "plugin.txt").write_text(PLUGIN)
    return root


def test_merge_places_files_and_edits_plugin_txt(two_bundles, plugin):
    report = merge.merge(plugin, list(two_bundles))
    text = (plugin / "plugin.txt").read_text()
    assert (plugin / "targets/APP_A/cmd_tlm/fprime_cmd.txt").exists()
    assert (plugin / "targets/APP_B/cmd_tlm/fprime_tlm.txt").exists()
    assert (plugin / "targets/TO").exists()
    assert (plugin / "lib/fprime_app_a_msg_id.rb").exists()
    assert (plugin / "lib/fprime_app_b_msg_id.rb").exists()
    assert (plugin / "lib/protocol_ccsds_checksum_compat.rb").exists()
    lines = text.splitlines()
    assert lines[:4] == [
        "VARIABLE cfs_endianness LITTLE_ENDIAN",
        'VARIABLE_DESCRIPTION "Processor endianness"',
        "VARIABLE fprime_app_a_target_name APP_A",
        'VARIABLE_DESCRIPTION "COSMOS target name for the F Prime application APP_A"',
    ]
    before_interface = text[: text.index("INTERFACE CFS_INTF")]
    assert "TARGET APP_A <%= fprime_app_a_target_name %>\n" in before_interface
    assert "TARGET APP_B <%= fprime_app_b_target_name %>\n" in before_interface
    assert before_interface.index("TARGET APP_B") < before_interface.index("<% include_tlm")
    interface = text[text.index("INTERFACE CFS_INTF") : text.index("TARGET CFS_FLEET")]
    assert "    MAP_TARGET <%= fprime_app_a_target_name %>\n" in interface
    assert "    MAP_TARGET <%= fprime_app_b_target_name %>\n" in interface
    assert interface.count("PROTOCOL WRITE ProtocolCcsdsChecksumCompat") == 1
    assert interface.count("PROTOCOL READ ProtocolFprimeChannelSplitter") == 1
    assert interface.index("LengthProtocol") < interface.index("fprime_app_a_target_name")
    assert "TARGET CFS_FLEET CFS_FLEET" in text
    assert report.interface == "CFS_INTF"
    assert len(report.copied) == 6


def test_merge_is_idempotent(two_bundles, plugin):
    merge.merge(plugin, list(two_bundles))
    before = (plugin / "plugin.txt").read_text()
    report = merge.merge(plugin, list(two_bundles))
    assert (plugin / "plugin.txt").read_text() == before
    assert report.copied == []
    assert report.plugin_additions == []
    assert len(report.unchanged) == 6


def test_merge_rejects_mid_and_target_collisions(two_bundles, plugin, tmp_path):
    first, second = two_bundles
    manifest = json.loads((second / "manifest.json").read_text())
    manifest["message_ids"]["command"] = "0x1800"
    (second / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(merge.MergeError, match="0x1800"):
        merge.merge(plugin, [first, second])
    reserved = tmp_path / "ordinary.json"
    reserved.write_text(json.dumps({"target": "TO", "message_ids": {"hk": "0x0820"}}))
    with pytest.raises(merge.MergeError, match="0x0820"):
        merge.merge(plugin, [first], reserved_manifests=(reserved,))
    assert not (plugin / "targets/APP_A").exists()


def test_merge_rejects_different_existing_target_unless_forced(two_bundles, plugin):
    first, _second = two_bundles
    (plugin / "targets/APP_A/cmd_tlm").mkdir(parents=True)
    (plugin / "targets/APP_A/cmd_tlm/fprime_cmd.txt").write_text("stale generated output\n")
    (plugin / "targets/APP_A/target.txt").write_text("stale\n")
    with pytest.raises(merge.MergeError, match="--force"):
        merge.merge(plugin, [first])
    merge.merge(plugin, [first], force=True)
    assert (plugin / "targets/APP_A/target.txt").read_text() == (first / "targets/APP_A/target.txt").read_text()


def test_interface_selection(two_bundles, plugin):
    first, _second = two_bundles
    text = PLUGIN + "\nINTERFACE OTHER_INTF tcpip_client_interface.rb host 1 2\n    MAP_TARGET X\n"
    (plugin / "plugin.txt").write_text(text)
    with pytest.raises(merge.MergeError, match="--interface"):
        merge.merge(plugin, [first])
    merge.merge(plugin, [first], interface="CFS_INTF")
    merged = (plugin / "plugin.txt").read_text()
    other = merged[merged.index("INTERFACE OTHER_INTF") :]
    assert "fprime_app_a_target_name" not in other


def test_dry_run_writes_nothing(two_bundles, plugin):
    report = merge.merge(plugin, list(two_bundles), dry_run=True)
    assert (plugin / "plugin.txt").read_text() == PLUGIN
    assert not (plugin / "targets/APP_A").exists()
    assert len(report.copied) == 6
    assert any("MAP_TARGET" in line for line in report.plugin_additions)


def test_manifest_paths_are_confined_to_the_bundle(two_bundles, plugin):
    first, _second = two_bundles
    manifest = json.loads((first / "manifest.json").read_text())
    manifest["shared_files"] = ["lib/../../etc/passwd"]
    (first / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(merge.MergeError, match="unexpected file"):
        merge.merge(plugin, [first])
    manifest["shared_files"] = ["targets/TO"]
    (first / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(merge.MergeError, match="unexpected file"):
        merge.merge(plugin, [first])


def test_crlf_plugin_keeps_line_endings(two_bundles, plugin):
    first, _second = two_bundles
    (plugin / "plugin.txt").write_bytes(PLUGIN.replace("\n", "\r\n").encode())
    merge.merge(plugin, [first])
    data = (plugin / "plugin.txt").read_bytes()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")
    assert b"MAP_TARGET <%= fprime_app_a_target_name %>\r\n" in data


def test_merge_writes_nothing_when_a_later_bundle_conflicts(two_bundles, plugin):
    first, second = two_bundles
    (plugin / "targets/APP_B/cmd_tlm").mkdir(parents=True)
    (plugin / "targets/APP_B/target.txt").write_text("stale\n")
    with pytest.raises(merge.MergeError, match="--force"):
        merge.merge(plugin, [first, second])
    assert not (plugin / "targets/APP_A").exists()
    assert not (plugin / "lib/fprime_app_a_msg_id.rb").exists()
    assert (plugin / "plugin.txt").read_text() == PLUGIN


def test_force_never_replaces_ordinary_cfs_targets(two_bundles, plugin):
    first, _second = two_bundles
    (plugin / "targets/APP_A/cmd_tlm").mkdir(parents=True)
    (plugin / "targets/APP_A/cmd_tlm/to_lab_cmd.txt").write_text("ordinary cFS\n")
    with pytest.raises(merge.MergeError, match="not generated"):
        merge.merge(plugin, [first], force=True)
    assert (plugin / "targets/APP_A/cmd_tlm/to_lab_cmd.txt").exists()


def test_interface_exact_match_wins_over_substring(two_bundles, plugin):
    first, _second = two_bundles
    text = PLUGIN + "\nINTERFACE CFS_INTF_BACKUP udp_interface.rb host 1 2\n    MAP_TARGET X\n"
    (plugin / "plugin.txt").write_text(text)
    merge.merge(plugin, [first], interface="CFS_INTF")
    merged = (plugin / "plugin.txt").read_text()
    assert "fprime_app_a_target_name" not in merged[merged.index("INTERFACE CFS_INTF_BACKUP") :]


def test_skip_target_and_reserved_manifests_without_target(two_bundles, plugin, tmp_path):
    first, _second = two_bundles
    inventories = []
    for index in range(2):
        path = tmp_path / f"inventory{index}.json"
        path.write_text(json.dumps({"message_ids": {"hk": f"0x{0x0900 + index:04x}"}}))
        inventories.append(path)
    merge.merge(plugin, [first], reserved_manifests=tuple(inventories), skip_target=True)
    assert "TARGET APP_A" not in (plugin / "plugin.txt").read_text()


def test_target_line_goes_before_an_erb_block_wrapping_the_interface(two_bundles, plugin):
    first, _second = two_bundles
    text = textwrap.dedent(
        """\
        VARIABLE n 2
        <% (1..n).each do |i| %>
            <% ip = "127.0.0.1" %>
            INTERFACE CFS-<%= i %>_INTF udp_interface.rb <%= ip %>
                MAP_TARGET CPU<%= i %>
        <% end %>
        """
    )
    (plugin / "plugin.txt").write_text(text)
    merge.merge(plugin, [first])
    lines = (plugin / "plugin.txt").read_text().splitlines()
    assert lines[1] == "VARIABLE fprime_app_a_target_name APP_A"
    assert lines[3] == "TARGET APP_A <%= fprime_app_a_target_name %>"
    assert lines[4] == "<% (1..n).each do |i| %>"
    assert lines[-2:] == ["        PROTOCOL READ ProtocolFprimeChannelSplitter", "<% end %>"]


def test_target_line_without_variables_goes_before_the_erb_block(two_bundles, plugin):
    first, _second = two_bundles
    (plugin / "plugin.txt").write_text("<% [1].each do |i| %>\n    INTERFACE I_<%= i %> udp_interface.rb\n<% end %>\n")
    merge.merge(plugin, [first])
    lines = (plugin / "plugin.txt").read_text().splitlines()
    assert lines[2] == "TARGET APP_A <%= fprime_app_a_target_name %>"
    assert lines[3] == "<% [1].each do |i| %>"
