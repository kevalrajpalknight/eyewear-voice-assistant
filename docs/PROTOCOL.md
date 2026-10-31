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

## Voice stream, cmd 0x59 (format matches the app exactly; audibility not yet confirmed)

- CONFIRMED (capture): payload is 40 bytes; a burst is 200 to 289 frames at 20 ms spacing (about 50 per second), bracketed by `0x73` markers `03 01` (start) and `0a 01` (end).
- INFERRED (app code): 0x59 is the app's "GPT upload", the AI voice audio. The app decodes it with no Ogg header, 16 kHz, mono and 40-byte packets, feeding the decoder the bytes
  after the 6-byte frame header, which is exactly our payload.
- CONFIRMED (our decode): those exact settings decode 289 of 289 packets of the 19:04 burst with no errors (20 ms per packet; FFmpeg's libopus outputs 48 kHz and we resample to the
  app's 16 kHz). Wrong offsets fail (11 to 149 errors out of 289, or clipped noise); Speex fails to open; SBC/mSBC is excluded (0 of 289 packets start with 0x9C/0xAD).
  Packet start `4b 41` is a valid Opus TOC pair (SILK wideband 20 ms, padded frames).
- The decoded signal is very quiet: rms about 17 and peak 368 out of 32767 (about -52 dBFS rms), like the Windows microphone recording.
- UNVERIFIED: whether it contains intelligible speech. Listen to
  `logs/decode_candidates/burst2_opus_strip2_app_format_16000Hz_mono_PEAK-NORMALIZED.wav` (and the unnormalised file). Not a success until a human hears speech.

## Events, cmd 0x73 (payload after the CRC; meanings from the app's code, matching our captures)

| Payload seen | Meaning | Status |
|---|---|---|
| `03 01` | AI voice start | INFERRED (app code; precedes every burst) |
| `0a 01` | AI voice / recognition ended | INFERRED (app code; follows every burst) |
| `01 07 00 00 00 00 00 01` | media counts after photo number 7: photos 7, videos 0, audio recordings 0, file-list type 1 | INFERRED (the app's own field names are image, video and record counts) |
| `01 08 00 02 00 00 00 01` | counts at stop-recording: 8 photos, 2 videos | INFERRED, matches capture |
| `02 00 NN 02 00` | image-recognition capture ready; the app then fetches a thumbnail. NN not decoded | INFERRED |
| `0b TT` | temperature in degrees C, sent every 3 s while recording (0x2a to 0x34 = 42 to 52) | INFERRED, matches capture |
| `12 ...` (14 bytes) | volume settings report; the current value went 15 to 16 after a volume swipe | INFERRED, matches capture |

The touch pad is visible over BLE (at least for volume). Earlier notes that said it sends nothing were wrong, and the earlier readings of the counter, the "heartbeat" and the
marker events are replaced by the table above. The full event-type list is under "APK findings".

## Photo/recording timing (CONFIRMED, labelled run at 19:03 UTC)

| Action | Delay to event |
|---|---|
| Front click (photo) | 4.3 s |
| Rear click (AI assistant) | 1.8 s to burst start; burst 5.8 s |
| Rear double click (image recognition) | 4.6 s |
| Front double click (start recording) | temperature events start (about 11 s later in one run; the mark may have been typed early) |
| Front click (stop recording) | media-count event with the next 3 s tick, then no more temperature events |

## Commands (writes)

No write has been captured or confirmed. Every known command comes from reading the app (INFERRED, see "APK findings" and `commands.yaml`). Three read-only requests
(battery, device info, camera status) are approved for one-shot testing with `aiglasses probe`; results are recorded below once run.

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
| `02 04` | read the media counts (the Album's request, also sent after connecting; reply type 4) |
| `02 08 dir res`, `02 09 01/02` | settings writes (video direction/resolution, others unknown) |

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
- **Access-point mode:** the SSID is the same name (device name plus the address without colons). It uses a fixed default password built into the app and is not recommended (see below).
- Live preview uses the same Wi-Fi link with a media player; its URL has not been read yet.

### Voice stream and cloud
- The 0x59 frames are the audio the glasses send while the AI voice is active. The app bundles Opus and Speex decoders and feeds speech recognition (Azure SDK) and a
  chat backend. Only hostnames/IPs and the usage pattern are recorded; nothing is contacted by this project.

### Byte-level denylist (implemented in `protocol/denylist.py`, beats any approval)
Commands 0x01 to 0x05 (firmware update), 0x25, 0x26, 0x30 to 0x35 and 0x80 to 0x82 (file transfers), 0xF2 (factory test), 0xFC (OTA via SoC), and control payloads starting
`02 01 0A` (factory reset) or `02 01 0E` (restart). Commands 0x4A and 0x54 are also used by file code with another meaning, so they are not denied; the allowlist covers them.

### Replies to our requests (INFERRED, from how the app parses them)
- **Battery (0x42):** payload `[percent 0 to 100][charging 1 or 0]`.
- **Device info (0x43):** payload `[1 byte][len16 firmware][len16 hardware][len16 Wi-Fi firmware][len16 Wi-Fi hardware]` followed by the four UTF-8 strings. The `wifi: 2512271030`
  line on the app's home screen is NOT in this reply on our unit (the two Wi-Fi strings were empty); its source is an open question.
- **Camera status (0x4A):** payload `[1 byte][1 = earphone camera open][2 = rotation open]`. It concerns an earphone-camera feature; it is not a recording-state flag.
- **Control (0x41) reply:** type 1 is `[function id][error code][activity byte]`; type 4 is the media counts (photos, videos, audio, file-list type, "only access-point import" flag);
  type 3 carries an IP (the app parses it oddly, so use the 0x73 type 8 event instead).
- **What the app does with error code and activity byte (INFERRED, from its message texts):** error codes 0 and 255 are handled identically (both go on to read the activity byte); any other
  code gives a generic failure message. Activity values and the app's wording: 1 "photo mode", 2 "video recording", 4 "transfer mode", 5 "firmware update mode", 7 "AI conversation mode",
  8 "recording mode" (audio) - each shown as "in this mode, end it before importing". Activity 255 is skipped in that list and, for the video command, falls through to the plain
  "device start recording" message. So `ff ff` most plausibly means "accepted, nothing else running". It is NOT "busy". The app's photo handler ignores the reply contents completely, so there is
  no photo-specific evidence. All of this is inferred from other handlers and unconfirmed.

### Stopping recordings (INFERRED; corrected 2026-10-31)
- No separate stop command was found, but the app's handling of the video command suggests the SAME command toggles: when the reply's activity byte is 2 (video recording), the app
  announces "device end recording", otherwise "device start recording". So sending `02 01 02` while recording may end it. This reverses the earlier note that the command is not a toggle.
  Unverified. Audio-only recording (`02 01 08`) is similar by name only.
- Rule unchanged: no recording command is sent unless the user is present and ready to stop the recording with a single click on the front button.
- `02 01 09` is what the app sends right after a download finishes, just before it drops the Wi-Fi Direct group: probably "end the Wi-Fi/album session", but the glasses probably also
  clear imported files then, so it is denylisted (see "Deleting files on the glasses").

### Voice codec settings used by the app (INFERRED, match our decode)
No Ogg header, 16 kHz, mono, 40-byte packets; input is the frame payload (bytes after the 6-byte header). See "Voice stream" above.

### Access-point mode is not recommended
Access-point mode (`02 01 04 02`) relies on a fixed default Wi-Fi password built into the app, so it is weak and is **not** recommended. Wi-Fi Direct (push-button setup, no shared password) is
preferred. The password is deliberately not recorded anywhere in this repository. If access-point mode is ever needed, the user supplies the password through a gitignored local config; it must
never be committed, logged or placed in tests.

### Corrections to earlier notes
- `writeIpToSoc` belongs to the OTA screen (cmd 0xFC), not the media IP. The earlier guess is withdrawn.
- The "counter" in photo and recording-stop events is the media count; the 3 s "heartbeat" is the temperature; `02 00 NN 02 00` is an AI-image capture; `03 01` and `0a 01` are AI voice start and end;
  the 14-byte events are volume reports (so the touch pad is visible over BLE after all, at least for volume).
- The glasses support both Wi-Fi Direct and an access-point mode; the SoftAP idea was partly right.

### How each will be confirmed
Read-only first: send the battery and device-info requests (replies expected as 0x42 and 0x43 frames). Then, only with your approval of the exact bytes: camera status read, photo, and
`02 01 04 01` followed by watching for event type 8 and the Wi-Fi Direct peer on this PC. See `commands.yaml` for the exact frames (all INFERRED, none sendable yet).


## Probe results (`aiglasses probe`; times are UTC and IST, IST = UTC+05:30)

### battery_read, `bc42020001b00000` (CONFIRMED)
Three runs on 2026-10-31. Every reply was `bc4202002b706400`: command 0x42, payload `64 00`, which is **100 percent, not charging** (the user confirmed this matches the glasses).

| Run | Connected | Write sent | Reply | Write to reply | Disconnect logged |
|---|---|---|---|---|---|
| 1 | 06:27:45.378Z / 11:57:45.378 IST | 06:27:45.429Z | 06:27:45.547Z | 118 ms | 06:27:46.563Z / 11:57:46.563 IST |
| 2 | 06:28:14.749Z / 11:58:14.749 IST | 06:28:14.781Z | 06:28:14.812Z | 31 ms | 06:28:15.815Z / 11:58:15.815 IST |
| 3 | 06:40:14.162Z / 12:10:14.162 IST | 06:40:14.207Z | 06:40:14.254Z | 47 ms | 06:40:15.264Z / 12:10:15.264 IST |

The "reply in 1.1 s" shown by the first version of the tool included its 1 s wait after the reply; the real write-to-reply time is 30 to 120 ms. Fixed.
Promoted to CONFIRMED in `commands.yaml`. It is read-only and is not to be repeated.

### What "link lost" meant (CONFIRMED from the logs)
- In all three runs the "link lost" warning came 1 to 2 ms after the probe wrote its audit record, which is the end of the run, 1.0 s after the reply (the tool waits 1 s after a
  reply to catch follow-up events), and then disconnects. The line was our **own deliberate disconnect**, logged under a misleading name. It is not a drop, and it did not happen before the write.
- Order of events in each run: connect, notifications enabled, write, reply, 1 s wait, audit written, disconnect callback. Notifications and the write target were valid throughout: the
  reply came back on the notification characteristic after the write.
- Fixed: a requested disconnect is now logged at INFO as "disconnected (we asked for it)"; an unexpected one is a WARNING "link lost (the glasses dropped the connection)".
  The probe and the write guard now also check that the link is up right before a write and refuse to write if it is not; the guard never sends the same payload twice in a session.

### Media counts frame seen right after connecting (INFERRED field meanings)
`bc73080054070104000000010001` at 06:40:14.232Z / 12:10:14.232 IST, 25 ms after our write: payload `01 04 00 00 00 01 00 01`. With the app's own layout: photos 4, videos 0, audio recordings 1,
file-list type 1 (no "only access-point import" byte, treated as false). The app would show 5 files to import (4 + 0 + 1). The glasses send these counts on connect.

### Glasses need time to advertise again
After a session ends the glasses may not advertise for a while. Observed: the first connect attempt of a run often reports "not found" and a later attempt finds them; waits of roughly 15 to 40 s
were seen. The tool scans up to 15 s per attempt with up to 4 attempts, so it waits well past 10 s before giving up, and logs the early attempts at INFO. Wait about 30 s between runs.


### Which media count is which (INFERRED; the photo field is confirmed only by the photo test)
Fields in order: 1 (bytes 1-2), 2 (bytes 3-4), 3 (bytes 5-6), each a 16-bit little-endian number. Evidence:
- **The app's names.** The reply to the Album's count request (`02 04`, below) is parsed into three consecutive fields called `imageCount`, `videoCount` and `recordCount`. The Album screen only
  shows their sum ("files to import"), so the app itself never separates them in the UI.
- **What "record" means in the app.** Everywhere in the app "record" is the audio-only recording: the audio-only quick function is `startRecord` (`02 01 08`), the file handler for it is
  `RecordHandle`, and the related duration field is `recordAudioDuration`. Video is `startVideo` (`02 01 02`). So field 3 is audio-only recordings, not video.
- **Our own capture.** At 19:11 UTC (00:41 IST) the stop-recording event was `01 08 00 02 00 00 00 01`: field 1 = 8, **field 2 = 2, field 3 = 0**, after two recordings started with the front button
  (double click, 19:04 and 19:11). So a button-started recording incremented field 2 (video), not field 3. Field 1 had also gone up by one at the AI image capture, so photos include AI image captures.
- **Now:** photos 4, videos 0, audio recordings 1. The earlier counts (8 photos, 2 videos) are gone, so the glasses' files were cleared in between (probably by the phone's Album import at 10:46 IST;
  the app's deletion step was not read). Whether the 1 audio recording came from the rear-button long press or the app's "Recording" cannot be told from the counts alone.
- `02 04` (cmd 0x41, write op 2, function 4) is the app's **read media counts** request; the app also sends it right after connecting. The glasses sent the same counts unasked on connect in our runs.

### The glasses sleep or power off when idle
They stop advertising when idle or after a session. Wake them (or take them out of the case) and start a probe straight away. If they are not advertising after the retries, the tool stops
without scanning further and says what to do: wake or power on, phone Bluetooth off, Cyan app closed.


### device_info_read, `bc43020001b00000` (CONFIRMED)
Run at 07:05:05.868Z / 12:35:05.868 IST (+05:30). Connected at 07:05:05, link up immediately before the write, one write, reply 0.05 s later, then our own disconnect.
Reply: `bc4329002d9c0015000b0000000000414d303147315f392e34302e30305f323630313134414d303147315f56392e34` (cmd 0x43, 41-byte payload, CRC ok, no bytes left over):
- string lengths (16-bit little-endian): firmware 21, hardware 11, Wi-Fi firmware 0, Wi-Fi hardware 0;
- firmware `AM01G1_9.40.00_260114` and hardware `AM01G1_V9.4`, **identical to the GATT Device Information values** read earlier, so the layout read from the app is right;
- the two Wi-Fi strings are **empty**. So the `wifi: 2512271030` line on the app's home screen does not come from this reply; its source is still unknown (an earlier guess that it is the Wi-Fi firmware
  version is withdrawn).
- No connect-time media-count frame arrived in this run (it was seen in only one of the four runs so far), so the photo test cannot rely on it. The probe now accepts `--known-photos N` to use
  a count seen earlier (last seen: 4 photos at 12:10:14 IST) and says which source it used.
Promoted to CONFIRMED in `commands.yaml`.


### Camera status (0x4A, payload `01`): not sent, left INFERRED (decision 2026-10-31)
Evidence from the decompiled app (names only):
- **Query, not a setter (INFERRED).** `LargeDataHandler.earphoneCameraStatus` sends `[01]` and waits for the reply; the matching setter `earphoneCameraStatusSetting` sends `[02 xx yy]`. This follows the
  app's convention that a first byte of 1 is a read and 2 is a write. No other use of the `[01]` form was found.
- **Reply fields.** `EarphoneCameraStatusRsp` has two flags: "open" (byte 1 equals 1) and "rotation open" (byte 2 equals 2). They describe an earphone-camera feature, not recording or busy state.
- **When the app uses it.** Callers (AI chat view model, the main and home screens, the settings screen) query it before taking a photo, video or audio recording only when the app's "earphone" flag is set,
  which comes from the device's wear-function-support reply (0x47). For other devices the app sends the camera command directly. Our unit's flag has not been read.
- **Where busy state really comes from.** The reply to the camera command itself carries an error code (255 = busy) and which activity the glasses are busy with (see "Replies to our requests").
- **Verdict.** Probably a pure query, but it would not tell us whether the glasses are busy or recording, so it is not useful for the photo test. It is removed from the approved probes and stays INFERRED and not
  sendable in `commands.yaml`. If we ever want to know whether this unit is an "earphone" model, the read is the wear-function-support request (0x47, `01 00`), which would need its own approval.

## Open questions (low priority)
- Where does `wifi: 2512271030` on the app's home screen come from? It is not in the device-info reply (the Wi-Fi strings there are empty). Candidates: another request or an event (not read yet), or a
  value the app builds itself. Parked until we have a reason to chase it.

### Photo probe result (2026-10-31): `bc4103001050020101` (CONFIRMED reaction; exactly +1 per command is OPEN)
Run at 07:10:43.233Z / 12:40:43.233 IST (+05:30), written once, no retry, link and notifications verified first. Baseline source: an earlier frame (4 photos at 12:10:14 IST; no connect-time frame arrived).
Everything that arrived (decoded and raw):
- 07:10:43.323Z, 0.09 s after the write: `bc4105000c4c020101ffff`, control reply type 1, function 1, error code 255, activity byte 255 (see the corrected reading above; not "busy").
- 07:10:45.250Z / 12:40:45.250 IST, 1.9 s after the write: `bc73080077c70106000000010001`, media counts: photos 6, videos 0, audio recordings 1, file-list type 1.
- 07:10:45.718Z / 12:40:45.718 IST: `bc7303004931056300`, type 5 event: battery 99 percent, not charging (the first real battery event; the layout read from the app matches).
The user heard the shutter (how many sounds was not stated), pressed nothing during the run, and did not intentionally take photos between 12:10 and 12:40 IST. Whether the front button was pressed to wake
the glasses before the earlier runs is not confirmed ("probably").
- CONFIRMED: the command triggers a shutter reaction and a media-count event after it.
- OPEN: "exactly +1 photo per command". The count went 4 to 6 against a 30-minute-old baseline, so one earlier unrecorded photo, or two photos from the command, cannot be told apart.
  A clean test needs a fresh baseline taken just before the command (see "Clean photo test" in docs/PHASE4_PLAN.md).
- INFERRED: the photo field is the first of the three counts.

### Counts can change without any BLE write
A single click on the right front button while the glasses are on takes a photo (per the manual), and AI image captures and the app also add files. So the counts can change between our runs
without a write from this tool. When waking the glasses, avoid clicking the front button while they are already on. Every run now records the counts it sees in `logs/media_counts.jsonl` and prints a
note if the counts seen on connect differ from the last recorded ones.

### Album read (`02 04`), `bc41020001130204` (CONFIRMED as a read, 2026-10-31)
Frame `bc41020001130204` (cmd 0x41, payload `02 04`). Evidence that it is a pure read: the app sends it only inside its read-only connect sync (next to the battery request, the volume-settings read and the
wear-support read) and when the Album tab opens; it carries no arguments; its reply handler only stores the counts, the file-list type and the "only access-point" flag and logs the total. Caveat: its
first byte is 2, the value other commands use for writes, so side effects cannot be excluded from the app alone; the glasses firmware decides. Not approved or sent.


### Deleting files on the glasses (investigated 2026-10-31, INFERRED)
- **No delete from the app.** Every delete in the Cyan app's code is a local phone file or database delete. There is no HTTP DELETE and no call to a BLE delete command.
- **A delete command exists in the SDK but is unused:** cmd 0x39 with payload `01` plus a UTF-8 file name (the SDK's file handler). It is on the byte-level denylist.
- **End of import:** after a download the app sends `02 01 09` and drops the Wi-Fi group. Evidence that the glasses clear imported media: the app text "Insufficient memory, please import resources from the
  APP album and try again", and our counts went from 8 photos and 2 videos (00:41 IST) to 0 videos and fewer photos after the phone's import. It is not known whether `02 01 09` or just ending the
  Wi-Fi session causes it. `02 01 09` is therefore **denylisted** until proven safe and is not part of any rollback.
- Wi-Fi Direct rollback relies on stopping the session on the PC and power-cycling the glasses.

### PC Wi-Fi state before the Wi-Fi Direct step (read-only check, 2026-10-31)
`aiglasses wifi preflight`: connected, 802.11ac, **5 GHz, channel 157**. The glasses are probably 2.4 GHz only, so before Step 2 the PC should be put on a 2.4 GHz network or disconnected from Wi-Fi.
The group owner intent to use on the PC is 15 (maximum), so that the PC is group owner as the phone was.

### Media client rules (enforced in `research/glasses_http.py`)
GET only, only the private IP the glasses reported, paths `/files/<name>` only, no redirects, 5 s connect timeout, size limit, JPEG marker check, saved under `captures/`.

### Clean photo test, reads A and B (run by the user)
- Read A at 07:37:57.212Z / 13:07:57.212 IST and read B at 07:39:33.947Z / 13:09:33.947 IST both returned `bc410b000b290204060000000100010000`: command 0x41, 11-byte payload (CRC ok),
  type 4, **photos 6, videos 0, audio recordings 1**, file-list type 1, "only access-point import" 0. **A equals B**, and both equal the count after the earlier photo run (6, 0, 1 at 12:40:45 IST),
  so nothing changed in the last 29 minutes (no stray photo). The layout matches the app's parser exactly (counts at payload bytes 2 to 7, file-list type at 8, flag at 9).
- One attempt between A and B (13:08:45 IST) found no advertising glasses and was interrupted with Ctrl+C; no write was made in it.
- `media_counts_read` promoted to CONFIRMED (read-only; replied with sane counts twice; no visible effect on the glasses).

### Clean photo test (2026-10-31, run by the user; times IST, +05:30): PASSED
Verified against `logs/write_audit.jsonl` and `logs/media_counts.jsonl`: exactly six writes since 13:00, four album reads, one photo and one more album read.

| Step | Time | Counts (photos / videos / audio) |
|---|---|---|
| Read A | 13:12:03 | 6 / 0 / 2 |
| Read B | 13:13:20 | 6 / 0 / 2 (equal to A, so the photo step went ahead) |
| Photo `bc4103001050020101` | 13:14:56.405 | reply `bc4105000c4c020101ffff` after 0.05 s; count event `bc73080097070107000000020001` 1.9 s later: 7 / 0 / 2 |
| Read C | 13:15:44 | 7 / 0 / 2 |

The user heard **one shutter sound**. Videos and audio were unchanged, so **one photo command added exactly one photo** (6 to 7).
- `photo` is promoted to CONFIRMED (one clean trial; n = 1). The earlier 4 to 6 jump remains unexplained (probably a front-button click while waking the glasses, unproven).
- `media_counts_read` is CONFIRMED (reply twice, then four more times, always consistent).
- The meaning of the reply `02 01 01 ff ff` stays INFERRED ("accepted, nothing else running").
- Before this run an earlier pair of reads (13:07:57 and 13:09:33) gave 6 / 0 / 1; the audio count then rose to 2 by 13:12:03 **without any command from us** (physical input, unconfirmed).

### Always read the counts first
Counts change with physical input as well as BLE commands (a front-button click takes a photo; recordings add audio or video). So the album read (`02 04`) is always done before any state-changing
test, and the previous counts are kept in `logs/media_counts.jsonl`.

