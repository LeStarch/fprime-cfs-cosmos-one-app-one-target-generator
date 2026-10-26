# Test stub reproducing the inline header contract described by the package owner
require 'erb'
module Ccsds
  CMD_HDR = %q{
  APPEND_ID_PARAMETER CCSDS_STREAMID  16      UINT  MIN_UINT16  MAX_UINT16  <%= apid %>  "CCSDS Packet Identification" BIG_ENDIAN
  APPEND_PARAMETER    CCSDS_SEQUENCE  16      UINT  MIN_UINT16  MAX_UINT16  0xC000       "CCSDS Packet Sequence Control" BIG_ENDIAN
  APPEND_PARAMETER    CCSDS_LENGTH    16      UINT  MIN_UINT16  MAX_UINT16  <%= len %>   "CCSDS Packet Data Length" BIG_ENDIAN
  APPEND_PARAMETER    CCSDS_FC        8       UINT  MIN_UINT8   MAX_UINT8   <%= fc %>    "CCSDS Command Function Code" BIG_ENDIAN
  APPEND_PARAMETER    CCSDS_CHECKSUM  8       UINT  MIN_UINT8   MAX_UINT8   0            "CCSDS Command Checksum" BIG_ENDIAN
}
  TLM_HDR = %q{
  APPEND_ID_ITEM CCSDS_STREAMID 16 UINT <%= apid %> "CCSDS Packet Identification" BIG_ENDIAN
  APPEND_ITEM    CCSDS_SEQUENCE 16 UINT "CCSDS Packet Sequence Control" BIG_ENDIAN
  APPEND_ITEM    CCSDS_LENGTH   16 UINT "CCSDS Packet Data Length" BIG_ENDIAN
  APPEND_ITEM    CCSDS_SECONDS  32 UINT "CCSDS Telemetry Secondary Header (seconds)" BIG_ENDIAN
  APPEND_ITEM    CCSDS_SUBSECS  16 UINT "CCSDS Telemetry Secondary Header (subseconds)" BIG_ENDIAN
}
  def self.resolve(mid)
    Integer(mid, 16)
  end

  def self.cmd(mid, fc, data_len)
    apid = format('0x%04X', resolve(mid))
    len = data_len + 2 - 1   # secondary header (fc + checksum) + data, minus one per CCSDS
    ERB.new(CMD_HDR).result(binding)
  end

  def self.tlm(mid)
    apid = format('0x%04X', resolve(mid))
    ERB.new(TLM_HDR).result(binding)
  end
end
