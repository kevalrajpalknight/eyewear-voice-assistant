# ADR 0004: PortAudio streams run on the event-loop thread

**Status:** accepted

Starting a WASAPI stream from an `asyncio.to_thread` worker failed with `PaErrorCode -9999 (Unanticipated host error)`,
while the identical call on the main thread worked. The audio backend therefore starts non-blocking streams on the
event-loop thread and polls `stream.active` with `asyncio.sleep`. PortAudio does its own audio I/O on its own thread, so
the event loop is not blocked during playback or recording. Cause assumed to be COM apartment state in worker threads; not
investigated further.

## Addendum: never import sounddevice in a BLE process

Importing `sounddevice` initialises COM in STA mode on the importing thread. bleak's WinRT backend then fails with
"Thread is configured for Windows GUI but callbacks are not working". `transport/audio_sd.py` therefore imports
sounddevice lazily, only when an audio command runs. Consequence: BLE and Windows audio must not share a process.
Phase 6 (voice assistant) must run audio in a subprocess or separate worker.
