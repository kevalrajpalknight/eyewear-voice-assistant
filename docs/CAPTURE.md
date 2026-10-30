# Capturing the official app (Phase 3)

Goal: record what the Cyan Glasses app sends to the glasses, so we learn the real commands instead of guessing.
This is passive. Nothing is written to the glasses by this project. Phone brand and model are still unknown, so the log
locations below are the common ones and will be confirmed once you tell me the model.

## 0. What you need

- Android phone with the Cyan Glasses app already working with the glasses.
- USB cable and `adb` on the PC (`winget install Google.PlatformTools`, then open a new terminal and run `adb version`).
- Phone: Settings > About phone > tap Build number 7 times, then enable USB debugging in Developer options.

## 1. Free the glasses first (they accept one BLE connection)

1. Stop any `aiglasses listen` or other script on the PC.
2. Turn Bluetooth off on the PC, or remove the glasses from Windows Bluetooth settings.
3. Turn the glasses off and on (long press the front button, see the manual) so they advertise again.

If the PC stays connected, the phone cannot connect, or the capture will show the wrong traffic.

## 2. Turn on HCI snoop logging

1. Settings > System > Developer options > **Enable Bluetooth HCI snoop log**. Choose the fullest setting
   (Android 14 offers "Enabled" and "Enabled Filtered"; pick **Enabled / Full**, not Filtered).
2. Switch the phone's Bluetooth **off, then on**. Logging starts with the new Bluetooth session. Some phones need a restart.
3. Do the action script below without turning Bluetooth off again.

## 3. Action script

Do **one action at a time and wait 10 seconds before the next**, so that actions can be told apart by order even if the
phone and PC clocks differ. Fill in the log template (`docs/capture_action_log.md`) as you go, writing the phone clock time
(with seconds) of each action.

Use the **buttons in the app** (the physical glasses buttons were already mapped from the PC side).

| # | Action | Notes |
|---|---|---|
| 1 | Open the Cyan Glasses app and connect the glasses | Wait until the home screen shows Connected, battery and firmware |
| 2 | Expand the device card (look only) | Do not tap anything inside it |
| 3 | Quick Function: **Take photo** | |
| 4 | Quick Function: **Record video**, wait 10 s, stop | One action: start and stop |
| 5 | Quick Function: **Recording** (audio only), wait 10 s, stop | One action: start and stop. Say something while it records |
| 6 | Quick Function: **Smart image recognition** | Wait for the answer |
| 7 | **AI tab**: one short question, wait for the **spoken answer to finish** | Write the question you asked |
| 8 | **AI translation**: one short sentence | Write the sentence and the language pair |
| 9 | **Album tab**: open it and let it sync, then open one photo | Probably what switches on the glasses' Wi-Fi. Your phone is on 5 GHz and the glasses hotspot should be 2.4 GHz, so write down the exact moment (with seconds) when the phone's Wi-Fi switches network, and the new network name |

Then wait 10 seconds and stop. Do not touch the glasses buttons or the phone's Bluetooth between actions.

**Do not** do any of these during the capture: firmware update, factory reset, unbind or remove the device, or anything that
looks like an update or OTA. Skip prompts to update the glasses or the app.

## 4. Pull the log

On the PC, with the phone connected by USB and USB debugging approved:

```powershell
adb devices
adb bugreport captures\bugreport.zip
```

This takes a few minutes. The log is inside the zip, normally at `FS/data/misc/bluetooth/logs/btsnoop_hci.log`
(`btsnoop_hci.log.last` is the previous session). The analyzer finds it automatically.

Vendor differences (to be confirmed for your model):

- Pixel and most stock Android: inside the bugreport zip as above.
- Samsung: often `FS/data/log/bt/btsnoop_hci.log` inside the bugreport.
- Xiaomi/Redmi/Poco: try `adb pull /sdcard/MIUI/debug_log/` or `adb pull /sdcard/btsnoop_hci.log`.
- OnePlus/Oppo/Realme: the bugreport zip usually has it. If not, enable the snoop toggle again and restart the phone.
- Rooted phones: `adb pull /data/misc/bluetooth/logs/btsnoop_hci.log captures\`.

If the file is a `.cfa`, a pcapng, or empty, tell me the phone model and what you got.

## 5. Analyze

```powershell
uv run aiglasses capture analyze captures\bugreport.zip --actions "connect,device-card,photo,video,audio-recording,image-recognition,ai-question,ai-translation,album"
```

Use the same number of names as segments reported (the command says so if they differ; adjust `--gap`, default 6 seconds
of quiet starts a new segment). It prints a table of every ATT write and notification to or from the glasses
(time, direction, characteristic, hex, decoded frame), any Classic Bluetooth RFCOMM/SPP data, and writes
`captures\analysis.md` and `captures\analysis.csv`. It also lists, for each event from the glasses, the writes the app
sent within 5 seconds.

The analyzer also searches every value for facts we already know and lists the matches under "known value": battery 100%
(`0x64`, a weak match because it is one byte), firmware text `9.40.00` (and the bytes 09 28 00), the app's build string
`2512271030` (as text and as BCD), and the glasses MAC `D0:4B:1B:C3:23:24` in both byte orders. Those matches are the
fastest way to find the device-info and battery commands.

What to look for:

- **After a photo event** (the glasses send `0x73`, 8 bytes): what the app writes back.
- **Before Wi-Fi comes up** (action 9, Album): the write that precedes the phone leaving 5 GHz for the glasses' 2.4 GHz network.
- **After an AI answer** (actions 7 and 8): anything the app sends back to the glasses.
- Whether writes use the same `BC | cmd | len | crc16 | payload` frame as the notifications (the analyzer decodes it).

The phone's Wi-Fi traffic is not in this log; only Bluetooth is. Write the SSID in the action log.

## 6. Privacy and cleanup

The snoop log contains **all** Bluetooth traffic from the phone: other devices' names and addresses, call and contact
data on hands-free links, and more. So:

- It stays on this PC, under `captures/`, which is gitignored (so are `btsnoop*` and `bugreport*.zip`). Never commit or upload it.
- Share only `captures\analysis.md` excerpts that concern the glasses.
- When done: turn **Enable Bluetooth HCI snoop log** back to Disabled, and delete the bugreport from the phone and PC when
  no longer needed.

## 7. Safety

Nothing from the capture is sent to the glasses automatically. A command is only replayed after it appears in an
analyzed capture, is added to `commands.yaml` with its source, and you approve that exact payload.
