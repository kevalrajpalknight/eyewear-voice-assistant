# Phase 4 plan: photo, Wi-Fi Direct, media download

Each step needs the user's approval of the exact bytes before it runs. Nothing is promoted from INFERRED to CONFIRMED until the glasses reply or show a visible effect.
Everything in this plan is read from the official app's code (see docs/PROTOCOL.md, "APK findings").

## Standing rules

- Cyan app closed and the phone's Bluetooth off before any BLE test (the glasses accept one connection).
- The glasses sleep or power off when idle. Wake them first and start the probe straight away. If they are not advertising after the retries, stop (the tool does not keep scanning) and fix the cause: wake or power on, phone Bluetooth off, app closed.
- One write at a time, no retries or loops, 5 s reply timeout, audited in `logs/probe_audit.jsonl` and `logs/write_audit.jsonl`.
- The byte-level denylist stays on. No firmware, file-transfer, factory-test, restart or factory-reset payload is ever sent.
- Nothing is sent to anything except the glasses and, for HTTP, the IP the glasses report. The app's cloud servers are never contacted.
- No video-start or audio-record-start until the user is present to stop the recording with the front button (there is no stop command).

## Step 0: reads (battery and device info done and CONFIRMED; camera status skipped, see PROTOCOL.md)

`aiglasses probe battery_read --send` and `device_info_read` are done. `camera_status_read` was skipped: it only describes an earphone-camera feature, not busy or recording state. Expected: a 0x42 frame with 0 to 100 and a charging flag; a 0x43 frame with four
version strings (firmware `AM01G1_9.40.00_260114` as read over GATT, Wi-Fi firmware probably `2512271030`); a 0x4A frame. Promote a command to CONFIRMED only after a sane reply.

## Step 1: photo

- Bytes for approval: `bc4103001050020101` (cmd 0x41, payload `02 01 01`) to `de5bf72a`, write without response.
- Preconditions: step 0 done; the user is watching the glasses at a harmless scene and does not touch the buttons or the Cyan app. `aiglasses probe photo --send --known-photos 4` asks the watching question first, waits up to 1.5 s for the connect-time media-count frame (if none arrives it uses the last known count and prints that source, it does not abort), checks the link right before the write, writes once, and listens 15 s. The result is reported as-is: the type-1 count event after the write plus what the user saw and heard; no forced verdict.
- Expected: a shutter sound or light on the glasses; a 0x41 reply (type 1, error code 0); a 0x73 media-count event about 4 s later with the photo count up by one. No stop needed.
- Rollback: none needed (the photo stays on the glasses; delete it with the app if wanted).

## Step 2: Wi-Fi Direct enable plus the PC connection (together)

- Bytes for approval: `bc410400935c02010401` (cmd 0x41, payload `02 01 04 01`).
- PC side (needs its own approval and an ADR for the `winrt-Windows.Devices.WiFiDirect` package): look for the peer named `AIMB-G2_D04B1BC32324`, connect with push-button setup,
  and ask to be group owner (the phone was group owner in the observed session and the glasses joined as client).
- Before starting: record the current Wi-Fi network and profile name (`netsh wlan show interfaces`, `netsh wlan show profiles`) and confirm internet works.
- Expected: the glasses report their IP in a 0x73 type 8 event; a group forms in about 1 to 2 s.
- Stop conditions: no peer within 16 s (the app's timeout), connect not complete within 15 s, any unexpected prompt. On failure do not retry more than once.
- Rollback (clean):
  1. Stop the Wi-Fi Direct session on the PC and disconnect the peer.
  2. With separate approval, send `bc4103001196020109` (`02 01 09`, "end Wi-Fi session", INFERRED) so the glasses stop Wi-Fi; otherwise power-cycling the glasses clears it.
  3. Restore the user's normal Wi-Fi: `netsh wlan connect name=<recorded profile>`, then verify the SSID and internet.
  4. Report what was restored. If Windows cannot hold the group and the normal Wi-Fi at once, stop and propose a second USB Wi-Fi adapter.

## Step 3: fetch the media list

`GET http://<glasses IP>/files/media.config` on port 80, 5 s timeout, only to the IP the glasses reported (INFERRED: the unit reports file-list type 1; if it reports type 2 use
`/storage/sd0/C/DCIM/1/vf_list.txt` instead). Read-only. Save to `captures/` (gitignored) and show the list.

## Step 4: download one photo

`GET http://<glasses IP>/files/<name>` for the single photo taken in step 1. Check that it starts with the JPEG marker `FF D8` and that the size matches the response header.
Save to `captures/` (gitignored). Then run the rollback above.

## Results log

Fill in as steps are run (date, bytes, reply, visible effect, what was promoted to CONFIRMED).
