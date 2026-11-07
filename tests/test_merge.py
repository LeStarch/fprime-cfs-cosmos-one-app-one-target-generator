import json
import textwrap

import pytest

from fprime_cfs_cosmos_generator import cfs, generator, merge

PLUGIN = textwrap.dedent(
    """\
    VARIABLE cfs_endianness LITTLE_ENDIAN

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
    assert lines[:3] == [
        "VARIABLE cfs_endianness LITTLE_ENDIAN",
        "VARIABLE fprime_app_a_target_name APP_A",
        "VARIABLE fprime_app_b_target_name APP_B",
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
    # bundle mappings precede the plugin's own (its file packets have no ID items and would swallow F Prime packets)
    assert interface.index("fprime_app_a_target_name") < interface.index("fprime_app_b_target_name")
    assert interface.index("fprime_app_b_target_name") < interface.index("MAP_TARGET TO")
    assert interface.index("MAP_TARGET TO") < interface.index("LengthProtocol")
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
    assert lines[2] == "TARGET APP_A <%= fprime_app_a_target_name %>"
    assert lines[3] == "<% (1..n).each do |i| %>"
    assert lines[-2:] == ["        PROTOCOL READ ProtocolFprimeChannelSplitter", "<% end %>"]


def test_target_line_without_variables_goes_before_the_erb_block(two_bundles, plugin):
    first, _second = two_bundles
    (plugin / "plugin.txt").write_text("<% [1].each do |i| %>\n    INTERFACE I_<%= i %> udp_interface.rb\n<% end %>\n")
    merge.merge(plugin, [first])
    lines = (plugin / "plugin.txt").read_text().splitlines()
    assert lines[1] == "TARGET APP_A <%= fprime_app_a_target_name %>"
    assert lines[2] == "<% [1].each do |i| %>"


def test_merge_inherits_ignore_lines_from_existing_targets(two_bundles, plugin):
    """The plugin's hand-written targets hide the shared CCSDS header fields via target.txt; the F Prime
    targets use the same header helpers, so they inherit those lines (idempotent, opt-out)"""
    first, _second = two_bundles
    (plugin / "targets/TO/target.txt").write_text(
        "IGNORE_PARAMETER CCSDS_STREAMID\n  IGNORE_PARAMETER   CCSDS_SEQUENCE\nIGNORE_ITEM CCSDS_LENGTH\nREQUIRE x.rb\n"
    )
    merge.merge(plugin, [first])
    text = (plugin / "targets/APP_A/target.txt").read_text()
    assert text.startswith("LANGUAGE ruby\nCOMMANDS fprime_cmd.txt\nTELEMETRY fprime_tlm.txt\n")
    assert text.splitlines()[-3:] == [
        "IGNORE_PARAMETER CCSDS_STREAMID",
        "IGNORE_PARAMETER CCSDS_SEQUENCE",
        "IGNORE_ITEM CCSDS_LENGTH",
    ]
    assert "REQUIRE" not in text
    assert merge.merge(plugin, [first]).copied == []
    merge.merge(plugin, [first], force=True, inherit_ignores=False)
    assert "IGNORE" not in (plugin / "targets/APP_A/target.txt").read_text()


def test_write_protocol_is_declared_before_the_plugin_write_protocols(two_bundles):
    """COSMOS runs WRITE protocols in reverse declaration order; ours must run last to finalize length/checksum"""
    plugin = PLUGIN.replace(
        "    PROTOCOL READ LengthProtocol",
        "    PROTOCOL WRITE ProtocolCcsdsChecksum\n    PROTOCOL WRITE ProtocolCcsdsLength\n"
        "    PROTOCOL READ LengthProtocol",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(p) for p in two_bundles], None)
    block = [line.strip() for line in result.text.splitlines() if line.startswith("    ")]
    assert block.index("PROTOCOL WRITE ProtocolCcsdsChecksumCompat") < block.index(
        "PROTOCOL WRITE ProtocolCcsdsChecksum"
    )
    assert block.index("PROTOCOL READ ProtocolFprimeChannelSplitter") > block.index(
        "PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil"
    )
    assert "    PROTOCOL WRITE ProtocolCcsdsChecksumCompat" in result.additions
    again = merge.merge_plugin_text(result.text, [merge.load_bundle(p) for p in two_bundles], None)
    assert again.text == result.text and again.additions == []


def test_misplaced_write_protocol_from_an_older_merge_is_moved(two_bundles):
    plugin = PLUGIN.replace(
        "    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil",
        "    PROTOCOL WRITE ProtocolCcsdsLength\n    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n"
        "    MAP_TARGET <%= fprime_app_a_target_name %>\n    PROTOCOL WRITE ProtocolCcsdsChecksumCompat\n"
        "    PROTOCOL READ ProtocolFprimeChannelSplitter",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(p) for p in two_bundles], None)
    block = [line.strip() for line in result.text.splitlines() if line.startswith("    ")]
    assert block.count("PROTOCOL WRITE ProtocolCcsdsChecksumCompat") == 1
    assert block.index("PROTOCOL WRITE ProtocolCcsdsChecksumCompat") < block.index("PROTOCOL WRITE ProtocolCcsdsLength")
    assert result.additions


NASA_PLUGIN = textwrap.dedent(
    """\
    <% require 'cfs_packet_headers' %>
    <% require 'cfs_globals' %>
    VARIABLE cfs_endianness LITTLE_ENDIAN
    <% cfs_globals_set_endianness(cfs_endianness) %>

    # This should map to the IP address that GSW sends commands to (and listens to telemetry on)
    VARIABLE cfs_1_intf_ip 172.17.0.1
    VARIABLE cfs_2_intf_ip 172.17.0.1

    <% (1..$cfs_fleet_globals_num_cfs_targets).each do |cfs_cpu_index| %>

        # Declare a new instance of the CFS target
        TARGET CFS <%= cfs_target_name_from_cpu_num(cfs_cpu_index) %>

        # Select the appropriate IP config input variable (declared above) for this cpu number
        <% if cfs_cpu_index == 1 %>
            <% current_cfs_intf_ip = cfs_1_intf_ip %>
        <% elsif cfs_cpu_index == 2 %>
            <% current_cfs_intf_ip = cfs_2_intf_ip %>
        <% else %>
            # This line should cause a plugin compilation error
            <% current_cfs_intf_ip = error %>
        <% end %>

        # Set up the FSW cmd/tlm interface
        INTERFACE CFS-<%= cfs_cpu_index %>_INTF udp_interface.rb &
                <%= current_cfs_intf_ip %> &
                <%= cfs_get_port_num_cmd(cfs_cpu_index) %> &
                <%= cfs_get_port_num_tlm(cfs_cpu_index) %> &
                nil nil 128 nil nil
            MAP_TARGET <%= cfs_target_name_from_cpu_num(cfs_cpu_index) %>
            PROTOCOL WRITE ProtocolCcsdsChecksum
            PROTOCOL WRITE ProtocolCcsdsLength
            PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil
            PROTOCOL READ ProtocolEvsLog
    <% end %>

    # Instantiate one "fleet" target
    TARGET CFS_FLEET CFS_FLEET
    """
)

HELPERS = ("global_defs", "cfs_ccsds", "fsw_msg_id", "fsw_config_param")


@pytest.fixture
def nasa_plugin(tmp_path):
    root = tmp_path / "nasa"
    (root / "lib").mkdir(parents=True)
    (root / "targets" / "CFS" / "lib").mkdir(parents=True)
    (root / "targets" / "CFS" / "lib" / "cfs_packet_headers.rb").write_text("def cfs_cmd_hdr(*); end\n")
    (root / "targets" / "CFS" / "lib" / "cfs_globals.rb").write_text("$cfs_globals_endianness = 'LITTLE_ENDIAN'\n")
    (root / "plugin.txt").write_text(NASA_PLUGIN)
    return root


def _interface_block(text: str) -> list[str]:
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if "INTERFACE" in line)
    end = next(index for index, line in enumerate(lines) if index > start and not line.strip())
    return [line.strip() for line in lines[start + 1 : end]]


def test_map_target_order_is_bundle_first_and_stable_across_merge_orders(two_bundles):
    first, second = (merge.load_bundle(path) for path in two_bundles)
    ab = merge.merge_plugin_text(PLUGIN, [first, second], None)
    maps = [line for line in _interface_block(ab.text) if line.startswith("MAP_TARGET")]
    assert maps == [
        "MAP_TARGET <%= fprime_app_a_target_name %>",
        "MAP_TARGET <%= fprime_app_b_target_name %>",
        "MAP_TARGET TO",
        "MAP_TARGET SCH",
    ]
    assert ab.moves == [] and ab.warnings == []
    # re-merging in the other order keeps the file (existing bundle mappings keep their relative order)
    ba = merge.merge_plugin_text(ab.text, [second, first], None)
    assert ba.text == ab.text and ba.additions == [] and ba.moves == []
    # A, then B, then A+B appends B after A and is then stable
    a = merge.merge_plugin_text(PLUGIN, [first], None)
    b = merge.merge_plugin_text(a.text, [second], None)
    assert [line for line in _interface_block(b.text) if line.startswith("MAP_TARGET")] == maps
    both = merge.merge_plugin_text(b.text, [first, second], None)
    assert both.text == b.text and both.additions == []
    assert merge.merge_plugin_text(b.text, [second, first], None).text == b.text


def test_map_target_without_existing_mapping_follows_the_interface_continuation_lines(two_bundles):
    plugin = PLUGIN.replace(
        "INTERFACE CFS_INTF udp_interface.rb host 1234 1235 nil nil 128 10.0 nil\n"
        "    MAP_TARGET TO\n    MAP_TARGET SCH\n",
        "INTERFACE CFS_INTF udp_interface.rb &\n        host 1234 1235 &\n        nil nil 128 10.0 nil\n",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    block = result.text.splitlines()
    start = block.index("INTERFACE CFS_INTF udp_interface.rb &")
    assert block[start + 1 : start + 6] == [
        "        host 1234 1235 &",
        "        nil nil 128 10.0 nil",
        "    MAP_TARGET <%= fprime_app_a_target_name %>",
        "    PROTOCOL WRITE ProtocolCcsdsChecksumCompat",
        "    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil",
    ]
    again = merge.merge_plugin_text(result.text, [merge.load_bundle(two_bundles[0])], None)
    assert again.text == result.text and again.additions == []


def test_misplaced_map_target_from_an_older_merge_is_moved_and_reported(two_bundles, plugin):
    old = PLUGIN.replace(
        "    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil",
        "    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n    MAP_TARGET <%= fprime_app_a_target_name %>",
    )
    result = merge.merge_plugin_text(old, [merge.load_bundle(two_bundles[0])], None)
    block = _interface_block(result.text)
    assert block.count("MAP_TARGET <%= fprime_app_a_target_name %>") == 1
    assert block.index("MAP_TARGET <%= fprime_app_a_target_name %>") < block.index("MAP_TARGET TO")
    assert result.moves == ["MAP_TARGET <%= fprime_app_a_target_name %>"]
    assert "MAP_TARGET <%= fprime_app_a_target_name %>" not in [line.strip() for line in result.additions]
    (plugin / "plugin.txt").write_text(old)
    report = merge.merge(plugin, [two_bundles[0]])
    assert "plugin.txt ~ MAP_TARGET <%= fprime_app_a_target_name %> (moved)" in report.summary()
    assert (plugin / "plugin.txt").read_text() == result.text


def test_interface_inside_an_erb_loop_warns(two_bundles, nasa_plugin):
    report = merge.merge(nasa_plugin, list(two_bundles))
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert "<% (1..$cfs_fleet_globals_num_cfs_targets).each do |cfs_cpu_index| %>" in warning
    assert "APP_A, APP_B" in warning and "INTERFACE CFS-<%= cfs_cpu_index %>_INTF" in warning
    assert "WARNING" not in report.summary()  # the CLI prints warnings once, on stderr
    text = (nasa_plugin / "plugin.txt").read_text()
    block = _interface_block(text)
    assert block[:6] == [
        "<%= current_cfs_intf_ip %> &",
        "<%= cfs_get_port_num_cmd(cfs_cpu_index) %> &",
        "<%= cfs_get_port_num_tlm(cfs_cpu_index) %> &",
        "nil nil 128 nil nil",
        "MAP_TARGET <%= fprime_app_a_target_name %>",
        "MAP_TARGET <%= fprime_app_b_target_name %>",
    ]
    assert block[6] == "MAP_TARGET <%= cfs_target_name_from_cpu_num(cfs_cpu_index) %>"
    assert "        MAP_TARGET <%= fprime_app_a_target_name %>" in text  # indented like the plugin's own mapping
    # TARGET lines land before the loop, after the VARIABLEs
    assert text.index("VARIABLE cfs_2_intf_ip") < text.index("TARGET APP_A") < text.index("<% (1..")
    again = merge.merge(nasa_plugin, list(two_bundles))
    assert again.copied == [] and again.plugin_additions == [] and again.plugin_moves == []
    assert (nasa_plugin / "plugin.txt").read_text() == text


def test_loop_is_found_behind_a_nested_erb_if(two_bundles):
    plugin = NASA_PLUGIN.replace(
        "    TARGET CFS <%= cfs_target_name_from_cpu_num(cfs_cpu_index) %>\n",
        "    TARGET CFS <%= cfs_target_name_from_cpu_num(cfs_cpu_index) %>\n<% if cfs_cpu_index == 1 %>\n",
    ).replace("\n<% end %>\n\n# Instantiate", "\n<% end %>\n<% end %>\n\n# Instantiate")
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    assert len(result.warnings) == 1 and "(1..$cfs_fleet_globals_num_cfs_targets).each" in result.warnings[0]
    assert result.text.index("TARGET APP_A") < result.text.index("<% (1..")


@pytest.mark.parametrize(
    ("opener", "warns"),
    [
        ("<% targets.map do |t| %>", True),
        ("<% (1..n).each_slice(1) do |group| %>", True),
        ("<% 3.times { %>", True),
        ("<% for cpu in cpus %>", True),
        ("<%# notes for each cpu %>", False),
        ("<% if cfs_fleet_globals_each_enabled %>", False),
        ("<% [1].each do |i| %>\n<% end %>\n<% if true %>", False),  # a loop that closed before the block
        ("<% if mode == 'for' %>", False),
        ("<% xs = ys.map do |y| y end %>\n<% if true %>", False),  # opens and closes on one line
        ("<% (1..n).each do |i| %>\n# a column-0 comment inside the loop", True),
    ],
)
def test_erb_loop_detection_shapes(two_bundles, opener, warns):
    plugin = PLUGIN.replace(
        "<% include_tlm = true %>\nINTERFACE CFS_INTF udp_interface.rb host 1234 1235 nil nil 128 10.0 nil\n"
        "    MAP_TARGET TO\n    MAP_TARGET SCH\n    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n",
        f"{opener}\n    INTERFACE CFS_INTF udp_interface.rb host 1234 1235 nil nil 128 10.0 nil\n"
        "        MAP_TARGET TO\n        PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n<% end %>\n",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    assert bool(result.warnings) == warns, result.warnings


def test_unindented_loop_body_is_detected(two_bundles):
    plugin = PLUGIN.replace(
        "<% include_tlm = true %>\nINTERFACE CFS_INTF udp_interface.rb host 1234 1235 nil nil 128 10.0 nil\n"
        "    MAP_TARGET TO\n    MAP_TARGET SCH\n    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n",
        "<% [1].each do |i| %>\nINTERFACE CFS_INTF udp_interface.rb host 1234 1235 nil nil 128 10.0 nil\n"
        "    MAP_TARGET TO\n<% end %>\n",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    assert len(result.warnings) == 1
    assert result.text.index("TARGET APP_A") < result.text.index("<% [1].each")


def test_target_goes_before_the_outermost_block_without_variables(two_bundles):
    plugin = NASA_PLUGIN.replace("VARIABLE cfs_endianness LITTLE_ENDIAN\n", "").replace(
        "VARIABLE cfs_1_intf_ip 172.17.0.1\nVARIABLE cfs_2_intf_ip 172.17.0.1\n", ""
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    assert result.text.index("TARGET APP_A") < result.text.index("<% (1..")


def test_bundle_lines_stay_outside_a_closed_block_before_the_plugin_mapping(two_bundles):
    plugin = PLUGIN.replace(
        "    MAP_TARGET TO\n    MAP_TARGET SCH\n    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n",
        "    <% if debug %>\n    OPTION LOG true\n    <% end %>\n    MAP_TARGET TO\n"
        "    <% if cfs_eds_enabled %> # eds\n    PROTOCOL READ LengthProtocol 32 16 7 1 BIG_ENDIAN 0 nil\n"
        "    <% end %>\n",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    block = _interface_block(result.text)
    assert block[:5] == [
        "<% if debug %>",
        "OPTION LOG true",
        "<% end %>",
        "MAP_TARGET <%= fprime_app_a_target_name %>",
        "MAP_TARGET TO",
    ]
    assert block[5:7] == ["PROTOCOL WRITE ProtocolCcsdsChecksumCompat", "<% if cfs_eds_enabled %> # eds"]
    again = merge.merge_plugin_text(result.text, [merge.load_bundle(two_bundles[0])], None)
    assert again.text == result.text and again.additions == [] and again.moves == []


def test_map_target_goes_outside_an_erb_guard_around_the_plugin_mapping(two_bundles):
    plugin = PLUGIN.replace(
        "    MAP_TARGET TO\n    MAP_TARGET SCH\n",
        "    <% if include_tlm %>\n    MAP_TARGET TO\n    <% end %>\n    MAP_TARGET SCH\n",
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    block = _interface_block(result.text)
    assert block[:3] == ["MAP_TARGET <%= fprime_app_a_target_name %>", "<% if include_tlm %>", "MAP_TARGET TO"]
    again = merge.merge_plugin_text(result.text, [merge.load_bundle(two_bundles[0])], None)
    assert again.text == result.text and again.additions == [] and again.moves == []


def test_map_cmd_and_tlm_target_lines_anchor_the_bundle_mapping(two_bundles):
    plugin = PLUGIN.replace(
        "    MAP_TARGET TO\n    MAP_TARGET SCH\n", "      MAP_TLM_TARGET TO\n      MAP_CMD_TARGET TO\n"
    )
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    block = _interface_block(result.text)
    assert block[:3] == ["MAP_TARGET <%= fprime_app_a_target_name %>", "MAP_TLM_TARGET TO", "MAP_CMD_TARGET TO"]
    assert "      MAP_TARGET <%= fprime_app_a_target_name %>" in result.text  # indented like the plugin's mappings


def test_map_target_without_mapping_or_protocol_goes_first(two_bundles):
    plugin = PLUGIN.replace("    MAP_TARGET TO\n    MAP_TARGET SCH\n", "    OPTION FOO 1\n")
    result = merge.merge_plugin_text(plugin, [merge.load_bundle(two_bundles[0])], None)
    assert _interface_block(result.text)[:2] == ["MAP_TARGET <%= fprime_app_a_target_name %>", "OPTION FOO 1"]


def test_interface_outside_a_loop_does_not_warn(two_bundles, plugin):
    assert not [warning for warning in merge.merge(plugin, list(two_bundles)).warnings if "ERB loop" in warning]


def test_compat_helpers_are_installed_into_a_nasa_style_plugin(two_bundles, nasa_plugin):
    report = merge.merge(nasa_plugin, list(two_bundles))
    for name in HELPERS:
        path = nasa_plugin / "lib" / f"{name}.rb"
        assert path.is_file() and merge._generated(path), name
        assert f"compat: lib/{name}.rb" in report.copied
    assert report.compat_skipped is None and report.warnings == [w for w in report.warnings if "ERB loop" in w]
    assert (nasa_plugin / "lib" / "cfs_ccsds.rb").read_text() == (merge.COMPAT_RUNTIME / "cfs_ccsds.rb").read_text()
    again = merge.merge(nasa_plugin, list(two_bundles))
    assert set(again.unchanged) >= {f"compat: lib/{name}.rb" for name in HELPERS} and again.copied == []


def test_compat_helpers_are_detected_from_the_plugin_lib_too(two_bundles, nasa_plugin):
    headers = nasa_plugin / "targets" / "CFS" / "lib" / "cfs_packet_headers.rb"
    headers.rename(nasa_plugin / "lib" / headers.name)
    report = merge.merge(nasa_plugin, list(two_bundles))
    assert report.compat_skipped is None and (nasa_plugin / "lib" / "global_defs.rb").is_file()


def test_compat_helpers_are_skipped_without_cfs_packet_headers(two_bundles, plugin):
    report = merge.merge(plugin, list(two_bundles))
    assert report.compat_skipped is not None and "cfs_packet_headers.rb" in report.compat_skipped
    assert not any((plugin / "lib" / f"{name}.rb").exists() for name in HELPERS)
    assert report.warnings == []  # a non-nasa plugin may load its helpers from a gem; nothing to judge
    assert "compat helpers not installed: no cfs_packet_headers.rb" in report.summary()


def test_compat_helpers_are_skipped_when_the_plugin_provides_all_of_them(two_bundles, nasa_plugin):
    for name in HELPERS:
        (nasa_plugin / "targets" / "CFS" / "lib" / f"{name}.rb").write_text(f"# hand written {name}\n")
    before = {path: path.read_text() for path in nasa_plugin.rglob("*.rb")}
    report = merge.merge(nasa_plugin, list(two_bundles), force=True)
    assert report.compat_skipped.startswith("hand-written helpers present: ")
    assert all(f"{name}.rb" in report.compat_skipped for name in HELPERS)
    assert not any((nasa_plugin / "lib" / f"{name}.rb").exists() for name in HELPERS)
    assert {path: path.read_text() for path in before} == before
    assert not any("required helper" in warning for warning in report.warnings)


def test_partial_hand_written_helper_set_fails_before_writing(two_bundles, nasa_plugin):
    (nasa_plugin / "lib" / "cfs_ccsds.rb").write_text("# hand written\n")
    (nasa_plugin / "targets" / "CFS" / "lib" / "global_defs.rb").write_text("# hand written\n")
    before = sorted(str(path.relative_to(nasa_plugin)) for path in nasa_plugin.rglob("*"))
    text = (nasa_plugin / "plugin.txt").read_text()
    with pytest.raises(merge.MergeError) as error:
        merge.merge(nasa_plugin, list(two_bundles))
    message = str(error.value)
    assert "lib/cfs_ccsds.rb" in message and "targets/CFS/lib/global_defs.rb" in message
    assert "fsw_msg_id.rb" in message and "fsw_config_param.rb" in message
    assert sorted(str(path.relative_to(nasa_plugin)) for path in nasa_plugin.rglob("*")) == before
    assert (nasa_plugin / "plugin.txt").read_text() == text


def test_generated_and_hand_written_copies_of_one_helper_fail(two_bundles, nasa_plugin):
    merge.merge(nasa_plugin, list(two_bundles))
    (nasa_plugin / "targets" / "CFS" / "lib" / "cfs_ccsds.rb").write_text("# hand written\n")
    with pytest.raises(merge.MergeError) as error:
        merge.merge(nasa_plugin, list(two_bundles))
    assert "lib/cfs_ccsds.rb" in str(error.value) and "targets/CFS/lib/cfs_ccsds.rb" in str(error.value)


def test_generated_helper_subset_is_completed_and_outdated_copies_need_force(two_bundles, nasa_plugin):
    stale = "# Generated F Prime cFS compatibility helper (older)\nmodule FswMsgId; end\n"
    (nasa_plugin / "lib" / "fsw_msg_id.rb").write_text(stale)
    with pytest.raises(merge.MergeError, match="use --force"):
        merge.merge(nasa_plugin, list(two_bundles))
    assert not (nasa_plugin / "lib" / "cfs_ccsds.rb").exists()
    report = merge.merge(nasa_plugin, list(two_bundles), force=True)
    assert "compat: lib/fsw_msg_id.rb" in report.copied and "compat: lib/cfs_ccsds.rb" in report.copied
    assert (nasa_plugin / "lib" / "fsw_msg_id.rb").read_text() == (merge.COMPAT_RUNTIME / "fsw_msg_id.rb").read_text()


def test_no_compat_helpers_leaves_generated_helpers_alone_and_reports_them(two_bundles, nasa_plugin):
    stale = "# Generated F Prime cFS compatibility helper (older)\nmodule FswMsgId; end\n"
    (nasa_plugin / "lib" / "fsw_msg_id.rb").write_text(stale)
    (nasa_plugin / "lib" / "cfs_ccsds.rb").write_text("# hand written\n")  # a partial set would otherwise fail
    report = merge.merge(nasa_plugin, list(two_bundles), force=True, compat_helpers=False)
    assert report.compat_skipped == "--no-compat-helpers"
    assert report.compat_kept == ["lib/fsw_msg_id.rb"]
    assert (nasa_plugin / "lib" / "fsw_msg_id.rb").read_text() == stale
    assert not (nasa_plugin / "lib" / "global_defs.rb").exists()
    assert not any(label.startswith("compat:") for label in report.copied + report.unchanged)
    assert "  kept      lib/fsw_msg_id.rb" in report.summary()
    assert sum("required helper" in warning for warning in report.warnings) == 2  # global_defs, fsw_config_param


def test_dry_run_reports_compat_helpers_without_writing(two_bundles, nasa_plugin):
    report = merge.merge(nasa_plugin, list(two_bundles), dry_run=True)
    assert {f"compat: lib/{name}.rb" for name in HELPERS} <= set(report.copied)
    assert not any((nasa_plugin / "lib" / f"{name}.rb").exists() for name in HELPERS)
    assert (nasa_plugin / "plugin.txt").read_text() == NASA_PLUGIN


def test_required_helpers_come_from_the_manifests(two_bundles, nasa_plugin):
    first, second = two_bundles
    manifest = json.loads((second / "manifest.json").read_text())
    manifest["required_helpers"] = ["cfs_ccsds", "mission_extras"]
    (second / "manifest.json").write_text(json.dumps(manifest))
    old = json.loads((first / "manifest.json").read_text())
    del old["required_helpers"]  # a manifest predating the field needs the four defaults
    (first / "manifest.json").write_text(json.dumps(old))
    bundles = [merge.load_bundle(first), merge.load_bundle(second)]
    assert merge.required_helpers(bundles) == [*HELPERS, "mission_extras"]
    report = merge.merge(nasa_plugin, [first, second])
    assert len([w for w in report.warnings if "mission_extras.rb" in w and "no compatibility version" in w]) == 1
    assert all((nasa_plugin / "lib" / f"{name}.rb").is_file() for name in HELPERS)
    # a hand-written extra helper does not block installing the packaged four
    (nasa_plugin / "targets" / "CFS" / "lib" / "mission_extras.rb").write_text("# hand written\n")
    for name in HELPERS:
        (nasa_plugin / "lib" / f"{name}.rb").unlink()
    report = merge.merge(nasa_plugin, [first, second])
    assert report.compat_skipped is None and report.warnings == [w for w in report.warnings if "ERB loop" in w]
    assert all((nasa_plugin / "lib" / f"{name}.rb").is_file() for name in HELPERS)
    (nasa_plugin / "targets" / "CFS" / "lib" / "mission_extras.rb").unlink()
    # a complete hand-written set plus a helper nobody packages is a warning, not a partial-set error
    for name in HELPERS:
        (nasa_plugin / "lib" / f"{name}.rb").write_text(f"# hand written {name}\n")
    report = merge.merge(nasa_plugin, [first, second], force=True)
    assert report.compat_skipped.startswith("hand-written helpers present")
    assert [w for w in report.warnings if "mission_extras.rb" in w]
    (nasa_plugin / "targets" / "CFS" / "lib" / "mission_extras.rb").write_text("# hand written\n")
    assert not [w for w in merge.merge(nasa_plugin, [first, second]).warnings if "mission_extras.rb" in w]
    for bad in (["../evil"], "cfs_ccsds", None, [1]):
        manifest["required_helpers"] = bad
        (second / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(merge.MergeError, match="plain file name|list of helper names"):
            merge.required_helpers([merge.load_bundle(second)])


def test_cli_no_compat_helpers_flag(two_bundles, nasa_plugin, capsys):
    from fprime_cfs_cosmos_generator import merge_cli

    assert merge_cli.main(["--plugin", str(nasa_plugin), "--no-compat-helpers", *map(str, two_bundles)]) == 0
    assert not (nasa_plugin / "lib" / "cfs_ccsds.rb").exists()
    out = capsys.readouterr()
    assert "compat helpers not installed: --no-compat-helpers" in out.out
    assert out.err.count("[WARNING]") == 5 and "ERB loop" in out.err  # loop + four missing helpers, once each
    assert "WARNING" not in out.out


def test_packaged_compat_helpers_are_stamped_and_complete():
    names = sorted(path.name for path in merge.COMPAT_RUNTIME.iterdir() if path.name.endswith(".rb"))
    assert names == sorted(f"{name}.rb" for name in HELPERS)
    for path in merge.COMPAT_RUNTIME.iterdir():
        assert path.read_text().startswith(merge.GENERATED_MARKER), path.name
