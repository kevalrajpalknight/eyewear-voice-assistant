# Wi-Fi Direct helper and photo retrieval (N5) - built with fakes, NOT run on hardware

## What exists
- `tools/wifidirect-env/` (gitignored): its own Python 3.12 environment holding only the winrt 3.2.1
  generation (see ADR 0011). The main environment keeps winrt 4.x for the BLE stack; nothing there
  changed (`uv pip list` still shows `winrt-runtime 4.0.3`).
- `src/aiglasses/helpers/wifidirect_helper.py`: the helper process (standard library + winrt, imports
  nothing from aiglasses). JSON lines on stdin/stdout. `--fake` runs a scripted backend with no
  radio; the real backend (winrt radio calls, `netsh` for the Wi-Fi profile) is UNVERIFIED.
- `services/photo_fetch.py`: `PhotoFetcher` (the flow below), `HelperWifiDirect` (talks to the helper),
  `SubprocessHelper`, `WifiDirectImageSource` (what `describe_scene` uses), all tested with fakes.
- Existing and reused: `WifiDirectSession` (16 s peer / 15 s connect limits, one retry, rollback on every
  exit path) and `GlassesHttpClient` (GET only, private IP only, no redirects, size limit, JPEG check).

## Helper protocol
| Request | Reply fields |
|---|---|
| `{"id":1,"cmd":"preflight"}` | `profile` (current Wi-Fi profile name), `band` |
| `{"cmd":"find_peer","prefix":"AIMB-G2","timeout":16}` | `peer` (device id) or null |
| `{"cmd":"connect","peer":...,"group_owner_intent":15,"timeout":15}` | `band`, `channel`, `glasses_ip` |
| `{"cmd":"status"}` | `internet`, `connected` |
| `{"cmd":"disconnect"}` / `{"cmd":"restore","profile":...}` | `{}` |
Errors: `{"id":n,"ok":false,"error":"short text"}`. When the parent exits, the helper leaves the group.

## Flow (`PhotoFetcher.fetch_latest`)
1. take the photo (the tool layer has already checked that you asked) and require the media count to rise;
2. enable discoverability: **a BLE write, not enabled** (`enable_not_approved` raises, so everything
   stops here with "I can't fetch the photo from the glasses yet");
3. join the glasses' peer through the helper, record your Wi-Fi profile first;
4. get the glasses' IP (0x73 type 8 event) and GET `/files/media.config`, pick the
   newest `.jpg` (INFERRED: names sort by time), GET it, check the JPEG marker, save under `captures/photos`;
5. leave the group, restore your Wi-Fi profile, verify internet. On any failure the same cleanup runs
   and a short line is spoken ("I couldn't connect to the glasses", "Your Wi-Fi may need reconnecting").
While the group is up the PC may have no internet, so nothing calls the cloud until restore.

## Supervised runs (approved 2026-11-01)

Approved exact bytes, nothing else: `bc410400935c02010401` (command 0x41, payload `02 01 04 01`), write
without response to `de5bf72a-d711-4e47-af26-65e3012a5dc7`. Sent once per run, never retried, only inside
these commands (`enable_wifi_direct(approved_frame=...)`). `wifi_direct_on` stays INFERRED and
`WIFI_DIRECT_ENABLED` stays false globally. Never sent: heartbeat, `02 01 09`, `02 01 04 02`, delete/OTA/reset,
recording. If the 0x73 type 8 IP event does not arrive within 15 s the run STOPS, rolls back, and does not
scan the network.

Run one at a time (phone Bluetooth off, Cyan app closed, glasses on and charged):

- A, connect only: `uv run aiglasses wifi-direct connect`
- B, A plus `GET /files/media.config` and one existing photo: `uv run aiglasses wifi-direct existing`
- C, full flow (take_photo, count rise, join, list, newest, restore): `uv run aiglasses wifi-direct full`

The report (names and addresses masked, safe to paste) lists timings per stage, group band and channel,
whether internet stayed up during the group, whether Wi-Fi came back, and errors. Restore records the profile
first, reconnects by name (3 attempts), verifies internet; on failure it prints the manual step (Windows
Settings > Network & internet > Wi-Fi, toggle and pick your network; remove the glasses under Bluetooth &
devices if listed).

## Not verified (real backend, docs review only)
- Everything on real hardware; only `--fake` is tested.
- `FromIdAsync` from an unpackaged desktop Python (docs mention the Proximity capability for UWP apps).
- Whether the unpaired glasses show up via `FindAllAsync` on the AssociationEndpoint selector (Microsoft's
  sample uses a watcher plus custom pairing).
- The pairing flow (push-button, `accept()`), whether a persistent pairing results, and that `unpair_async` cleans it.
- That GroupOwnerIntent 15 makes the PC the group owner.
- Band and channel come only indirectly from `netsh wlan show interfaces` (shared radio).
- `netsh wlan connect name=` restore and State/Profile parsing on this Windows locale.
- "Newest" photo = last name in sorted `media.config` (INFERRED: names carry a timestamp).
- That the IP event arrives over BLE after the enable write.
