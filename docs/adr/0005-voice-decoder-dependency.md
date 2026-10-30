# ADR 0005: PyAV for offline voice decoding

**Status:** accepted (research extra only)

The glasses stream voice as 40-byte packets that decode as Opus (see PROTOCOL.md). To test decodings offline we need an
Opus decoder that works on raw packets. Options considered: `opuslib` and `pyogg` (need a separate `opus.dll` on
Windows), or `av` (PyAV), whose Windows wheel bundles FFmpeg with `libopus`, `speex` and `sbc`.

Decision: `av` is an optional extra (`uv sync --extra research`, also in the dev group). **No separate native library
is needed on Windows**; the wheel contains it. The core package and the BLE/audio features do not depend on it. The decoder
sits behind the `VoiceDecoder` protocol in `protocol/voice.py`; the PyAV implementation lives in `research/`.

PyAV returns float32 or int16 depending on the decoder, so `research/voice_decode.py` normalises to int16.
