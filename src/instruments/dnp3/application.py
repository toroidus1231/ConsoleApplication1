"""DNP3 application layer per IEEE 1815-2012 §11.

Request APDU layout:

    Byte 0   : Application Control (FIR/FIN/CON/UNS/SEQ)
    Byte 1   : Function Code
    Bytes 2+ : Object headers + objects

Response APDU layout:

    Byte 0   : Application Control
    Byte 1   : Function Code (RESPONSE=0x81 or UNSOLICITED_RESPONSE=0x82)
    Bytes 2-3: IIN (Internal Indications)
    Bytes 4+ : Object headers + objects

This module handles the request/response framing only — the object
headers and the objects themselves live in `objects.py`.
"""

from __future__ import annotations

import dataclasses


# Function codes — IEEE 1815-2012 §11.2 Table 11-3
FC_CONFIRM = 0x00
FC_READ = 0x01
FC_WRITE = 0x02
FC_SELECT = 0x03
FC_OPERATE = 0x04
FC_DIRECT_OPERATE = 0x05
FC_DIRECT_OPERATE_NR = 0x06
FC_FREEZE = 0x07
FC_COLD_RESTART = 0x0D
FC_WARM_RESTART = 0x0E
FC_INITIALIZE_DATA = 0x0F
FC_DELAY_MEASURE = 0x17
FC_RECORD_CURRENT_TIME = 0x18
FC_OPEN_FILE = 0x19
FC_CLOSE_FILE = 0x1A
FC_DELETE_FILE = 0x1B
FC_GET_FILE_INFO = 0x1C
FC_AUTH_FILE = 0x1D
FC_ABORT_FILE = 0x1E
FC_ENABLE_UNSOLICITED = 0x14
FC_DISABLE_UNSOLICITED = 0x15
FC_ASSIGN_CLASS = 0x16
FC_RESPONSE = 0x81
FC_UNSOLICITED_RESPONSE = 0x82
FC_AUTHENTICATION_RESPONSE = 0x83


# Application control bits — IEEE 1815-2012 §11.2.1 Figure 11-2
AC_FIR = 0x80
AC_FIN = 0x40
AC_CON = 0x20
AC_UNS = 0x10
AC_SEQ_MASK = 0x0F


# Internal Indications bit positions — IEEE 1815-2012 §11.3
class IIN:
    BROADCAST = 1 << 0
    CLASS_1_EVENTS = 1 << 1
    CLASS_2_EVENTS = 1 << 2
    CLASS_3_EVENTS = 1 << 3
    NEED_TIME = 1 << 4
    LOCAL_CONTROL = 1 << 5
    DEVICE_TROUBLE = 1 << 6
    DEVICE_RESTART = 1 << 7
    NO_FUNC_CODE_SUPPORT = 1 << 8
    OBJECT_UNKNOWN = 1 << 9
    PARAMETER_ERROR = 1 << 10
    EVENT_BUFFER_OVERFLOW = 1 << 11
    ALREADY_EXECUTING = 1 << 12
    CONFIG_CORRUPT = 1 << 13


@dataclasses.dataclass
class ApplicationControl:
    fir: bool = True
    fin: bool = True
    con: bool = False
    uns: bool = False
    sequence: int = 0

    def encode(self) -> int:
        b = 0
        if self.fir:
            b |= AC_FIR
        if self.fin:
            b |= AC_FIN
        if self.con:
            b |= AC_CON
        if self.uns:
            b |= AC_UNS
        b |= self.sequence & AC_SEQ_MASK
        return b

    @classmethod
    def decode(cls, b: int) -> "ApplicationControl":
        return cls(
            fir=bool(b & AC_FIR),
            fin=bool(b & AC_FIN),
            con=bool(b & AC_CON),
            uns=bool(b & AC_UNS),
            sequence=b & AC_SEQ_MASK,
        )


@dataclasses.dataclass
class Request:
    function_code: int
    application_control: ApplicationControl
    object_data: bytes = b""

    def encode(self) -> bytes:
        return bytes([self.application_control.encode(), self.function_code]) + self.object_data


@dataclasses.dataclass
class Response:
    function_code: int
    application_control: ApplicationControl
    iin: int = 0
    object_data: bytes = b""

    def encode(self) -> bytes:
        return (
            bytes([self.application_control.encode(), self.function_code])
            + self.iin.to_bytes(2, "little")
            + self.object_data
        )

    @classmethod
    def decode(cls, apdu: bytes) -> "Response":
        if len(apdu) < 4:
            raise ValueError(f"response APDU too short: {len(apdu)} bytes")
        ac = ApplicationControl.decode(apdu[0])
        fc = apdu[1]
        if fc not in (FC_RESPONSE, FC_UNSOLICITED_RESPONSE,
                      FC_AUTHENTICATION_RESPONSE):
            raise ValueError(f"not a response function code: {fc:#04x}")
        iin = int.from_bytes(apdu[2:4], "little")
        return cls(
            function_code=fc,
            application_control=ac,
            iin=iin,
            object_data=apdu[4:],
        )

    def has_iin(self, bit: int) -> bool:
        return bool(self.iin & bit)


def iin_decode(iin: int) -> dict[str, bool]:
    """Decode an IIN word into a dict of named flags for inspection."""
    return {
        "broadcast": bool(iin & IIN.BROADCAST),
        "class_1_events": bool(iin & IIN.CLASS_1_EVENTS),
        "class_2_events": bool(iin & IIN.CLASS_2_EVENTS),
        "class_3_events": bool(iin & IIN.CLASS_3_EVENTS),
        "need_time": bool(iin & IIN.NEED_TIME),
        "local_control": bool(iin & IIN.LOCAL_CONTROL),
        "device_trouble": bool(iin & IIN.DEVICE_TROUBLE),
        "device_restart": bool(iin & IIN.DEVICE_RESTART),
        "no_func_code_support": bool(iin & IIN.NO_FUNC_CODE_SUPPORT),
        "object_unknown": bool(iin & IIN.OBJECT_UNKNOWN),
        "parameter_error": bool(iin & IIN.PARAMETER_ERROR),
        "event_buffer_overflow": bool(iin & IIN.EVENT_BUFFER_OVERFLOW),
        "already_executing": bool(iin & IIN.ALREADY_EXECUTING),
        "config_corrupt": bool(iin & IIN.CONFIG_CORRUPT),
    }
