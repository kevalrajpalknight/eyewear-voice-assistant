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

## Facts reported by the official app (user observation, 2026-10-31)

- CONFIRMED (app home screen): device AIMB-G2_2324, D0:4B:1B:C3:23:24, FW 9.40.00, a line `wifi: 2512271030`, status Connected, battery 100%.
- Quick Functions: Take photo, Record video, Recording (audio only), Smart image recognition. Function settings: AI translation,
  "Learn to take photos and videos". Tabs: Home, AI, Album.
- INFERRED: `2512271030` looks like a build timestamp (2025-12-27 10:30), probably of the Wi-Fi/camera side firmware, since it is shown on the
  "wifi:" line. These values are searched for automatically in `capture analyze`.
- INFERRED: the Album tab is the likely trigger for the glasses' 2.4 GHz Wi-Fi (the phone is on 5 GHz and must switch networks).

## Handle numbers

bleak on Windows reports each characteristic's **declaration** handle. The ATT value handle is one higher (for example
`de5bf729` is listed as 143 but is read and notified on ATT handle 144; its CCCD is 145, which matches the dump). Phone
captures show ATT handles, so `capture analyze` maps value handle = dump handle + 1. INFERRED from the attribute layout; to be
confirmed by the first capture.

## Phone capture attempts

- 2026-10-31, OnePlus Nord CE 2 (Android 13), on-phone bug report taken at 10:33:40: **no usable capture.** The only Bluetooth log
  (`BT_HCI_2026_1031_100407.cfa`) is a valid btsnoop header with 0 packets, there is no text snoop section, and the logcat covers
  only 09:37 to 10:08. The Cyan app wrote no logcat lines. Actions done: connect, photo, audio recording, smart recognition,
  video recording (10:30 to 10:33). Next: smoke test with Bluetooth switched off before the report (see docs/CAPTURE.md).
- The only glasses-related line in the report is `BtGatt.GattService onWriteCharacteristic() ... D0:4B:1B:C3:23:24` at 09:59:58, which shows the
  app wrote a characteristic that morning but not what.

## Evidence from the phone bug reports (OnePlus Nord CE 2, 2026-10-31; `aiglasses capture triage`)

None of the four reports contains Bluetooth packets, but they do hold Cyan-specific facts. Sources are Android's own state dumps for the
app (`com.aitowe.aitoglasses`, uid 10096) and the glasses address.

**Wi-Fi link is Wi-Fi Direct (P2P), not a normal hotspot** (CONFIRMED in the 10:45 report, one occurrence):
- The app called `WifiP2pManager.DISCOVER_PEERS` at 10:46:41 and `CONNECT` at 10:46:47.144. The glasses answered a push-button (PBC)
  provisioning request at 10:46:47.259, GO negotiation succeeded at 10:46:47.419, the group started at 10:46:48.303, and the glasses
  joined as a client at 10:46:48.354. About 1.2 s from connect to joined.
- The phone was the **group owner** (network `DIRECT-ym-OnePlus Nord CE 2`, owner IP 192.168.49.1). The glasses appeared as a P2P peer
  named `AIMB-G2_D04B1BC32324` with P2P device address `60:c2:2a:65:83:63` (different from the Bluetooth address; probably the Wi-Fi chip).
  The glasses' own IP and the transfer protocol are NOT in the reports.
- INFERRED: the glasses only need a push-button confirmation; no password was involved in the negotiation. The group passphrase is
  generated by the group owner and is not part of this project's data.
- INFERRED: the P2P device info says "WFD enabled: true" (Wi-Fi Display capability flag). Meaning unknown.
- OBSERVED (single occurrence, trigger unconfirmed): the Wi-Fi Direct start at 10:46:41 followed whatever the user was doing around
  10:46:30 to 10:46:50 (they do not remember what). `MainActivity` came to the foreground at 10:46:41 and the BLE connection had started
  at 10:46:08, so the trigger may be opening the Album or a transfer. The BLE command that makes the glasses discoverable is still unknown.
- Consequence: the original SoftAP hypothesis is probably wrong. For a PC, the route is Wi-Fi Direct (Windows has a Wi-Fi Direct API) and
  needs a Wi-Fi adapter that supports it; not yet tested.

**Cloud**: the app keeps HTTPS (port 443) connections open to `47.92.167.140`, and at other times `47.254.114.161` and `135.222.38.13`.
Only the addresses and the pattern of use are recorded. Who operates them is UNVERIFIED and not needed now. They are probably the AI backend,
which is also unconfirmed. This project does not contact or probe them.

**Classic Bluetooth**: the glasses are a dual-mode device (`DUAL`) with A2DP and headset profiles enabled; A2DP codec **SBC**.

**Timeline of the app on the phone** (UsageStats, GATT history, audio log):
- 09:39:28 app installed; 09:39:43 first launch (splash, then login); 09:44:29 main screen after login.
- 09:52:11 to 09:52:18 bind/pairing screens (`BindGuideActivity`, `DeviceBindActivity`); BLE connection from the app to the glasses starts
  at 09:52:18 and ends about 10:03 (first report: at least 7.3 min; second: 10.7 min).
- 09:53:42 A2DP audio connected. 10:04:02 A2DP dropped and 10:04:17 reconnected (Bluetooth switched off/on; the snoop log file starts 10:04:07).
- 10:30:03 A2DP dropped, 10:30:09 back. 10:34:55 A2DP dropped. 10:46:08 BLE connection (42 s at dump time), 10:46:12 A2DP.
- The app is one activity (`MainActivity`), so Android does not show which tab or button was used.

**Not in the reports**: any BLE command, the app's own log lines, and the Wi-Fi password/transfer protocol.

## PC Wi-Fi Direct capability (read-only check, 2026-10-31)

CONFIRMED on this PC (Intel Wi-Fi 6 AX201, driver 23.160.0.4, Windows 11 build 26300; `netsh wlan show wirelesscapabilities`):
Wi-Fi Direct Device, Group Owner and Client are all supported, P2P device discovery is supported, group owner on 5 GHz is supported (not 6 GHz),
the Microsoft Wi-Fi Direct virtual adapters exist, and the WinRT `Windows.Devices.WiFiDirect` types load. Nothing was started, advertised or connected.
The adapter reports Soft AP as not supported; that is a separate feature and does not affect Wi-Fi Direct.

In the observed phone session the phone was group owner and the glasses joined as client, so a PC would need to take the phone's role: connect with a
high group-owner intent and push-button (PBC) pairing. Both are available in the WinRT API (INFERRED from the API documentation, untested).
UNVERIFIED: whether Windows can hold a Wi-Fi Direct group and a normal Wi-Fi connection at once on this adapter (the glasses are probably 2.4 GHz only, and
the phone's own Wi-Fi was on 5 GHz); if not, a second USB Wi-Fi adapter, or running the Wi-Fi side from the phone, is the fallback. The Python route needs the
`winrt-Windows.Devices.WiFiDirect` package, not installed yet (would need an ADR).

## APK findings (INFERRED, from reading the decompiled app; "matches capture" where our own data agrees)

Source: the decompiled Java of `base.apk` (Cyan Glasses 1.0.2.18), read locally in a gitignored folder. No code is copied here; these are observations
(numbers, names, call order). Nothing is confirmed on the device until stated. Tools: `aiglasses apk packages|search|methods`.

### Channel and frame
- The SDK is Oudmon "QC" (not Jieli RCSP). Writes go to `de5bf72a` (write without response), notifications arrive on `de5bf729`. Frame:
  `BC | cmd | len16 LE | crc16-MODBUS of payload | payload`, with CRC `FFFF` for an empty payload. Matches capture (756/756 frames).
- The same characteristic and framing are used by the firmware update (cmd 1 to 5), file transfers and a factory test, so a write to this
  characteristic can be dangerous. The byte-level denylist below covers them.

### Command numbers (cmd byte)
| Hex | Name in the app | Notes |
|---|---|---|
| 0x40 | sync time | phone to glasses |
| 0x41 | glasses control | payload `[op][function][args]`, op 1 = read, 2 = write (table below) |
| 0x42 | battery | request payload `00 00`; the reply comes back as a 0x42 frame |
| 0x43 | device info | request payload `00 00` |
| 0x44 | AI voice setting | read `01 00`, set `02 xx` |
| 0x45 | heartbeat | phone to glasses |
| 0x46 / 0x47 | wear check / wear-function support | |
| 0x48 | voice status / play prompt | |
| 0x49 | BT connect (`02 01`) | probably brings up the Classic Bluetooth link; unverified |
| 0x4A | camera status | read `01`; the app reads it before photo, video and recording |
| 0x4B to 0x57 | shorthand, head angle, screen, inscription, push message, volume, speak-sound switch, gyro, switch status, AI shortcut, GPT chat | settings for other glasses models in the family; mostly not relevant to this unit |
| **0x59** | **GPT upload** | the voice stream from the glasses (matches capture) |
| 0x5C to 0x5E | other GPT messages | |
| **0x73** | **device data reporting** | the events from the glasses (matches capture) |
| 0xF2 | factory test | denied |
| 0xFC | update over the SoC (OTA) | denied |
| 0xFD | picture thumbnails | the app asks for a thumbnail after an image-recognition capture |

### glassesControl (0x41) payloads seen in the app
| Payload | Meaning (from the calling method) |
|---|---|
| `02 01 01` | take photo |
| `02 01 02` | start video |
| `02 01 06 s s` | photo for AI vision, `s` = thumbnail size |
| `02 01 08` | start audio-only recording |
| `02 01 04 01` | **start Wi-Fi Direct mode** (sent with peer discovery when the Album import starts) |
| `02 01 04 02` | start access-point mode (the fallback import) |
| `02 01 0F` | reset the glasses' Wi-Fi Direct state (retry path) |
| `02 01 0B` | used by the speech recognizer; meaning unknown |
| `02 01 0A` | **factory reset (denied)** |
| `02 01 0E` | **restart (denied)** |
| `01 0A`, `01 02`, `01 06`, `01 08 xx` | settings reads |
| `02 08 dir res`, `02 09 01/02`, `02 04` | settings writes (video direction/resolution, others unknown) |

Stop commands for video and audio recording were not found; the same command may toggle, or recording is stopped with the button. Unverified.

### 0x73 event types (first payload byte)
| Type | Meaning | Matches capture |
|---|---|---|
| 1 | media counts: bytes 1-2 photos, 3-4 videos, 5-6 audio (INFERRED which is which), byte 7 file-list type, byte 8 = 1 if only access-point import is supported | Yes: `01 07 ...` after the 7th photo; the video count reached 2 after two recordings |
| 2 | AI image-recognition capture ready; the app then fetches a thumbnail | Yes: rear double click, `02 00 NN 02 00` |
| 3 | AI voice start (byte 1 = 1) | Yes: `03 01` precedes the 0x59 burst |
| 0x0A | AI voice / recognition ended | Yes: `0a 01` ends the burst |
| 4 | glasses ask the app to open the firmware-update screen; **never act on it** | |
| 5 | battery percent (byte 1) and charging flag (byte 2 = 1) | not yet seen |
| 8 | the glasses' IP address, 4 bytes (bytes 1-4) | not yet seen |
| 9 | error, bytes 1-2 (`ff 02` makes the app reset the P2P state) | |
| 0x0B | temperature in degrees C (byte 1) | Yes: 42 to 52 every 3 s while recording |
| 0x0C | user interrupts the spoken answer | |
| 0x0D | unbind request | |
| 0x12 | volume settings report, groups of `[id, min, max, current]` | Yes: current value 15 to 16 after a volume swipe |
| 0x14 / 0x16 / 0x17 / 0x18 / 0x19 | working state, gimbal angle (x90), real-time translation, transcription, teleprompter (byte 1 = 1 starts) | |

### Wi-Fi: questions (a) and (b)
- **(a) What makes the glasses available:** the app writes `02 01 04 01` over BLE and, at the same moment, starts Wi-Fi Direct peer discovery on the phone
  (consistent with the observed discovery at 10:46:41; the BLE write itself was not captured). `02 01 04 02` is the access-point alternative. The glasses report
  in event type 1 whether only access-point import is supported.
- **Pairing:** the app picks the peer whose name equals the stored glasses Wi-Fi name, or ends with the Bluetooth address without colons (observed
  `AIMB-G2_D04B1BC32324`), then connects with push-button setup and no explicit group-owner intent. Discovery times out after 16 s (one retry after `02 01 0F`),
  the connect after 15 s (one retry). In our observed session the phone became group owner.
- **(b) IP and media protocol:** the glasses report their IP over BLE as event type 8 once the link is up (when the phone is not the group owner, the app uses the
  group owner's address). Media is served over **plain HTTP on port 80**, no authentication seen: first `GET /files/media.config` (a text list of names), then
  `GET /files/<name>`. If the unit reports file-list type 2 instead, the list is `GET /storage/sd0/C/DCIM/1/vf_list.txt` (JSON with `file_list[].f` paths) and files are at
  `/<f>`. Logs are listed by `/files/log/log.list`. Our unit reports file-list type 1.
- **Access-point mode:** the SSID is the same name (device name plus the address without colons). The app has a fixed default password for it; the value is deliberately not
  recorded here. Using it on your own glasses is your decision; it is visible in the decompiled source (`MyBluetoothReceiver`).
- Live preview uses the same Wi-Fi link with a media player; its URL has not been read yet.

### Voice stream and cloud
- The 0x59 frames are the audio the glasses send while the AI voice is active. The app bundles Opus and Speex decoders and feeds speech recognition (Azure SDK) and a
  chat backend. Only hostnames/IPs and the usage pattern are recorded; nothing is contacted by this project.

### Byte-level denylist (implemented in `protocol/denylist.py`, beats any approval)
Commands 0x01 to 0x05 (firmware update), 0x25, 0x26, 0x30 to 0x35 and 0x80 to 0x82 (file transfers), 0xF2 (factory test), 0xFC (OTA via SoC), and control payloads starting
`02 01 0A` (factory reset) or `02 01 0E` (restart). Commands 0x4A and 0x54 are also used by file code with another meaning, so they are not denied; the allowlist covers them.

### Corrections to earlier notes
- `writeIpToSoc` belongs to the OTA screen (cmd 0xFC), not the media IP. The earlier guess is withdrawn.
- The "counter" in photo and recording-stop events is the media count; the 3 s "heartbeat" is the temperature; `02 00 NN 02 00` is an AI-image capture; `03 01` and `0a 01` are AI voice start and end;
  the 14-byte events are volume reports (so the touch pad is visible over BLE after all, at least for volume).
- The glasses support both Wi-Fi Direct and an access-point mode; the SoftAP idea was partly right.

### How each will be confirmed
Read-only first: send the battery and device-info requests (replies expected as 0x42 and 0x43 frames). Then, only with your approval of the exact bytes: camera status read, photo, and
`02 01 04 01` followed by watching for event type 8 and the Wi-Fi Direct peer on this PC. See `commands.yaml` for the exact frames (all INFERRED, none sendable yet).
