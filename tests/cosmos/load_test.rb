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
