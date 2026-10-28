# Generated F Prime cFS runtime support. Do not edit.
require 'openc3/interfaces/protocols/protocol'

module OpenC3
  # Finalizes the cFS command header of every packet written through the interface: the CCSDS length from the
  # serialized buffer (variable-length F Prime strings shrink the packet below its defined length) and the cFS XOR
  # checksum. The merge declares this protocol before the plugin's own WRITE protocols so that it runs last
  # (COSMOS runs WRITE protocols in reverse declaration order). Header items are found by the plugin's names or,
  # failing that, by their fixed CCSDS position (length: bytes 4-5, secondary header checksum: byte 7).
  class ProtocolCcsdsChecksumCompat < Protocol
    LENGTH_NAMES = ['CCSDS_LENGTH'].freeze
    CHECKSUM_NAMES = ['SEC_CRC', 'CCSDS_CHECKSUM'].freeze
    CCSDS_LENGTH_OFFSET = 7

    def write_packet(packet)
      length_item = find_item(packet, LENGTH_NAMES, 32, 16)
      checksum_item = find_item(packet, CHECKSUM_NAMES, 56, 8)
      return packet unless length_item || checksum_item

      packet.write(length_item.name, packet.buffer.bytesize - CCSDS_LENGTH_OFFSET) if length_item
      if checksum_item
        packet.write(checksum_item.name, 0)
        checksum = packet.buffer.each_byte.reduce(0xFF) { |value, byte| value ^ byte }
        packet.write(checksum_item.name, checksum)
      end
      packet
    end

    private

    def find_item(packet, names, bit_offset, bit_size)
      named = names.map { |name| packet.items[name] }.compact.first
      return named if named

      packet.sorted_items.find do |item|
        item.bit_offset == bit_offset && item.bit_size == bit_size && item.data_type == :UINT
      end
    end
  end
end
