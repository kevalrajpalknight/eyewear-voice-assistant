# Phase 4 plan: clean photo test, Wi-Fi Direct, media download

Each step needs the user's approval of the exact bytes before it runs. Nothing is promoted from INFERRED to CONFIRMED until the glasses reply or show a visible effect.
Everything here is read from the official app's code (see docs/PROTOCOL.md, "APK findings").

## Standing rules

- Cyan app closed and the phone's Bluetooth off before any BLE test (the glasses accept one connection). Do not click the glasses' front button while they are on: that takes a photo.
- The glasses sleep or power off when idle. Wake them first and start the probe straight away. If they are not advertising after the retries, stop (the tool does not keep scanning) and fix the cause:
  wake or power on, phone Bluetooth off, app closed.
- One write at a time per run, no retries or loops, 5 s reply timeout, link checked right before the write, audited in `logs/probe_audit.jsonl` and `logs/write_audit.jsonl`; media counts are
  recorded in `logs/media_counts.jsonl`. The guard never sends the same payload twice in a session.
- The byte-level denylist stays on and beats any approval: firmware update, file transfer, **file delete (0x39)**, factory test, restart, factory reset, update over the SoC, and
  **`02 01 09` (end of import session)**. See "Deletion safety" below.
- Nothing is sent to anything except the glasses and, for HTTP, the IP the glasses report. The app's cloud servers are never contacted.
- No video-start or audio-record-start until the user is present to stop the recording with the front button (the video command may toggle, unverified).
- HTTP rules (enforced in `research/glasses_http.py` and tested): only the IP the glasses reported, and only if it is a private RFC 1918 address; GET is the only method the client can send; paths
  must be `/files/<name>` with a strict character set; redirects are never followed; 5 s connect timeout; response size limit (default 50 MB); photos must start with the JPEG marker `FF D8`;
  saved only under `captures/` (gitignored).

## Step 0: reads (battery and device info done and CONFIRMED; camera status skipped, see PROTOCOL.md)

Done. `camera_status_read` was skipped: it only describes an earphone-camera feature, not busy or recording state.

## Photo (reaction CONFIRMED on 2026-10-31; "exactly +1 per command" is OPEN)

Frame `bc4103001050020101`. The first run gave a shutter, a reply and a count event, but the count went 4 to 6 against a stale baseline, so "+1" is not proven.

## Clean photo test (approved 2026-10-31; each its own run)

1. Album read A with `bc41020001130204` (command 0x41, payload `02 04`, probably a pure read, INFERRED). Wait. Album read B. A and B must match; if they differ, stop and report (no photo).
2. One photo command `bc4103001050020101` with `--known-photos` set to the photo count from read B.
3. Album read C. Expected: C = B + 1 photo. The user says how many shutter sounds were heard.
4. Stop and wait for approval before Step 2.

Every run: wake the glasses first, write once, link check, no retries, report everything that arrives (decoded and raw) and any count change seen on connect.

## Deletion safety (investigated 2026-10-31, INFERRED)

- The app never deletes media on the glasses by BLE command or HTTP: every delete in its code is a local phone file or database delete; there is no HTTP DELETE.
- The BLE SDK does contain a file-delete command (0x39, payload `01` plus a file name) that the app never calls. It is now on the denylist.
- After an import the app sends `02 01 09` and drops the Wi-Fi group. The app's own text says to import resources to free memory on the glasses ("Insufficient memory, please import resources from the
  APP album"), and our counts fell from 8 photos and 2 videos to nothing after the phone's import. So the glasses probably clear imported files, and `02 01 09` is the prime suspect. It is **not
  proven safe**, so it is denylisted and will not be sent. It is also unknown whether merely ending the Wi-Fi session clears files.
- Risk to accept before Step 2: media on the glasses may disappear after an import. Currently 6 photos and 1 audio recording are counted.

## Step 2: Wi-Fi Direct enable plus the PC connection (together; needs approval and the dependency decision in ADR 0006)

- Dependency: `winrt-Windows.Devices.WiFiDirect` and the winrt family must be pinned to versions at least two weeks old. Option B (wait until 2026-11-07, 4.x set, Python 3.11 or newer) is
  recommended; option A pins 3.2.1 and replaces the BLE stack's winrt packages. Nothing is pinned yet.
- Pre-flight (read-only): `aiglasses wifi preflight` reports the PC's band and channel. On 2026-10-31 the PC was on **5 GHz, channel 157, 802.11ac**. The glasses may be 2.4 GHz only (V821 spec;
  our unit unverified) and one adapter usually cannot hold a 5 GHz connection and a 2.4 GHz group at once, so before Step 2 the user either connects the PC to a 2.4 GHz network or disconnects from Wi-Fi.
- Bytes for approval: the enable `bc410400935c02010401` (cmd 0x41, payload `02 01 04 01`). The end-session frame `bc4103001196020109` is **not** part of the pair for now (see Deletion safety).
- PC side: look for the peer named `AIMB-G2_D04B1BC32324`, connect with push-button setup, with the group-owner intent set to the **maximum (15)** so the PC is group owner and the glasses join as client
  (the roles seen with the phone). Report the intent used and the band and channel of the group that forms.
- Before starting: record the current Wi-Fi network (kept locally, not printed) and confirm internet works.
- Expected: the glasses report their IP in a 0x73 type 8 event; a group forms in about 1 to 2 s.
- Stop conditions: no peer within 16 s (the app's timeout), connect not complete within 15 s, any unexpected prompt. At most one retry.
- Rollback: (1) stop the Wi-Fi Direct session on the PC and disconnect the peer; (2) the glasses are not sent any end-session command: power-cycle them to clear the Wi-Fi state;
  (3) restore the user's normal Wi-Fi (reconnect the recorded profile) and verify the network and internet; (4) report what was restored. If Windows cannot hold the group and the normal Wi-Fi
  together, stop and propose a second USB Wi-Fi adapter.

## Step 3: fetch the media list

`GET http://<glasses IP>/files/media.config` on port 80 through the guarded client (INFERRED: the unit reports file-list type 1; if type 2, `/storage/sd0/C/DCIM/1/vf_list.txt`, which would need a
widening of the allowed path and its own approval). Read-only. Saved to `captures/`.

## Step 4: download one photo

`GET http://<glasses IP>/files/<name>` for one photo, JPEG check, size limit, saved to `captures/`. Then run the rollback above.

## Results log

Fill in as steps are run (date, bytes, reply, visible effect, what was promoted to CONFIRMED).
