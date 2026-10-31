# Capabilities

| Capability | Status | How verified | Known limits |
|---|---|---|---|
| BLE scan | CONFIRMED | `aiglasses scan` on Windows 11 | Glasses stop advertising when idle; toggle their Bluetooth |
| BLE connect | CONFIRMED | `aiglasses info`, MTU 517, uncached services | No pairing needed; phone must be disconnected; link is slow to establish (about 5 s) |
| GATT dump | CONFIRMED | `aiglasses gatt-dump` -> `logs/gatt_dump.json` | 2A05 indicate needs bonding |
| Notifications | CONFIRMED | `aiglasses listen`: 8 of 9 CCCD enables succeed | Frames are `BC cmd len16 crc16 payload` (CRC verified on 756/756); event meanings partly inferred |
| Voice stream decoding | INFERRED | `aiglasses voice candidates`: the 0x59 payloads decode as Opus with 0 errors | Not yet confirmed by listening; very low level |
| Device info | CONFIRMED | 0x180A read: hardware, firmware | Serial empty; no battery service |
| Battery | INFERRED | Command read from the app (0x42, payload 00 00); not yet sent | Needs approval of the exact bytes (docs/PROTOCOL.md) |
| Photo / video | INFERRED | Commands read from the app (02 01 01, 02 01 02); not yet sent | Stop-recording command not found |
| Media download | UNVERIFIED | | Needs Wi-Fi hotspot discovery (Phase 4) |
| Wi-Fi link | INFERRED | Phone bug report: the app connects to the glasses over **Wi-Fi Direct** (phone is group owner, glasses join as client) | Not hotspot/SoftAP as first assumed; trigger and PC-side route untested. This PC's adapter supports Wi-Fi Direct device, group owner and client (read-only check) |
| Audio endpoints | CONFIRMED | `aiglasses audio devices` after Windows pairing | WASAPI: `Headphones (AIMB-G2_2324)` 48 kHz stereo (A2DP), `Headset (AIMB-G2_2324)` 16 kHz mono (HFP) |
| Audio playback | INFERRED | `aiglasses audio play` opened the A2DP stream and finished without error | Audible result not yet confirmed by the user; HFP and A2DP cannot run at the same time |
| Audio record | UNVERIFIED | `aiglasses audio record` recorded 5.0 s at 16 kHz but the signal was silent (peak 0.0006), likely because nobody spoke | Needs a retest while speaking; HFP is low bandwidth |
| Audio-only recording on the glasses ("Recording" in the app) | UNVERIFIED | Listed in the app's Quick Functions | Needs the Phase 3 capture; the glasses may store the file or stream it |
| AI translation | UNVERIFIED | Listed in the app's Function settings | Probably runs in the phone app using the voice stream; needs capture |
| Events (buttons, touch, wear) | UNVERIFIED | | Phase 5 |
