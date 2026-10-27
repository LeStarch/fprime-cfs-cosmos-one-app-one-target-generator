# Generated F Prime cFS runtime support. Do not edit.
#
# Svc::TlmChan packs several channel records (id, time, value) into one com buffer and CfsTlmFramer wraps
# that buffer in a single CCSDS telemetry packet. COSMOS packets are fixed layouts, so this READ protocol
# splits such a packet into one packet per record, each carrying a copy of the cFS header (with its own
# CCSDS_LENGTH) and the F Prime descriptor, and hands them out one at a time (Interface#read drains pending
# data through the blank read). Record lengths are measured with the channel's own COSMOS definition, so
# variable-length (string) channels split correctly. Works on every COSMOS 6.x. Packets whose stream id is
# not a channelized F Prime MID pass through untouched.
#
#   PROTOCOL READ ProtocolFprimeChannelSplitter [stream_id_item]   (default CCSDS_STREAMID)
require 'openc3/interfaces/protocols/protocol'
require 'openc3/utilities/logger'

module OpenC3
  class ProtocolFprimeChannelSplitter < Protocol
    CFS_TLM_HEADER_BYTES = 12
    LENGTH_OFFSET = 4
    DESCRIPTOR_ITEM = 'FPRIME_DESCRIPTOR'.freeze
    CHANNEL_ID_ITEM = 'FPRIME_CHANNEL_ID'.freeze

    class << self
      attr_accessor :telemetry_source
    end

    def initialize(stream_id_item = 'CCSDS_STREAMID', allow_empty_data = nil)
      super(allow_empty_data)
      @stream_id_item = stream_id_item.to_s.upcase
      @pending = []
      @table = nil
    end

    def reset
      super
      @pending = []
    end

    def read_data(data, extra = nil)
      if data.length <= 0
        return @pending.shift, extra unless @pending.empty?
        return super(data, extra)
      end
      pieces = split(data)
      return super(data, extra) if pieces.nil? || pieces.length <= 1
      @pending.concat(pieces[1..])
      return pieces[0], extra
    end

    private

    # { stream_id => { prefix_bytes:, id_bytes:, channels: { channel_id => Packet } } }
    def table
      return @table if @table
      @table = {}
      telemetry = (self.class.telemetry_source || -> { System.telemetry }).call
      targets = @interface ? @interface.target_names : telemetry.target_names
      candidates = 0
      targets.each do |target|
        telemetry.packets(target).each_value do |packet|
          next unless packet.items.key?(CHANNEL_ID_ITEM)
          candidates += 1
          stream = stream_id_item(packet)
          channel = packet.get_item(CHANNEL_ID_ITEM)
          next unless stream && stream.id_value && channel.id_value
          descriptor_bytes = packet.items.key?(DESCRIPTOR_ITEM) ? packet.get_item(DESCRIPTOR_ITEM).bit_size / 8 : 0
          entry = @table[stream.id_value] ||= {
            prefix_bytes: CFS_TLM_HEADER_BYTES + descriptor_bytes,
            id_bytes: channel.bit_size / 8,
            channels: {}
          }
          entry[:channels][channel.id_value] = packet
        end
      end
      if candidates > 0 && @table.empty?
        Logger.warn("ProtocolFprimeChannelSplitter: #{candidates} channel packets but no #{@stream_id_item} ID item; nothing will be split")
      end
      @table
    end

    def stream_id_item(packet)
      return packet.get_item(@stream_id_item) if packet.items.key?(@stream_id_item)
      packet.id_items.find { |item| item.bit_offset == 0 && item.bit_size == 16 }
    end

    def split(data)
      return nil if data.length < CFS_TLM_HEADER_BYTES + 2
      entry = table[data[0, 2].unpack1('n')]
      return nil unless entry
      prefix = data[0, entry[:prefix_bytes]]
      offset = entry[:prefix_bytes]
      pieces = []
      while offset + entry[:id_bytes] <= data.length
        id = unpack_id(data[offset, entry[:id_bytes]])
        packet = entry[:channels][id]
        unless packet
          Logger.warn("ProtocolFprimeChannelSplitter: unknown channel id #{id} at byte #{offset}")
          break
        end
        piece = prefix + data[offset..]
        length = record_length(packet, piece)
        if length > piece.bytesize
          Logger.warn("ProtocolFprimeChannelSplitter: truncated channel id #{id} at byte #{offset}")
          break
        end
        piece = piece[0, length]
        piece[LENGTH_OFFSET, 2] = [piece.bytesize - 7].pack('n')
        pieces << piece
        offset += length - entry[:prefix_bytes]
      end
      pieces.empty? ? nil : pieces
    end

    # Bytes of a complete channel packet (header + descriptor + record) measured against the channel's items.
    # Assigning the buffer to a clone makes COSMOS recalculate the offsets of items that follow a
    # variable-length (string) item, so structs with a string in the middle measure correctly.
    def record_length(packet, buffer)
      return packet.defined_length if packet.fixed_size
      probe = packet.clone
      probe.buffer = buffer
      end_bits = probe.defined_length_bits
      probe.sorted_items.each do |item|
        next if item.data_type == :DERIVED || item.bit_offset < 0
        bits = if item.variable_bit_size
                 probe.send(:calculate_total_bit_size, item) # protected in openc3 6.9
               elsif item.array_size
                 item.array_size
               else
                 item.bit_size
               end
        end_bits = [end_bits, item.bit_offset + bits].max
      end
      (end_bits + 7) / 8
    end

    def unpack_id(bytes)
      bytes.each_byte.reduce(0) { |value, byte| (value << 8) | byte }
    end
  end
end
