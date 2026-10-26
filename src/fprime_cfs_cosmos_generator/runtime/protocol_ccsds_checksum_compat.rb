# Generated F Prime cFS runtime support. Do not edit.
require 'openc3/interfaces/protocols/protocol'

module OpenC3
  class ProtocolCcsdsChecksumCompat < Protocol
    CHECKSUM_ITEMS = ['SEC_CRC', 'CCSDS_CHECKSUM'].freeze
    LENGTH_ITEM = 'CCSDS_LENGTH'.freeze

    def write_packet(packet)
      checksum_item = CHECKSUM_ITEMS.find { |name| packet.items.key?(name) }
      return packet unless checksum_item

      packet.write(LENGTH_ITEM, packet.buffer.bytesize - 7) if packet.items.key?(LENGTH_ITEM)
      packet.write(checksum_item, 0)
      checksum = packet.buffer.each_byte.reduce(0xFF) { |value, byte| value ^ byte }
      packet.write(checksum_item, checksum)
      packet
    end
  end
end
