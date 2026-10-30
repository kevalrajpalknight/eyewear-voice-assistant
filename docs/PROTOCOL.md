# Protocol notes

Every claim is labelled CONFIRMED (observed on this unit) or INFERRED / UNVERIFIED.
Evidence: `aiglasses scan`, `gatt-dump` and `listen` runs against unit AIMB-G2_2324 on Windows 11 (bleak 3.0.2, uncached GATT services, no pairing), 2026-10-30.

## Verdict on the 0xAE00 / RCSP hypothesis

| Claim | Verdict | Evidence |
|---|---|---|
| Custom service 0xAE00 with AE01 (write-no-resp) and AE02 (notify) | **DENIED as stated** | No 0xAE00 service exists. The unit has 0xAE30 (AE01, AE02, AE03, AE04, AE05, AE10) and 0xAE3A (AE3B, AE3C). The AE01 write-no-response and AE02 notify characteristics do exist, under 0xAE30. |
| Jieli-style control channel | **INFERRED (plausible)** | AE-prefixed custom UUIDs with write-no-response + notify pairs match the Jieli pattern; Jieli JL7018F is claimed by the spec sheet (unverified). |
| RCSP framing (`FE DC BA ... EF` or `FE EF AA 55 | len | payload | sum16`) | **DENIED on the one channel that has spoken, INCONCLUSIVE elsewhere** | All 756 notifications seen so far (all on `de5bf729`) use a different frame: `BC | cmd | len16 | crc16-MODBUS | payload`. The 0xAE30 / 0xAE3A / Nordic UART channels have produced no traffic yet, so RCSP on them is untested. |

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

## Notification frame format (CONFIRMED)

All notifications seen so far are on `de5bf729` (handle 143) and use this layout:

`BC | cmd (1) | length (uint16 LE) | crc16 (uint16 LE) | payload (length bytes)`

The CRC is CRC-16/MODBUS (poly 0x8005, init 0xFFFF, reflected, no final xor) over the payload only. **756 of 756 captured
notifications satisfy it**, across 5 sessions. Parser: `protocol/bcframe.py`. Earlier notes called the first two payload
bytes a "trailer"; that was wrong, they are the CRC.

Commands seen: `0x73` (control/status events) and `0x59` (voice stream).

## Voice stream, cmd 0x59 (frame layout CONFIRMED, audio content NOT yet confirmed)

- CONFIRMED: payload is 40 bytes; a burst is 200 to 289 frames at 20 ms spacing (about 50 per second).
- CONFIRMED: the payloads decode as Opus. `4b 41` at the start of most payloads is a valid Opus TOC byte pair (config 9 =
  SILK wideband 20 ms, "code 3" with padding, 1 frame). Silence frames are a few bytes of data zero-padded to 40 bytes. FFmpeg's
  libopus decoded 289 of 289 packets of the 19:04 burst with no errors, 960 samples (20 ms) per packet at 48 kHz.
- CONFIRMED by elimination (`aiglasses voice candidates`, burst of 289 packets): only the CRC-removed 40-byte packet decodes
  cleanly. Treating the CRC as part of the packet, or stripping 1, 3 or 4 bytes, fails (11 to 149 decode errors out of 289, or
  clipped noise). Speex failed to open without a header. SBC/mSBC is excluded: 0 of 289 packets start with the sync bytes
  0x9C/0xAD.
- The decoded signal is very quiet: rms about 17 and peak 368 out of 32767 (about -52 dBFS rms). A peak-normalised copy is
  written for listening. This matches the low level of the Windows microphone recording.
- UNVERIFIED: whether it contains intelligible speech. Listen to
  `logs/decode_candidates/burst2_opus_strip2_crc_removed_48000Hz_mono_PEAK-NORMALIZED.wav` (and the unnormalised file).
  Not to be called a success until a human hears speech.
- INFERRED: the burst is bracketed by `0x73` markers with payload `0301` (start) and `0a01` (end). Rear single click starts it
  (confirmed in the labelled 19:03 session).

## Control events, cmd 0x73 (payload shown after the CRC is removed)

| Payload | Meaning | Status |
|---|---|---|
| `0301` | voice burst start | INFERRED (precedes every burst) |
| `0a01` | voice burst end | INFERRED (follows every burst) |
| `01 NN 00 00 00 00 00 01` (8 bytes) | front click (photo). NN was 05, 06, 07 on three photos | CONFIRMED trigger, NN as running count INFERRED |
| `01 08 00 02 00 00 00 01` | stop recording. Same layout, NN=08, byte 3 = 02 | CONFIRMED trigger, "02 = video" INFERRED |
| `02 00 NN 02 00` (5 bytes) | rear double click (image recognition). NN was 0x17, 0x16, 0x15, 0x16 | CONFIRMED trigger, NN meaning unknown |
| `0b2a` ... `0b34` (2 bytes) | sent every exactly 3.000 s while recording; a slowly rising 16-bit value; stops when recording stops | CONFIRMED timing, meaning unknown |
| `12 01 00 10 NN 02 00 0f 0f 03 00 10 0a 03` (14 bytes) | NN went 0x0f then 0x10 in the 18:55 session, after the front click, rear click and rear double click; INFERRED volume level from touch-pad swipes (UNVERIFIED) | UNVERIFIED |

Previous notes saying the touch pad sends nothing were wrong: the 14-byte events are the likely touch-pad events.

## Photo/recording timing (CONFIRMED, labelled run at 19:03 UTC)

| Action | Delay to event |
|---|---|
| Front click (photo) | 4.3 s |
| Rear click (AI assistant) | 1.8 s to burst start; burst 5.8 s |
| Rear double click (image recognition) | 4.6 s |
| Front double click (start recording) | heartbeat starts about 11 s later in one run (the mark may have been typed early) |
| Front click (stop recording) | 8-byte event with the next 3 s tick, then no more heartbeats |

## Commands (writes)

None known. `protocol/commands.yaml` stays empty until the Phase 3 capture.
