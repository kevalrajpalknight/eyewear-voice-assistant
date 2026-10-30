# Capabilities

| Capability | Status | How verified | Known limits |
|---|---|---|---|
| BLE scan | CONFIRMED | `aiglasses scan` on Windows 11 | Glasses stop advertising when idle; toggle their Bluetooth |
| BLE connect | CONFIRMED | prototype `enumerate.py`, MTU 517 | No pairing needed; phone must be disconnected |
| GATT dump | CONFIRMED | `logs/gatt_dump.json` (prototype) | Phase 1 ports it into the package |
| Notifications | CONFIRMED | prototype `listen.py` (1 packet) | Content not decoded |
| Device info | CONFIRMED | 0x180A read: hardware, firmware | Serial empty; no battery service |
| Battery | UNVERIFIED | | Needs captured command |
| Photo / video | UNVERIFIED | | Needs capture (Phase 3) |
| Media download | UNVERIFIED | | Needs Wi-Fi hotspot discovery (Phase 4) |
| Wi-Fi hotspot | UNVERIFIED | | |
| Audio playback / record | UNVERIFIED | | Windows shows glasses as "Audio"; not paired or tested (Phase 2) |
| Events (buttons, touch, wear) | UNVERIFIED | | Phase 5 |
