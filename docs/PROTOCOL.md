# Protocol notes

Every claim is labelled CONFIRMED (observed on this unit) or INFERRED / UNVERIFIED.
Evidence: `aiglasses scan`, `gatt-dump` and `listen` runs against unit AIMB-G2_2324 on Windows 11 (bleak 3.0.2, uncached GATT services, no pairing), 2026-10-30.

## Verdict on the 0xAE00 / RCSP hypothesis

| Claim | Verdict | Evidence |
|---|---|---|
| Custom service 0xAE00 with AE01 (write-no-resp) and AE02 (notify) | **DENIED as stated** | No 0xAE00 service exists. The unit has 0xAE30 (AE01, AE02, AE03, AE04, AE05, AE10) and 0xAE3A (AE3B, AE3C). The AE01 write-no-response and AE02 notify characteristics do exist, under 0xAE30. |
| Jieli-style control channel | **INFERRED (plausible)** | AE-prefixed custom UUIDs with write-no-response + notify pairs match the Jieli pattern; Jieli JL7018F is claimed by the spec sheet (unverified). |
| RCSP framing (`FE DC BA ... EF` or `FE EF AA 55 | len | payload | sum16`) | **DENIED on the one channel that has spoken, INCONCLUSIVE elsewhere** | All 294 notifications seen so far (all on `de5bf729`) start with `0xBC`, not either RCSP header. The 0xAE30 / 0xAE3A / Nordic UART channels have produced no traffic yet, so RCSP on them is untested. |

The command side still needs the Phase 3 capture of the official app.

## Advertising (CONFIRMED)

- Name `AIMB-G2_<last 2 MAC bytes>`; MAC `D0:4B:1B:C3:23:24`.
- No service UUIDs advertised; service data `0x3802` = own MAC bytes; manufacturer ID `0x1234`, empty payload.
- The unit stops advertising after a while or while another central is connected; toggling glasses Bluetooth makes it advertise again.
- Windows lists it under "New devices" as an Audio device, i.e. it also advertises Classic Bluetooth audio. CONFIRMED after the user paired it in Windows: it then exposes an A2DP speaker and an HFP microphone (see CAPABILITIES.md). BLE keeps working while Classic is connected.

## GATT (CONFIRMED, MTU 517, connects without pairing; full data in `logs/gatt_dump.json`)

| Service | Characteristics (handle: properties) |
|---|---|
| 0x1800 Generic Access | 2A00 (2: read, write) = `AIMB-G2_2324` |
| 0xAE30 | AE01 (5: write-no-resp), AE02 (7: notify), AE03 (10: write-no-resp), AE04 (12: notify), AE05 (15: indicate), AE10 (18: read, write; empty) |
| 0x3802 | 4A02 (49: read, notify, write; empty) |
| 0xAE3A | AE3B (65: write-no-resp), AE3C (67: notify) |
| 0x1801 Generic Attribute | 2A05 (71: indicate). Enabling it is refused with "Access Denied", so it needs a bonded link. |
| Nordic UART `6e40fff0-b5a3-f393-e0a9-e50e24dcca9e` | 6e400002 (135: write, write-no-resp), 6e400003 (137: notify) |
| Vendor `de5bf728-d711-4e47-af26-65e3012a5dc7` | de5bf72a (141: write, write-no-resp), de5bf729 (143: notify) |
| 0x180A Device Information | 2A25 serial (empty), 2A27 hardware `AM01G1_V9.4`, 2A26 firmware `AM01G1_9.40.00_260114`, 2A23 System ID `2423c300001b4bd0` |
| 0xFEE1 | FEE3 (160: read, write, notify; empty) |

- CONFIRMED: no Battery service (0x180F); battery must come through a custom command.
- INFERRED: System ID `24 23 c3 00 00 1b 4b d0` encodes the MAC bytes in reverse order (24 23 C3 ... 1B 4B D0 = D0:4B:1B:C3:23:24, with 00 00 filling the manufacturer-defined bytes).
- CONFIRMED: 8 of 9 notify/indicate characteristics accept the standard CCCD enable.

## Notifications (all observed on `de5bf729`, handle 143)

- CONFIRMED: 291 notifications in one 60 s session (Windows Classic Bluetooth was connected and music was playing at the time; trace in `logs/trace.jsonl`) plus 1 in an earlier session. All of them fit this layout, with zero exceptions:

  `BC | cmd (1 byte) | length (uint16 little-endian) | payload (length bytes) | 2-byte trailer`

  This is INFERRED as the frame format; the field meanings below are guesses.
- CONFIRMED observed commands:

  | cmd | length | Count | Example (hex) | Notes |
  |---|---|---|---|---|
  | 0x73 | 8 | 1 | `bc73 0800 50c70101 00000000 0001` | Seen about 27 s into the session. |
  | 0x73 | 2 | 2 | `bc73 0200 c080 0301`, `bc73 0200 c6d0 0a01` | One right before the 0x59 burst, one right after it. Possibly start/stop markers (INFERRED). |
  | 0x59 | 40 | 288 | `bc59 2800 0f8d4b41 110be84a...` | A burst of about 288 frames in about 6 s (roughly 48 per second). |
  | 0x73 | 3 | 1 (earlier session) | `bc73 0300 523105 4700` | |

- INFERRED: the 0x59 burst is a stream of encoded audio from the glasses (fixed 40-byte payloads, repeating identical frames with zero padding like codec frames of silence, constant `4b 41` bytes in each frame). Not confirmed; the codec, and whether this is microphone audio, are unknown. What triggered it is also unknown (the user needs to say what they did at that moment).
- INFERRED: trailer meaning unknown; values seen (`0001`, `0301`, `0a01`, `4700`) look like counters or flags, not a CRC.
- CONFIRMED (test session 18:55 UTC, user did the front click, rear click, rear double click, swipes and touch double-tap without labelled timings): besides the 0x59 burst (239 frames, about 5 s, framed by `0x73` messages `c080 0301` and `c6d0 0a01`), only three further events arrived, all on `de5bf729`:
  - `bc73 0500 eca4020017 0200` about 19 s after connect.
  - `bc73 0e00 3a99120100100f02000f0f03 0010 0a03` about 32 s after connect, and `bc73 0e00 0b3c120100101002000f0f03 0010 0a03` about 47 s after connect. Same layout; only bytes 0-1 and byte 6 differ (0x0f then 0x10, i.e. 15 then 16). INFERRED: byte 6 is a counter such as a media item number; bytes 0-1 may be a timestamp or id.
  - Nothing at all arrived in the last 50 s of the 100 s session, which is when the touch pad swipes and double-tap were done. INFERRED: touch-pad music and volume controls do not use this BLE channel (probably Classic Bluetooth AVRCP, which Windows handles itself). Not yet verified by checking whether the music paused and the volume changed.
- CONFIRMED (labelled session 19:03 UTC, marks typed as each action was done; all events on `de5bf729`):

  | Action (mark) | Delay | What arrived |
  |---|---|---|
  | Front button single click (photo) | 4.3 s | `bc73 0800 36c70107 00000000 0001` (cmd 0x73, len 8) |
  | Rear button single click (AI assistant), spoke a phrase | 1.8 s | `0x73 c080 0301` (start), then 289 `0x59` frames over 5.8 s (about 50 per second), then `0x73 c6d0 0a01` (end) |
  | Rear button double click (image recognition) | 4.6 s | `bc73 0500 4d64020015 0200` (cmd 0x73, len 5) |
  | Front button double click (start recording) | 11.3 s | Then a `0x73` len-2 event every exactly 3.000 s: payloads `875f`, `869c`, `475c`, `8755`, `8755`, `4695`, `0757` with trailers `0b2a`, `0b2e`, `0b2f`, `0b32`, `0b32`, `0b33`, `0b34` |
  | Front button single click (stop recording) | not captured | The session ended while the glasses were still recording |

- INFERRED: the `0x59` burst is the voice-assistant microphone stream. It correlates with the rear single click in the labelled run, and with the earlier unlabelled bursts. 40-byte frames at about 50 per second is 16 kbps, which fits a 16 kHz Opus voice codec (common in Jieli AI voice SDKs). The codec is not verified; the audio has not been decoded.
- INFERRED: `0x73` with len 8 means photo taken, and with len 5 means image-recognition capture; both arrive about 4.5 s after the press, which would be the capture time. Not verified.
- INFERRED: the 3 s `0x73` len-2 events are a recording-in-progress heartbeat or status (the trailer rises slowly, but the meaning is unknown). Start of heartbeats was 11.3 s after the mark, so either the recording started late or the mark was typed early.
- UNVERIFIED: touch pad (swipe, double-tap) traffic; nothing was captured for it in the 18:55 session.

## Frame format and commands

No write command is known. The notification layout above is the only framing evidence. `protocol/commands.yaml` stays empty until the Phase 3 capture.
