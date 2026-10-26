require 'openc3/subpacketizers/subpacketizer'
require 'openc3/system/system'
require 'openc3/utilities/logger'

module OpenC3
  class FprimeSubpacketizer < Subpacketizer
    PARENTS = {
      'FPRIME_CHANNELS' => ['CHANNELS', 'FPRIME_CHANNEL_ID'],
      'FPRIME_PACKETS' => ['PACKET', 'FPRIME_PACKET_ID']
    }.freeze

    def call(packet)
      parent = PARENTS[packet.packet_name]
      return [packet] unless parent

      block, key_item = parent
      candidates = System.telemetry.packets(packet.target_name).values.select do |candidate|
        candidate.subpacket && candidate.items.key?(key_item)
      end
      packets = []
      data = packet.read(block)
      while data && !data.empty?
        subpacket = candidates.find { |candidate| candidate.identify?(data) }
        if subpacket.nil?
          Logger.warn("#{packet.target_name} #{packet.packet_name}: unknown F Prime record, #{data.length} bytes left")
          break
        end
        subpacket = subpacket.clone
        subpacket.buffer = data
        length = record_length(subpacket)
        subpacket.buffer = data[0, length]
        subpacket.received_time = packet.received_time
        packets << subpacket
        data = data[length..-1]
      end
      packets << packet
      packets
    end

    def record_length(subpacket)
      end_bits = subpacket.defined_length_bits
      subpacket.sorted_items.each do |item|
        next if item.data_type == :DERIVED || !item.parent_item.nil? || item.bit_offset < 0

        bits = if item.variable_bit_size
                 subpacket.calculate_total_bit_size(item)
               elsif item.array_size
                 item.array_size
               else
                 item.bit_size
               end
        end_bits = [end_bits, item.bit_offset + bits].max
      end
      (end_bits + 7) / 8
    end
  end
end
