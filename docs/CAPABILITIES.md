# Capabilities

| Capability | Status | How verified | Known limits |
|---|---|---|---|
| BLE scan | CONFIRMED | `aiglasses scan` on Windows 11 | Glasses stop advertising when idle; toggle their Bluetooth |
| BLE connect | CONFIRMED | `aiglasses info`, MTU 517, uncached services | No pairing needed; phone must be disconnected; link is slow to establish (about 5 s) |
| GATT dump | CONFIRMED | `aiglasses gatt-dump` -> `logs/gatt_dump.json` | 2A05 indicate needs bonding |
| Notifications | CONFIRMED | `aiglasses listen`: 8 of 9 CCCD enables succeed | Content not decoded; one packet seen so far |
| Device info | CONFIRMED | 0x180A read: hardware, firmware | Serial empty; no battery service |
| Battery | UNVERIFIED | | Needs captured command |
| Photo / video | UNVERIFIED | | Needs capture (Phase 3) |
| Media download | UNVERIFIED | | Needs Wi-Fi hotspot discovery (Phase 4) |
| Wi-Fi hotspot | UNVERIFIED | | |
| Audio playback / record | UNVERIFIED | | Windows shows glasses as "Audio"; not paired or tested (Phase 2) |
| Events (buttons, touch, wear) | UNVERIFIED | | Phase 5 |
