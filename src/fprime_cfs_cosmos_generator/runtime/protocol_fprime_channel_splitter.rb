# Generated F Prime cFS runtime support. Do not edit.
#
# Svc::TlmChan packs several channel records (id, time, value) into one com buffer and CfsTlmFramer wraps
# that buffer in a single CCSDS telemetry packet. COSMOS packets are fixed layouts, so this READ protocol
# splits such a packet into one packet per record, each carrying a copy of the cFS header and the F Prime
# descriptor, and hands them out one at a time (Interface#read drains pending data through the blank read).
# Works on every COSMOS 6.x. Packets whose stream id is not a channelized F Prime MID pass through untouched.
require 'openc3/interfaces/protocols/protocol'

module OpenC3
  class ProtocolFprimeChannelSplitter < Protocol
    CFS_TLM_HEADER_BYTES = 12
    STREAM_ID_ITEM = 'CCSDS_STREAMID'.freeze
    DESCRIPTOR_ITEM = 'FPRIME_DESCRIPTOR'.freeze
    CHANNEL_ID_ITEM = 'FPRIME_CHANNEL_ID'.freeze

    class << self
      attr_accessor :telemetry_source
    end

    def initialize(allow_empty_data = nil)
      super(allow_empty_data)
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
      records = split(data)
      return super(data, extra) if records.nil? || records.length <= 1
      @pending.concat(records[1..])
      return records[0], extra
    end

    private

    # { stream_id => { descriptor_bytes:, id_bytes:, channels: { channel_id => record_bytes } } }
    def table
      return @table if @table
      @table = {}
      telemetry = (self.class.telemetry_source || -> { System.telemetry }).call
      targets = @interface ? @interface.target_names : telemetry.target_names
      targets.each do |target|
        telemetry.packets(target).each_value do |packet|
          next unless packet.items.key?(STREAM_ID_ITEM) && packet.items.key?(CHANNEL_ID_ITEM)
          stream = packet.get_item(STREAM_ID_ITEM)
          channel = packet.get_item(CHANNEL_ID_ITEM)
          next unless stream.id_value && channel.id_value
          entry = @table[stream.id_value] ||= {
            descriptor_bytes: packet.items.key?(DESCRIPTOR_ITEM) ? packet.get_item(DESCRIPTOR_ITEM).bit_size / 8 : 0,
            id_bytes: channel.bit_size / 8,
            channels: {}
          }
          entry[:channels][channel.id_value] = packet.defined_length - CFS_TLM_HEADER_BYTES - entry[:descriptor_bytes]
        end
      end
      @table
    end

    def split(data)
      return nil if data.length < CFS_TLM_HEADER_BYTES + 2
      entry = table[data[0, 2].unpack1('n')]
      return nil unless entry
      prefix_length = CFS_TLM_HEADER_BYTES + entry[:descriptor_bytes]
      prefix = data[0, prefix_length]
      offset = prefix_length
      records = []
      while offset + entry[:id_bytes] <= data.length
        id = unpack_id(data[offset, entry[:id_bytes]])
        record_length = entry[:channels][id]
        unless record_length && offset + record_length <= data.length
          Logger.warn("ProtocolFprimeChannelSplitter: unknown or truncated channel record id #{id} at byte #{offset}")
          break
        end
        records << (prefix + data[offset, record_length])
        offset += record_length
      end
      records.empty? ? nil : records
    end

    def unpack_id(bytes)
      bytes.each_byte.reduce(0) { |value, byte| (value << 8) | byte }
    end
  end
end
