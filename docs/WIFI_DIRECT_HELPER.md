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
4. get the glasses' IP (0x73 type 8 event; not wired yet) and GET `/files/media.config`, pick the
   newest `.jpg` (INFERRED: names sort by time), GET it, check the JPEG marker, save under `captures/photos`;
5. leave the group, restore your Wi-Fi profile, verify internet. On any failure the same cleanup runs
   and a short line is spoken ("I couldn't connect to the glasses", "Your Wi-Fi may need reconnecting").
While the group is up the PC may have no internet, so nothing calls the cloud until restore.

## STOP: what I need from you before the first real connection
1. **Approve this exact BLE write** (INFERRED from the app; `safe_to_send: false` today):
   `bc410400935c02010401` = frame for glassesControl payload `02 01 04 01` on characteristic
   `de5bf72a-d711-4e47-af26-65e3012a5dc7`, write without response. Expected reaction: the glasses start
   Wi-Fi Direct and appear as a peer named `AIMB-G2_<address>`; a 0x73 type 8 event with their IP
   follows once joined. Not `02 01 09` (never), not `02 01 04 02` (access-point mode).
2. **Preflight on your PC** (read-only): `tools\wifidirect-env\Scripts\python.exe -I src\aiglasses\helpers\wifidirect_helper.py`
   then type `{"id":1,"cmd":"preflight"}` and Enter; it prints your Wi-Fi profile name and band. If the band
   is 5 GHz, switch to 2.4 GHz or disconnect first (the glasses are probably 2.4 GHz only, UNVERIFIED).
3. After you approve in writing, I add the guarded enable step and the type 8 IP wait, and give you the
   exact supervised command. You run it with the glasses on and charged, phone Bluetooth off.
`WIFI_DIRECT_ENABLED` stays `false` until then.
