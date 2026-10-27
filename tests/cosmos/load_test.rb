# Loads the generated bundle into the real openc3 gem (no server), identifies telemetry, builds a command,
# and runs the checksum protocol. Usage: ruby load_test.rb <bundle_dir> <TARGET>
require 'openc3'
require 'openc3/packets/packet_config'
require 'openc3/packets/telemetry'
require 'openc3/packets/commands'
require 'openc3/system/system'
bundle, target = ARGV
$LOAD_PATH.unshift('/stubs', "#{bundle}/lib")
Dir["#{bundle}/lib/*.rb"].sort.each { |f| require File.basename(f, '.rb') }

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
  pkt = tlm.packets(target).values.find { |p| p.identify?(buf) }
  return nil unless pkt
  pkt = pkt.clone
  pkt.buffer = buf
  pkt
end
cmd = OpenC3::Commands.new(config)
puts "telemetry packets: #{tlm.packets(target).length}"
puts "command packets: #{cmd.packets(target).length}"

# --- telemetry: Health packet (MID 0x0820, descriptor 32, packet id 1) with cFS header
hdr = [0x0820, 0xC000, 0, 0x5F5E100, 0x8000].pack('nnnNn')
health = tlm.packet(target, 'Health')
body = [32, 1, 2, 0, 1000, 500].pack("nnnCNN"); body += ([0] * (health.defined_length - hdr.bytesize - body.bytesize)).pack("C*")
buf = hdr + body
buf[4, 2] = [buf.bytesize - 7].pack('n')
id = identify(tlm, target, buf)
puts "identify Health -> #{id ? id.packet_name : 'nil'}"
raise 'Health not identified' unless id && id.packet_name == 'HEALTH'
puts "  CommandsDispatched=#{id.read('CfsCore.cmdDisp.CommandsDispatched')} tlm_time=#{id.read('FPRIME_TIME')}"

# --- telemetry: a channel (MID 0x0801, descriptor 1, channel id of CommandsDispatched)
chan = tlm.packet(target, 'CfsCore.cmdDisp.CommandsDispatched')
chan_id = chan.get_item('FPRIME_CHANNEL_ID').id_value
hdr = [0x0801, 0xC000, 0, 0x5F5E100, 0x8000].pack('nnnNn')
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
  version = tlm.packet(target, 'CfsCore.version.FrameworkVersion')
  ver_id = version.get_item('FPRIME_CHANNEL_ID').id_value
  rec_s = [ver_id, 2, 0, 1002, 502, 6].pack('NnCNNn') + 'v4.3.0'
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
  rec_c = [custom_id, 2, 0, 1003, 503, 7, 5].pack('NnCNNNn') + 'v1.2a' + [2].pack('C')
  buf = hdr + [1].pack('n') + rec_c + rec1
  buf[4, 2] = [buf.bytesize - 7].pack('n')
  p1, = splitter.read_data(buf)
  p2, = splitter.read_data('')
  rest, = splitter.read_data('')
  raise 'struct-with-string split failed' unless p1.is_a?(String) && p2.is_a?(String) && rest == :STOP
  c = identify(tlm, target, p1); d = identify(tlm, target, p2)
  puts "splitter struct case -> #{c.packet_name} value=#{c.read('CustomVersion01.version_value')} status=#{c.read('CustomVersion01.version_status')}; #{d.packet_name}=#{d.read('CommandsDispatched')}"
  raise 'struct channel wrong' unless c.read('CustomVersion01.version_value') == 'v1.2a' && c.read('CustomVersion01.version_status') == 2 && d.read('CommandsDispatched') == 77
  packets_hdr = [0x0820, 0xC000, 0, 0x5F5E100, 0x8000].pack('nnnNn')
  passthrough, = splitter.read_data(packets_hdr + [32, 1].pack('nn') + ("\0" * 40))
  raise 'non-channel packet must pass through' unless passthrough.bytesize == packets_hdr.bytesize + 44
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
