"""cFS identifiers of an F Prime application hosted in cFS (fprime_cfs), derived from its dictionary

fprime_cfs frames F Prime packets as CCSDS space packets on the cFS software bus; the stream (message) identifier
carries the APID the deployment configured in ComCfg.Apid and commands carry the function code published as the
ComCfg.FprimeCommandFunctionCode constant.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from .dictionary import APID_ENUM, CFS_FUNCTION_CODE_CONSTANT, DictionaryError, FprimeDictionary
from .emit import identifier

# Stream identifier (cFS message identifier) prefixes: packet version 0, secondary header flag set, and the
# packet type bit for commands; the APID occupies the low 11 bits
COMMAND_STREAM_ID = 0x1800
TELEMETRY_STREAM_ID = 0x0800
APID_MASK = 0x07FF
COMMAND_APID = "FW_PACKET_COMMAND"
CHANNELS_APID = "FW_PACKET_TELEM"
PACKETS_APID = "FW_PACKET_PACKETIZED_TLM"
EVENTS_APID = "FW_PACKET_LOG"
# Ruby conversion: cFS COSMOS targets are Ruby targets
TIME_CONVERSION = "openc3/conversions/unix_time_conversion.rb"


@dataclass(frozen=True)
class CfsApp:
    """One F Prime application hosted in cFS: its dictionary and the identifiers derived from ComCfg"""

    dictionary: FprimeDictionary
    name: str
    command_mid: int
    channels_mid: int
    packets_mid: int
    function_code: int
    events_mid: int | None = None  # None (events not modeled) when ComCfg.Apid or the packet types lack FW_PACKET_LOG

    def message_ids(self, channelized: bool = True) -> dict[str, int]:
        ids = {"command": self.command_mid, "packetized telemetry": self.packets_mid}
        if channelized:
            ids["channelized telemetry"] = self.channels_mid
        if self.events_mid is not None:
            ids["events"] = self.events_mid
        return ids


def _stream_id(prefix: int, apids: dict[str, int], key: str, dictionary: FprimeDictionary) -> int:
    apid = apids[key]
    if not 0 <= apid <= APID_MASK:
        raise DictionaryError(f"{dictionary.path.name}: {APID_ENUM}.{key} = {apid:#x} is not an 11 bit APID")
    return prefix | apid


def cfs_app(dictionary: FprimeDictionary) -> CfsApp:
    """Derive the cFS identifiers of an F Prime application from its dictionary, or raise DictionaryError"""
    name = dictionary.path.name
    apids = dictionary.apids
    if not apids:
        raise DictionaryError(f"{name}: no {APID_ENUM} enumeration; cFS hosted F Prime dictionaries define it")
    missing = [key for key in (COMMAND_APID, CHANNELS_APID, PACKETS_APID) if key not in apids]
    if missing:
        raise DictionaryError(f"{name}: {APID_ENUM} lacks {', '.join(missing)}")
    if dictionary.cfs_function_code is None:
        raise DictionaryError(f"{name}: missing dictionary constant {CFS_FUNCTION_CODE_CONSTANT}")
    if not 0 <= dictionary.cfs_function_code <= 0x7F:
        raise DictionaryError(f"{name}: {CFS_FUNCTION_CODE_CONSTANT} = {dictionary.cfs_function_code} exceeds 7 bits")
    events_mid = None
    if EVENTS_APID in apids and dictionary.layout.log_descriptor is not None:
        events_mid = _stream_id(TELEMETRY_STREAM_ID, apids, EVENTS_APID, dictionary)
    else:
        warnings.warn(
            f"{name}: no {EVENTS_APID} in {APID_ENUM} or the packet-type enumeration; FPRIME_EVENTS is not generated "
            "and F Prime events will not be identified in COSMOS",
            stacklevel=2,
        )
    return CfsApp(
        dictionary=dictionary,
        name=identifier(dictionary.deployment_name.rsplit(".", 1)[-1]),
        command_mid=_stream_id(COMMAND_STREAM_ID, apids, COMMAND_APID, dictionary),
        channels_mid=_stream_id(TELEMETRY_STREAM_ID, apids, CHANNELS_APID, dictionary),
        packets_mid=_stream_id(TELEMETRY_STREAM_ID, apids, PACKETS_APID, dictionary),
        function_code=dictionary.cfs_function_code,
        events_mid=events_mid,
    )
