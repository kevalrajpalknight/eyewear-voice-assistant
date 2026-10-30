# Protocol notes

Every claim is labelled CONFIRMED (observed on this unit) or INFERRED/UNVERIFIED. Source: prototype scans run against unit AIMB-G2_2324.

## Advertising

- CONFIRMED: name `AIMB-G2_<last 2 MAC bytes>`; MAC `D0:4B:1B:C3:23:24`.
- CONFIRMED: no service UUIDs advertised; service data `0x3802` = own MAC bytes; manufacturer ID `0x1234`, empty payload.
- CONFIRMED: the unit stops advertising after a while; toggling glasses Bluetooth makes it advertise again.
- CONFIRMED: Windows also lists it as a Classic "Audio" device (not yet paired or tested).

## GATT (CONFIRMED, MTU 517, connects without pairing)

| Service | Characteristics (handle: properties) |
|---|---|
| 0x1800 Generic Access | 2A00 (2: read, write) |
| 0xAE30 | AE01 (5: write-no-resp), AE02 (7: notify), AE03 (10: write-no-resp), AE04 (12: notify), AE05 (15: indicate), AE10 (18: read, write) |
| 0x3802 | 4A02 (49: read, notify, write) |
| 0xAE3A | AE3B (65: write-no-resp), AE3C (67: notify) |
| 0x1801 Generic Attribute | 2A05 (71: indicate; subscribe refused "Access Denied", likely needs bonding) |
| Nordic UART `6e40fff0-b5a3-f393-e0a9-e50e24dcca9e` | 6e400002 (135: write, write-no-resp), 6e400003 (137: notify) |
| Vendor `de5bf728-d711-4e47-af26-65e3012a5dc7` | de5bf72a (141: write, write-no-resp), de5bf729 (143: notify) |
| 0x180A Device Information | 2A25 serial (empty), 2A27 hardware `AM01G1_V9.4`, 2A26 firmware `AM01G1_9.40.00_260114`, 2A23 System ID |
| 0xFEE1 | FEE3 (160: read, write, notify) |

- CONFIRMED: no Battery service (0x180F).
- AE00/RCSP hypothesis: PARTIALLY holds. A Jieli-style AE service exists but as 0xAE30 (and 0xAE3A), not 0xAE00. RCSP framing is UNVERIFIED (no frame observed).

## Notifications

- CONFIRMED: one notification seen on `de5bf729` (h143) right after subscribing: `bc7303005231054700`. Does not start with `FE EF AA 55`. Meaning INFERRED only (possibly an unsolicited status message).
- UNVERIFIED: button/touch/wake-word traffic (first 60 s listen was probably done without input).

## Frame format and commands

None known. `protocol/commands.yaml` is empty until Phase 3 capture.
