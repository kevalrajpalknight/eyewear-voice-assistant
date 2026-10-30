# Capabilities

| Capability | Status | How verified | Known limits |
|---|---|---|---|
| BLE scan | CONFIRMED | `aiglasses scan` on Windows 11 | Glasses stop advertising when idle; toggle their Bluetooth |
| BLE connect | CONFIRMED | `aiglasses info`, MTU 517, uncached services | No pairing needed; phone must be disconnected; link is slow to establish (about 5 s) |
| GATT dump | CONFIRMED | `aiglasses gatt-dump` -> `logs/gatt_dump.json` | 2A05 indicate needs bonding |
| Notifications | CONFIRMED | `aiglasses listen`: 8 of 9 CCCD enables succeed | Frames follow an inferred `BC cmd len16 payload trailer2` layout; contents not decoded |
| Device info | CONFIRMED | 0x180A read: hardware, firmware | Serial empty; no battery service |
| Battery | UNVERIFIED | | Needs captured command |
| Photo / video | UNVERIFIED | | Needs capture (Phase 3) |
| Media download | UNVERIFIED | | Needs Wi-Fi hotspot discovery (Phase 4) |
| Wi-Fi hotspot | UNVERIFIED | | |
| Audio endpoints | CONFIRMED | `aiglasses audio devices` after Windows pairing | WASAPI: `Headphones (AIMB-G2_2324)` 48 kHz stereo (A2DP), `Headset (AIMB-G2_2324)` 16 kHz mono (HFP) |
| Audio playback | INFERRED | `aiglasses audio play` opened the A2DP stream and finished without error | Audible result not yet confirmed by the user; HFP and A2DP cannot run at the same time |
| Audio record | UNVERIFIED | `aiglasses audio record` recorded 5.0 s at 16 kHz but the signal was silent (peak 0.0006), likely because nobody spoke | Needs a retest while speaking; HFP is low bandwidth |
| Events (buttons, touch, wear) | UNVERIFIED | | Phase 5 |
