# ADR 0004: PortAudio streams run on the event-loop thread

**Status:** accepted

Starting a WASAPI stream from an `asyncio.to_thread` worker failed with `PaErrorCode -9999 (Unanticipated host error)`,
while the identical call on the main thread worked. The audio backend therefore starts non-blocking streams on the
event-loop thread and polls `stream.active` with `asyncio.sleep`. PortAudio does its own audio I/O on its own thread, so
the event loop is not blocked during playback or recording. Cause assumed to be COM apartment state in worker threads; not
investigated further.
