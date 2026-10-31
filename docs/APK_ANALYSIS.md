# Phase 3b: static analysis of the Cyan Glasses APK

Goal: form hypotheses about the glasses protocol from the official app, then confirm each one on the device. Nothing found here is
trusted until it is observed on the glasses.

## Rules

- The APK and everything extracted from it stay local in a gitignored folder (`apk/`, `apk-analysis/`, `jadx-out/`). Never commit them.
- Do **not** copy or paste decompiled code or assets into the repository or the docs. Record only our own observations: class names, string
  constants, opcode numbers, call order, and what we conclude from them.
- Every finding is marked **INFERRED** until it is confirmed on the device (or in a capture). Confirmed findings move to `docs/PROTOCOL.md`.
- Never run the app or its code. Read only.
- OTA, firmware and factory-reset opcodes are recorded **only** to build the byte-level denylist in `safety.py`; none of them is ever sent.
- Cloud endpoints: record host names and how they are used. Do not contact or probe them.
- No account data, tokens, keys or personal data from the APK or the phone go into the repository.

## Search targets, in priority order

Focus first:

1. **(a)** Which BLE command makes the glasses discoverable for Wi-Fi Direct, and what the app does just before calling `WifiP2pManager.discoverPeers`.
2. **(b)** How the app finds the glasses' IP address after the group forms, and which protocol serves the media (HTTP, FTP, RTSP, custom).
3. **(c)** What the `0x73` and `0x59` notification families mean (events, counters, the voice stream, the 3 second heartbeat).

Then:

- Jieli SDK packages (`com.jieli.*`) and how they frame commands (compare with the observed `BC | cmd | len | crc16 | payload`).
- Strings `AM01G1`, `AIMB-G2_`, and the `wifi: 2512271030` style version field.
- Wi-Fi Direct usage (`WifiP2pManager`, `WifiP2pConfig`, group owner intent, PBC, what the app expects from the glasses).
- Photo, video, audio-only recording and smart image recognition commands; device info and battery.
- Jieli OTA, upgrade and reset opcodes (for the denylist only).

## Status (2026-10-31)

A decompiled source tree (base.apk_Decompiler.com, gitignored) was provided and used; androguard and Java were not needed. Findings are in docs/PROTOCOL.md under APK findings.

## Tools

No Java, jadx, apktool or androguard is installed on this PC. Options once the APK path is available:

- `androguard` (pure Python, no Java): list classes, strings and cross-references. Would be a `research` extra with an ADR.
- `jadx` (needs a Java runtime): readable decompilation for browsing. Optional.

## Findings log

Add findings to `docs/PROTOCOL.md` under a heading "APK findings (INFERRED)", each with what was observed in the app and how it will be confirmed.
