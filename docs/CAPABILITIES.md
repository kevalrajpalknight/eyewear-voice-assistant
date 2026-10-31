# Capabilities

| Capability | Status | How verified | Known limits |
|---|---|---|---|
| BLE scan | CONFIRMED | `aiglasses scan` on Windows 11 | Glasses stop advertising when idle; toggle their Bluetooth |
| BLE connect | CONFIRMED | `aiglasses info`, MTU 517, uncached services | No pairing needed; phone must be disconnected; link is slow to establish (about 5 s) |
| GATT dump | CONFIRMED | `aiglasses gatt-dump` -> `logs/gatt_dump.json` | 2A05 indicate needs bonding |
| Notifications | CONFIRMED | `aiglasses listen`: 8 of 9 CCCD enables succeed | Frames are `BC cmd len16 crc16 payload` (CRC verified on 756/756); event meanings partly inferred |
| Voice stream decoding | CONFIRMED | The app's exact settings (16 kHz mono, 40-byte Opus packets, 20 ms, no Ogg header): 0 decode errors, and the user heard intelligible speech (2026-10-31) | Very quiet signal; a session lasted 288 frames (5.76 s) and ended with the glasses' `0a 01` marker |
| Device info | CONFIRMED | GATT 0x180A read, and `aiglasses probe device_info_read` (reply gives the same firmware and hardware strings) | Serial empty; Wi-Fi version strings empty in the reply; no standard battery service |
| Battery | CONFIRMED | `aiglasses probe battery_read`: reply `bc4202002b706400` = 100 percent, not charging, 3 runs, user confirmed | Read-only request; not to be repeated needlessly |
| Media counts | CONFIRMED | `aiglasses probe media_counts_read` (reads A, B, C in the clean test): photos, videos and audio recordings; also pushed as a 0x73 type-1 event after a photo | Counts change with physical button clicks too, so always read them before a state-changing test |
| Photo | CONFIRMED | Clean test 2026-10-31: read 6, photo command, read 7; one shutter; reply and count event | One clean trial (n = 1); the earlier 4 to 6 jump is unexplained; the reply `02 01 01 ff ff` is inferred to mean accepted |
| Video | INFERRED | Start command read from the app (`02 01 02`); it may toggle start and stop | Only with the user present to stop it with the front button |
| Media download | INFERRED | App code: plain HTTP on port 80, `/files/media.config` then `/files/<name>` | Needs Wi-Fi Direct up first; plan in docs/PHASE4_PLAN.md |
| Wi-Fi link | INFERRED | Phone bug report: the app connects to the glasses over **Wi-Fi Direct** (phone is group owner, glasses join as client) | Not hotspot/SoftAP as first assumed; trigger and PC-side route untested. This PC's adapter supports Wi-Fi Direct device, group owner and client (read-only check) |
| Audio endpoints | CONFIRMED | `aiglasses audio devices` after Windows pairing | WASAPI: `Headphones (AIMB-G2_2324)` 48 kHz stereo (A2DP), `Headset (AIMB-G2_2324)` 16 kHz mono (HFP) |
| Audio playback | INFERRED | `aiglasses audio play` opened the A2DP stream and finished without error | Audible result not yet confirmed by the user; HFP and A2DP cannot run at the same time |
| Audio record | UNVERIFIED | `aiglasses audio record` recorded 5.0 s at 16 kHz but the signal was silent (peak 0.0006), likely because nobody spoke | Needs a retest while speaking; HFP is low bandwidth |
| Audio-only recording on the glasses ("Recording" in the app) | INFERRED | Start command read from the app (02 01 08) | No stop command exists; only test with the user present to stop it with the front button |
| AI translation | UNVERIFIED | Listed in the app's Function settings | Probably runs in the phone app using the voice stream; needs capture |
| Events (photo counts, temperature, volume, AI voice start/end, battery) | INFERRED | `Glasses.events()` decodes them; unit-tested on one recorded frame per type from our traces; media counts, battery and temperature were seen live | Promote per type after a live run with a known trigger; IP (type 8) not yet seen |
| AI voice WAV (`ai-voice record`) | CONFIRMED | A single rear click gave `03 01`, 288 voice frames, `0a 01`; the WAV is intelligible | A long press starts an audio-only recording instead |
| Utterance stream (`Glasses.voice_requests()`) | INFERRED | Unit-tested with synthetic frames (early frames, missing marker, timeout, disconnect, restart) | Not yet run live |
| Wi-Fi Direct (PC side) | UNVERIFIED | Interface, fake, preflight checks and rollback are written and unit-tested | No real radio code; winrt unpinned until 2026-11-07; Step 2 not approved |
