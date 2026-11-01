"""Does the glasses' headset (HFP) microphone work as a continuous input on this PC?

Run as `python -m aiglasses.research.hfp_probe` (a separate process: sounddevice must not be
imported in the BLE process). Lists input devices that look like the glasses, then records a few
seconds from each and prints sample rate, channels and signal level. Read-only: it only listens.
You speak during the recording. It does not play any sound.
"""

import sys

import numpy as np
import sounddevice as sd

SECONDS = 5
HINTS = ("aimb", "glasses", "hands-free", "headset")


def main() -> None:
    devices = sd.query_devices()
    wanted = [
        (index, d)
        for index, d in enumerate(devices)
        if d["max_input_channels"] > 0 and any(h in d["name"].lower() for h in HINTS)
    ]
    if not wanted:
        print("No input device that looks like the glasses. All inputs:")
        for index, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                print(f"  [{index}] {d['name']} ({d['default_samplerate']:.0f} Hz)")
        sys.exit(1)
    for index, d in wanted:
        rate = int(d["default_samplerate"])
        print(f"[{index}] {d['name']}: default {rate} Hz, {d['max_input_channels']} channel(s)")
        print(f"    speak for {SECONDS} s now ...")
        try:
            audio = sd.rec(
                SECONDS * rate, samplerate=rate, channels=1, device=index, dtype="float32"
            )
            sd.wait()
        except sd.PortAudioError as exc:
            print(f"    could not open: {exc}")
            continue
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio**2)))
        verdict = "usable level" if peak > 0.02 else "very quiet"
        print(f"    peak {peak:.3f}, rms {rms:.4f} ({verdict})")
    print("Check by ear in Windows sound settings whether the speaker audio got worse while the")
    print("headset microphone was open (the Bluetooth headset profile lowers playback quality).")


if __name__ == "__main__":
    main()
