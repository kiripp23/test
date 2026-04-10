"""Minimal RTP packet handling for ulaw/alaw audio."""

import struct

PAYLOAD_ULAW = 0
PAYLOAD_ALAW = 8
SAMPLES_PER_FRAME = 160  # 20ms at 8kHz
SAMPLE_RATE = 8000


class RTPPacket:
    """Parse or build an RTP packet."""

    __slots__ = ("version", "payload_type", "sequence", "timestamp", "ssrc", "payload")

    def __init__(
        self,
        payload: bytes,
        payload_type: int = PAYLOAD_ULAW,
        sequence: int = 0,
        timestamp: int = 0,
        ssrc: int = 0,
    ):
        self.version = 2
        self.payload_type = payload_type
        self.sequence = sequence
        self.timestamp = timestamp
        self.ssrc = ssrc
        self.payload = payload

    @classmethod
    def parse(cls, data: bytes) -> "RTPPacket":
        if len(data) < 12:
            raise ValueError("RTP packet too short")
        first_byte = data[0]
        cc = first_byte & 0x0F
        header_len = 12 + cc * 4

        # Check for extension
        if first_byte & 0x10:
            if len(data) < header_len + 4:
                raise ValueError("RTP extension header too short")
            ext_len = struct.unpack("!H", data[header_len + 2 : header_len + 4])[0]
            header_len += 4 + ext_len * 4

        second_byte = data[1]
        payload_type = second_byte & 0x7F
        sequence = struct.unpack("!H", data[2:4])[0]
        timestamp = struct.unpack("!I", data[4:8])[0]
        ssrc = struct.unpack("!I", data[8:12])[0]

        return cls(
            payload=data[header_len:],
            payload_type=payload_type,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
        )

    def build(self) -> bytes:
        header = struct.pack(
            "!BBHII",
            0x80,  # V=2, P=0, X=0, CC=0
            self.payload_type & 0x7F,
            self.sequence & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc & 0xFFFFFFFF,
        )
        return header + self.payload


class RTPSender:
    """Manages RTP sequence/timestamp for outgoing stream."""

    def __init__(self, ssrc: int = 1234, payload_type: int = PAYLOAD_ULAW):
        self.ssrc = ssrc
        self.payload_type = payload_type
        self.sequence = 0
        self.timestamp = 0

    def make_packet(self, payload: bytes) -> bytes:
        pkt = RTPPacket(
            payload=payload,
            payload_type=self.payload_type,
            sequence=self.sequence,
            timestamp=self.timestamp,
            ssrc=self.ssrc,
        )
        self.sequence = (self.sequence + 1) & 0xFFFF
        self.timestamp = (self.timestamp + len(payload)) & 0xFFFFFFFF
        return pkt.build()
