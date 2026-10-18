import numpy as np

from conftest import FRAME, sine_frames
from airwire.opus import OpusDecoder, OpusEncoder, version


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def test_version_string():
    assert "opus" in version().lower()


def test_stereo_roundtrip_preserves_signal(packets):
    dec = OpusDecoder()
    out = np.concatenate([dec.decode(pkt) for pkt in packets])
    ref = np.concatenate(sine_frames(len(packets)))
    assert out.shape == ref.shape
    # Skip codec warm-up, then the output level must match the input level.
    assert abs(_rms(out[4800:]) - _rms(ref[4800:])) / _rms(ref[4800:]) < 0.1


def test_mono_stream_decodes_to_stereo():
    enc = OpusEncoder(channels=1, bitrate=64_000)
    dec = OpusDecoder(channels=2)
    frames = [dec.decode(enc.encode(f)) for f in sine_frames(20, channels=1)]
    assert all(f.shape == (FRAME, 2) for f in frames)
    assert _rms(frames[-1]) > 1000


def test_plc_and_fec_produce_requested_length(packets):
    dec = OpusDecoder()
    for pkt in packets[:5]:
        dec.decode(pkt)
    assert dec.decode(None, FRAME).shape == (FRAME, 2)
    assert dec.decode(packets[6], FRAME, fec=True).shape == (FRAME, 2)
    assert dec.decode(packets[6]).shape == (FRAME, 2)


def test_variable_frame_size():
    enc = OpusEncoder(channels=2)
    dec = OpusDecoder()
    twenty_ms = np.concatenate(sine_frames(2))
    assert dec.decode(enc.encode(twenty_ms)).shape == (960, 2)
