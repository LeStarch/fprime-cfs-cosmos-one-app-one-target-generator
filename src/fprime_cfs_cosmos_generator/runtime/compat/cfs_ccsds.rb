# Generated F Prime cFS compatibility helper for nasa/cfs-cosmos-plugin. Do not edit.
#
# Ccsds.cmd / Ccsds.tlm emit the cFE 7 CCSDS header items the generated F Prime cmd/tlm definitions start with,
# matching the layout of nasa/cfs-cosmos-plugin's cfs_cmd_hdr / cfs_tlm_hdr (cfs_packet_headers.rb):
#   command:   6-byte primary header + function code + checksum              (payload at byte 8)
#   telemetry: 6-byte primary header + seconds + subseconds + 4 spare bytes  (payload at byte 16)
# PACKET_TIME is not derived here: its READ_CONVERSION (unix_time_conversion_epoch_offset.rb) lives in another
# target's lib directory, which a per-target decom microservice cannot load. The generated FPRIME_TIME item
# carries the F Prime timestamp instead.
module Ccsds
  CMD_SECONDARY_HEADER_BYTES = 2

  # Message ids arrive as the strings of the generated *_msg_id.rb files ('0x1800') or as Integers.
  def self.resolve(mid)
    return mid if mid.is_a?(Integer)
    Integer(mid.to_s, 16)
  end

  def self.cmd(mid, fc, data_len)
    apid = format('0x%04X', resolve(mid))
    length = Integer(data_len) + CMD_SECONDARY_HEADER_BYTES - 1
    <<~HEADER
      APPEND_ID_PARAMETER CCSDS_STREAMID 16 UINT MIN_UINT16 MAX_UINT16 #{apid} "CCSDS Packet Identification" BIG_ENDIAN
        FORMAT_STRING "0x%04X"
      APPEND_PARAMETER CCSDS_SEQUENCE 16 UINT MIN_UINT16 MAX_UINT16 0xC000 "CCSDS Packet Sequence Control" BIG_ENDIAN
        FORMAT_STRING "0x%04X"
      APPEND_PARAMETER CCSDS_LENGTH 16 UINT MIN_UINT16 MAX_UINT16 #{length} "CCSDS Packet Data Length" BIG_ENDIAN
      APPEND_PARAMETER CCSDS_FC 8 UINT MIN_UINT8 MAX_UINT8 #{Integer(fc)} "CCSDS Command Function Code"
      APPEND_PARAMETER CCSDS_CHECKSUM 8 UINT MIN_UINT8 MAX_UINT8 0 "CCSDS Command Checksum"
    HEADER
  end

  def self.tlm(mid)
    apid = format('0x%04X', resolve(mid))
    <<~HEADER
      APPEND_ID_ITEM CCSDS_STREAMID 16 UINT #{apid} "CCSDS Packet Identification" BIG_ENDIAN
        FORMAT_STRING "0x%04X"
      APPEND_ITEM CCSDS_SEQUENCE 16 UINT "CCSDS Packet Sequence Control" BIG_ENDIAN
        FORMAT_STRING "0x%04X"
      APPEND_ITEM CCSDS_LENGTH 16 UINT "CCSDS Packet Data Length" BIG_ENDIAN
      APPEND_ITEM SECONDS 32 UINT "CCSDS Telemetry Secondary Header (seconds)" BIG_ENDIAN
      APPEND_ITEM SUBSECS 16 UINT "CCSDS Telemetry Secondary Header (subseconds)" BIG_ENDIAN
      APPEND_ITEM SPARE_FOR_64_ALIGN 32 UINT "Spare padding for 64-bit alignment"
    HEADER
  end
end
