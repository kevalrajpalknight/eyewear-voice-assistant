# Approval request: recording commands (NOTHING IS SENT; this is a request, not a test)

Status 2026-11-01: both commands are INFERRED from the decompiled app only, `safe_to_send: false`
in `commands.yaml`, and the assistant's `start_video` and `start_audio_recording` tools are BLOCKED.
They are registered only if all three are true: the tool is listed in `TOOLS_ENABLED`,
`ALLOW_RECORDING_TOOLS=true`, and the command's status in `commands.yaml` is CONFIRMED. Even then the
tool stays blocked in code: the facade has no recording method. Nothing here changes unless you
approve the exact bytes below in writing and a facade method is added in a separate change.

## Exact bytes (from `commands.yaml`, characteristic `de5bf72a-d711-4e47-af26-65e3012a5dc7`)
| Name | Frame (hex) | glassesControl payload | Source |
|---|---|---|---|
| `video_start` | `bc4103005051020102` | `02 01 02` | the app's start-video method (static reading) |
| `audio_record_start` | `bc410300d056020108` | `02 01 08` | the app's start-record method (static reading) |

The frames are standard BC frames (command 0x41, length 3, CRC-16/MODBUS). The photo command
`02 01 01` is already CONFIRMED on this unit; these two are not.

## What I expect to happen (INFERRED, not observed)
- The glasses start recording video (or audio only) and a 0x41 reply arrives:
  `[function id][error code][activity byte]`. The app treats error 0 and 255 alike and then reads the
  activity byte: 2 = video recording, 8 = audio recording ("in this mode, end it first").
- A media-count change (videos +1 or audio +1) appears only after the recording is stopped and
  saved. A 0x73 event type 1 carries the counts.
- If the glasses are already in another mode (photo 1, transfer 4, firmware update 5, AI
  conversation 7, recording 8), the reply names that activity instead of starting.

## Busy-state replies visible in the app code (names only, INFERRED)
Activity byte 1 "photo mode", 2 "video recording", 4 "transfer mode", 5 "firmware update mode",
7 "AI conversation mode", 8 "recording mode". `ff ff` most plausibly means "accepted, nothing else
running", not "busy" (see PROTOCOL.md, "Replies to our requests").

## How a recording is stopped
- No stop command was found in the app (see PROTOCOL.md, "Recording and light commands, search
  2026-11-01"). The "stopRecord" names that exist belong to the phone microphone recorder of the
  translate screens, not the glasses.
- The app's handling suggests the SAME video command toggles (activity 2 leads to "end recording").
  Unverified; I will NOT send a second command to find out.
- **Your stop: a single click on the front button of the glasses.** You do this, not me.

## What could go wrong
- The glasses keep recording until stopped; a forgotten recording fills storage and drains battery.
- `02 01 08` is not known to be audio-only; the byte could mean something else on this firmware.
- A recording left running blocks other modes (photo, AI voice) until it is stopped.
- Storage on the glasses is shared with photos; a long recording could delete nothing, but I have
  no evidence about what the firmware does when full.
- Commands next to these are dangerous (`02 01 0A` factory reset, `02 01 0E` restart, `02 01 09`
  end of Wi-Fi session): the byte-level denylist beats any approval and stays in force.

## Supervised test steps (only after you approve in writing, one command per approval)
1. You, present with the glasses on and charged above 50%, close the phone app, and say "go".
2. I read the battery and media counts (both CONFIRMED reads) and show them.
3. I send ONE command (`video_start` first) after you type the confirmation yourself.
4. I print the 0x41 reply and the next 10 s of events. You watch the glasses.
5. You stop the recording with one click on the front button.
6. I read the media counts again and report: reply bytes, activity byte, count changes, and
   whether the recording appears. Result is recorded as CONFIRMED or left INFERRED.
7. `audio_record_start` is a separate approval and a separate run.

## What I need from you
A written approval naming the exact frame, or a refusal. Without it nothing in this document is
ever sent, and the assistant cannot start a recording.
