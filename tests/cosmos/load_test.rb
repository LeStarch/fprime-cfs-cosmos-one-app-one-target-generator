# Loads the generated bundle into the real openc3 gem (no server), identifies telemetry (incl. the FPRIME_EVENTS
# packet when manifest.json lists an events MID; fails if the manifest advertises it but the target lacks it; the
# descriptors are hard-coded like the neighbouring cases), builds a command, and runs the checksum protocol.
# Usage: ruby load_test.rb <bundle_dir> <TARGET> [header16|header12] [nasa_plugin_dir]
#   header16 (default): the shipped runtime/compat helpers (/compat), cFE 7 16-byte telemetry header
#   header12: the 12-byte header stub (/stubs/header12) ahead of /compat on the load path
#   nasa_plugin_dir: compare the shipped helpers' header items with its cfs_packet_headers.rb
require 'openc3'
require 'openc3/packets/packet_config'
require 'openc3/packets/telemetry'
require 'openc3/packets/commands'
require 'openc3/system/system'
bundle, target, flavour, nasa = ARGV
flavour = 'header16' if flavour.nil? || flavour.empty?
nasa = nil if nasa && nasa.empty?
$LOAD_PATH.unshift('/compat', '/stubs', "#{bundle}/lib")
$LOAD_PATH.unshift('/stubs/header12') if flavour == 'header12'
Dir["#{bundle}/lib/*.rb"].sort.each { |f| require File.basename(f, '.rb') }
require 'global_defs'
require 'cfs_ccsds'
puts "flavour: #{flavour} (Ccsds from #{$LOADED_FEATURES.grep(/cfs_ccsds/).first}) endian=#{Globals::Cfg.processor_endian}"
raise 'wrong Ccsds helper on the load path' unless $LOADED_FEATURES.grep(/cfs_ccsds/).first.start_with?(flavour == 'header12' ? '/stubs/header12' : '/compat')
HEADER_BYTES = flavour == 'header12' ? 12 : 16
# cFS telemetry header: primary header + seconds + subseconds (+ 4 spare bytes on cFE 7)
def cfs_hdr(mid)
  hdr = [mid, 0xC000, 0, 0x5F5E100, 0x8000].pack('nnnNn')
  hdr += [0].pack('N') if HEADER_BYTES == 16
  hdr
end

if nasa
  # The shipped Ccsds helpers must lay the header out exactly like nasa/cfs-cosmos-plugin's cfs_packet_headers.rb
  # (APPEND keyword, item name, bit size, type, endianness), ignoring the ID/default values and PACKET_TIME.
  source = Dir["#{nasa}/targets/*/lib/cfs_packet_headers.rb", "#{nasa}/lib/cfs_packet_headers.rb"].first
  raise "no cfs_packet_headers.rb under #{nasa}" unless source
  def header_items(text)
    text.scan(/(APPEND_\w+)\s+(\w+)\s+(\d+)\s+(\w+)(?:\s+MIN_\w+\s+MAX_\w+)?(?:\s+[^"\s]+)?\s+"[^"]*"\s*(BIG_ENDIAN|LITTLE_ENDIAN)?/)
        .map { |keyword, name, bits, type, endian| [keyword, name, bits.to_i, type, endian || 'host'] }
  end
  nasa_text = File.read(source).delete('\\') # the header lines are Ruby string literals with escaped quotes
  nasa_cmd = header_items(nasa_text[/def cfs_cmd_hdr.*?^end/m])
  nasa_tlm = header_items(nasa_text[/def cfs_tlm_hdr.*?^end/m])
  ours_cmd = header_items(Ccsds.cmd('0x1800', 0, 6))
  ours_tlm = header_items(Ccsds.tlm('0x0820'))
  puts "nasa cmd header: #{nasa_cmd.map { |i| i[1] }.join(' ')}"
  puts "nasa tlm header: #{nasa_tlm.map { |i| i[1] }.join(' ')}"
  raise "command header differs from nasa: #{ours_cmd.inspect} vs #{nasa_cmd.inspect}" unless ours_cmd == nasa_cmd
  raise "telemetry header differs from nasa: #{ours_tlm.inspect} vs #{nasa_tlm.inspect}" unless ours_tlm == nasa_tlm
  raise 'nasa telemetry header is not 16 bytes' unless nasa_tlm.sum { |i| i[2] } == 128
  puts 'nasa header comparison OK'
end

config = OpenC3::PacketConfig.new
Dir["#{bundle}/targets/#{target}/cmd_tlm/*.txt"].sort.each do |f|
  erb = ERB.new(File.read(f), trim_mode: '-')
  erb.filename = f
  rendered = erb.result(binding)
  tmp = "/tmp/#{File.basename(f)}"
  File.write(tmp, rendered)
  config.process_file(tmp, target)
end
puts "warnings: #{config.warnings.length}"
config.warnings.first(10).each { |w| puts "  W: #{w}" }
tlm = OpenC3::Telemetry.new(config)
def identify(tlm, target, buf)
  # Telemetry#identify's two modes (openc3 7.5.1 packets/telemetry.rb) without System.instance: unique-id mode
  # scans every packet's identify?; otherwise the FIRST non-virtual packet's ID items key the id-value hash
  packets = tlm.packets(target).values.reject(&:subpacket)
  if tlm.tlm_unique_id_mode(target)
    pkt = packets.find { |p| p.identify?(buf) }
  else
    first = packets.find { |p| !p.virtual }
    hash = tlm.config.tlm_id_value_hash[target]
    pkt = first && (hash[first.read_id_values(buf)] || hash['CATCHALL'])
  end
  return nil unless pkt
  pkt = pkt.clone
  pkt.buffer = buf
  pkt
end
cmd = OpenC3::Commands.new(config)
puts "telemetry packets: #{tlm.packets(target).length}"
puts "command packets: #{cmd.packets(target).length}"

# --- telemetry: an F Prime event (events MID, descriptor 2) as one opaque, exact RECORD; driven by the bundle's manifest
events_mid = JSON.parse(File.read("#{bundle}/manifest.json"))['message_ids']['events']
if events_mid
  raise 'manifest declares an events MID but the target has no FPRIME_EVENTS packet' unless tlm.packets(target).key?('FPRIME_EVENTS')
  record = ['10020001000200000f47ae00090c9000000007'].pack('H*')
  buf = cfs_hdr(Integer(events_mid)) + [2].pack('n') + record
  buf[4, 2] = [buf.bytesize - 7].pack('n')
  id = identify(tlm, target, buf)
  puts "identify event -> #{id ? id.packet_name : 'nil'} record=#{id && id.read('RECORD').unpack1('H*')}"
  raise 'event not identified' unless id && id.packet_name == 'FPRIME_EVENTS'
  raise 'event record not opaque/exact' unless id.read('RECORD') == record
  matches = tlm.packets(target).values.count { |p| p.identify?(buf) }
  raise "event identified by #{matches} packets" unless matches == 1
  stray = buf.dup
  stray[cfs_hdr(Integer(events_mid)).bytesize, 2] = [1].pack('n') # same MID, telemetry descriptor: not an event
  raise 'descriptor does not discriminate FPRIME_EVENTS' if tlm.packets(target).values.any? { |p| p.identify?(stray) }
else
  puts 'SKIP events: bundle has no events MID (no FW_PACKET_LOG in the dictionary)'
end

# --- telemetry: Health packet (MID 0x0820, descriptor 32, packet id 1) with cFS header
hdr = cfs_hdr(0x0820)
health = tlm.packet(target, 'Health')
body = [32, 1, 2, 0, 1000, 500].pack("nnnCNN"); body += ([0] * (health.defined_length - hdr.bytesize - body.bytesize)).pack("C*")
buf = hdr + body
buf[4, 2] = [buf.bytesize - 7].pack('n')
id = identify(tlm, target, buf)
puts "identify Health -> #{id ? id.packet_name : 'nil'}"
raise 'Health not identified' unless id && id.packet_name == 'HEALTH'
puts "  CommandsDispatched=#{id.read('CfsCore.cmdDisp.CommandsDispatched')} tlm_time=#{id.read('FPRIME_TIME')}"
raise 'header width not applied' unless health.get_item('FPRIME_DESCRIPTOR').bit_offset == HEADER_BYTES * 8
raise 'descriptor misread' unless id.read('FPRIME_DESCRIPTOR') == 32 && id.read('FPRIME_TIME').to_i == 1000
# a buffer with the other header width must not decode: proves the definition really has this header size
other = cfs_hdr(0x0820)[0, HEADER_BYTES == 16 ? 12 : 16].ljust(HEADER_BYTES == 16 ? 12 : 16, "\0") + body
other[4, 2] = [other.bytesize - 7].pack('n')
wrong = identify(tlm, target, other)
raise 'other header width decoded as well-formed' if wrong && wrong.read('FPRIME_DESCRIPTOR') == 32
puts "  other header width (#{HEADER_BYTES == 16 ? 12 : 16} bytes) correctly fails to decode"
# LIMITS behaviour: a one-sided dictionary "red high" must leave 0 GREEN (no false RED_LOW)
limited = health.sorted_items.select { |item| item.limits.values && item.limits.values[:DEFAULT] }
raise 'expected LIMITS items in the Health packet' if limited.empty?
limited.each { |item| item.limits.enabled = true }
id.check_limits(:DEFAULT, false)
states = limited.map { |item| [item.name, id.get_item(item.name).limits.state] }
puts "  limits at zero: #{states.map { |n, st| "#{n}=#{st}" }.join(' ')}"
raise 'false limits violation at 0' unless states.all? { |_n, st| st == :GREEN }

# --- telemetry: a channel (MID 0x0801, descriptor 1, channel id of CommandsDispatched)
chan = tlm.packet(target, 'CfsCore.cmdDisp.CommandsDispatched')
chan_id = chan.get_item('FPRIME_CHANNEL_ID').id_value
hdr = cfs_hdr(0x0801)
body = [1, chan_id, 2, 0, 1000, 500, 77].pack('nNnCNNN')
buf = hdr + body
buf[4, 2] = [buf.bytesize - 7].pack('n')
id = identify(tlm, target, buf)
puts "identify channel -> #{id ? id.packet_name : 'nil'} value=#{id && id.read('CommandsDispatched')}"
raise 'channel not identified' unless id && id.packet_name.upcase == 'CFSCORE.CMDDISP.COMMANDSDISPATCHED'

# --- multi-record TlmChan packet through the READ splitter (two channels in one CCSDS packet)
if defined?(OpenC3::ProtocolFprimeChannelSplitter)
  OpenC3::ProtocolFprimeChannelSplitter.telemetry_source = -> { tlm }
  splitter = OpenC3::ProtocolFprimeChannelSplitter.new('CCSDS_STREAMID', false)
  errors = tlm.packet(target, 'CfsCore.cmdDisp.CommandErrors')
  err_id = errors.get_item('FPRIME_CHANNEL_ID').id_value
  rec1 = [chan_id, 2, 0, 1000, 500, 77].pack('NnCNNN')
  rec2 = [err_id, 2, 0, 1001, 501, 5].pack('NnCNNN')
  buf = hdr + [1].pack('n') + rec1 + rec2
  buf[4, 2] = [buf.bytesize - 7].pack('n')
  first, = splitter.read_data(buf)
  second, = splitter.read_data('')
  third, = splitter.read_data('')
  raise 'splitter did not emit two records' unless first.is_a?(String) && second.is_a?(String) && third == :STOP
  a = identify(tlm, target, first); b = identify(tlm, target, second)
  puts "splitter -> #{a.packet_name}=#{a.read('CommandsDispatched')} #{b.packet_name}=#{b.read('CommandErrors')}"
  raise 'split records wrong' unless a.read('CommandsDispatched') == 77 && b.read('CommandErrors') == 5
  raise 'split record length wrong' unless first.bytesize == chan.defined_length && second.bytesize == errors.defined_length
  raise 'piece CCSDS_LENGTH wrong' unless first[4, 2].unpack1('n') == first.bytesize - 7 && second[4, 2].unpack1('n') == second.bytesize - 7
  # string channel (variable length) in both orders
  # the string length field is FwSizeStoreType, whose width comes from the dictionary (16 or 64 bits)
  def packed_length(packet, item_name, value)
    bits = packet.get_item(item_name).bit_size
    [value].pack({ 16 => 'n', 32 => 'N', 64 => 'Q>' }.fetch(bits))
  end
  version = tlm.packet(target, 'CfsCore.version.FrameworkVersion')
  ver_id = version.get_item('FPRIME_CHANNEL_ID').id_value
  rec_s = [ver_id, 2, 0, 1002, 502].pack('NnCNN') + packed_length(version, 'FrameworkVersion_LENGTH', 6) + 'v4.3.0'
  [[rec_s, rec1], [rec1, rec_s]].each do |order|
    buf = hdr + [1].pack('n') + order.join
    buf[4, 2] = [buf.bytesize - 7].pack('n')
    p1, = splitter.read_data(buf)
    p2, = splitter.read_data('')
    rest, = splitter.read_data('')
    raise 'string split failed' unless p1.is_a?(String) && p2.is_a?(String) && rest == :STOP
    decoded = [p1, p2].map { |piece| identify(tlm, target, piece) }
    values = decoded.map { |pk| pk.packet_name.end_with?('FRAMEWORKVERSION') ? pk.read('FrameworkVersion') : pk.read('CommandsDispatched') }
    puts "splitter string case -> #{decoded.map(&:packet_name).join(', ')} = #{values.inspect}"
    raise 'string channel split wrong' unless values.sort_by(&:to_s) == ['77', 'v4.3.0'].sort_by(&:to_s).map { |v| v == '77' ? 77 : v }
  end
  # struct channel with a string in the middle (enum, string, U8 status) followed by a scalar channel
  custom = tlm.packet(target, 'CfsCore.version.CustomVersion01')
  custom_id = custom.get_item('FPRIME_CHANNEL_ID').id_value
  rec_c = [custom_id, 2, 0, 1003, 503, 7].pack('NnCNNN') + packed_length(custom, 'CustomVersion01.version_value_LENGTH', 5) +
          'v1.2a' + [2].pack('C')
  buf = hdr + [1].pack('n') + rec_c + rec1
  buf[4, 2] = [buf.bytesize - 7].pack('n')
  p1, = splitter.read_data(buf)
  p2, = splitter.read_data('')
  rest, = splitter.read_data('')
  raise 'struct-with-string split failed' unless p1.is_a?(String) && p2.is_a?(String) && rest == :STOP
  c = identify(tlm, target, p1); d = identify(tlm, target, p2)
  puts "splitter struct case -> #{c.packet_name} value=#{c.read('CustomVersion01.version_value')} status=#{c.read('CustomVersion01.version_status')}; #{d.packet_name}=#{d.read('CommandsDispatched')}"
  raise 'struct channel wrong' unless c.read('CustomVersion01.version_value') == 'v1.2a' && c.read('CustomVersion01.version_status') == 2 && d.read('CommandsDispatched') == 77
  packets_hdr = cfs_hdr(0x0820)
  passthrough, = splitter.read_data(packets_hdr + [32, 1].pack('nn') + ("\0" * 40))
  raise 'non-channel packet must pass through' unless passthrough.bytesize == packets_hdr.bytesize + 44
  # a channelized packet cut inside the channel id (header + descriptor + 2 of 4 id bytes) passes through untouched
  short = hdr + [1].pack('n') + [chan_id].pack('N')[0, 2]
  short[4, 2] = [short.bytesize - 7].pack('n')
  kept, = splitter.read_data(short)
  rest, = splitter.read_data('')
  raise 'short channel packet must pass through' unless kept == short && rest == :STOP
  one_byte, = splitter.read_data("\x08")
  raise 'one-byte buffer must pass through' unless one_byte == "\x08"
end

# --- command: NO_OP_STRING then checksum protocol
pkt = cmd.build_cmd(target, 'CfsCore.cmdDisp.CMD_NO_OP_STRING', { 'arg1' => 'hello' })
proto = OpenC3::ProtocolCcsdsChecksumCompat.new
proto.write_packet(pkt)
puts "cmd bytes: #{pkt.buffer.unpack1('H*')}"
puts "  CCSDS_LENGTH=#{pkt.read('CCSDS_LENGTH')} CCSDS_FC=#{pkt.read('CCSDS_FC')} CHECKSUM=#{pkt.read('CCSDS_CHECKSUM')} opcode=#{pkt.read('FPRIME_OPCODE')} arg1=#{pkt.read('arg1').inspect}"
xor = pkt.buffer.each_byte.reduce(0xFF) { |a, b| a ^ b }
puts "  xor over packet (expect 0): #{xor}"
raise 'checksum invalid' unless xor == 0
raise 'length invalid' unless pkt.read('CCSDS_LENGTH') == pkt.buffer.bytesize - 7
puts 'LOAD TEST OK'
