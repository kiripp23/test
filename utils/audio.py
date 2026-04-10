"""Audio format conversion utilities."""

import audioop
import struct

# ulaw 8kHz mono -> PCM 16-bit 8kHz mono
def ulaw_to_pcm(data: bytes) -> bytes:
    return audioop.ulaw2lin(data, 2)


# PCM 16-bit 8kHz mono -> ulaw 8kHz mono
def pcm_to_ulaw(data: bytes) -> bytes:
    return audioop.lin2ulaw(data, 2)


# PCM 16-bit 16kHz -> PCM 16-bit 8kHz (downsample 2x)
def downsample_16k_to_8k(data: bytes) -> bytes:
    return audioop.ratecv(data, 2, 1, 16000, 8000, None)[0]


# PCM 16-bit 24kHz -> PCM 16-bit 8kHz (downsample 3x)
def downsample_24k_to_8k(data: bytes) -> bytes:
    # Ensure data length is a whole number of frames (2 bytes per sample)
    if len(data) % 2 != 0:
        data = data[:-1]
    return audioop.ratecv(data, 2, 1, 24000, 8000, None)[0]
