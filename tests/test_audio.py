import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from typer.testing import CliRunner

from aiglasses.cli import main as cli
from aiglasses.domain.errors import NotSupportedError
from aiglasses.services.audio import AudioService, fit_channels, make_tone, resample_linear
from aiglasses.transport.audio_sd import guess_profile, select_endpoints
from aiglasses.transport.base import AudioEndpoint
from aiglasses.transport.fakes import FakeAudioIO

HOSTAPIS = [{"name": "MME"}, {"name": "Windows WASAPI"}]
DEVICES: list[dict[str, Any]] = [
    {"name": "Headphones (AIMB-G2_2324)", "hostapi": 0, "max_output_channels": 2,
     "max_input_channels": 0, "default_samplerate": 44100.0},
    {"name": "Headphones (AIMB-G2_2324)", "hostapi": 1, "max_output_channels": 2,
     "max_input_channels": 0, "default_samplerate": 48000.0},
    {"name": "Headset (AIMB-G2_2324)", "hostapi": 1, "max_output_channels": 0,
     "max_input_channels": 1, "default_samplerate": 16000.0},
    {"name": "Speakers (Realtek)", "hostapi": 1, "max_output_channels": 2,
     "max_input_channels": 0, "default_samplerate": 48000.0},
]  # fmt: skip

SPEAKER = AudioEndpoint(
    "Headphones (AIMB-G2_2324)", "output", 48000, 16, 2, "Windows WASAPI", "a2dp"
)
MIC = AudioEndpoint("Headset (AIMB-G2_2324)", "input", 16000, 17, 1, "Windows WASAPI", "hfp")


def test_select_endpoints_keeps_only_wasapi_glasses() -> None:
    found = select_endpoints(DEVICES, HOSTAPIS, "AIMB-G2")
    assert [(e.index, e.kind, e.profile, e.sample_rate) for e in found] == [
        (1, "output", "a2dp", 48000),
        (2, "input", "hfp", 16000),
    ]
    assert len(select_endpoints(DEVICES, HOSTAPIS, "")) == 3


@pytest.mark.parametrize(
    ("name", "profile"),
    [("Headphones (X)", "a2dp"), ("Headset (X)", "hfp"), ("Speakers", "unknown")],
)
def test_guess_profile(name: str, profile: str) -> None:
    assert guess_profile(name) == profile


def test_make_tone_is_faded_and_bounded() -> None:
    tone = make_tone(440, 1.0, 48000, 0.15)
    assert len(tone) == 48000
    assert tone[0] == 0.0 and abs(tone[-1]) < 1e-3
    assert 0.14 < float(np.max(np.abs(tone))) <= 0.1501


def test_resample_and_channels() -> None:
    mono = np.ones((16000, 1), dtype=np.float32)
    up = resample_linear(mono, 16000, 48000)
    assert up.shape == (48000, 1)
    assert fit_channels(up, 2).shape == (48000, 2)
    assert fit_channels(np.ones((4, 3), dtype=np.float32), 2).shape == (4, 2)


async def test_play_tone_targets_speaker_stereo() -> None:
    fake = FakeAudioIO([SPEAKER, MIC])
    target = await AudioService(fake).play_tone(880, 0.1, 0.1)
    samples, rate, endpoint = fake.played[0]
    assert target == SPEAKER and endpoint == SPEAKER
    assert rate == 48000 and samples.shape == (4800, 2)


async def test_play_file_resamples(tmp_path: Path) -> None:
    wav = tmp_path / "in.wav"
    sf.write(wav, np.zeros(1600, dtype=np.float32), 16000)
    fake = FakeAudioIO([SPEAKER])
    await AudioService(fake).play_file(wav)
    assert fake.played[0][0].shape == (4800, 2)


async def test_record_writes_wav_and_stats(tmp_path: Path) -> None:
    fake = FakeAudioIO([SPEAKER, MIC])
    stats = await AudioService(fake).record(0.5, tmp_path / "mic.wav")
    data, rate = sf.read(tmp_path / "mic.wav")
    assert rate == 16000 and len(data) == 8000
    assert stats.seconds == 0.5 and 0.09 < stats.peak < 0.11


async def test_missing_endpoint_raises_not_supported() -> None:
    with pytest.raises(NotSupportedError, match="Pair the glasses"):
        await AudioService(FakeAudioIO([])).play_tone()
    with pytest.raises(NotSupportedError):
        AudioService(None).endpoints()


def test_audio_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake = FakeAudioIO([SPEAKER, MIC])
    monkeypatch.setattr(cli, "SoundDeviceAudio", lambda: fake)
    runner = CliRunner()
    devices = runner.invoke(cli.app, ["audio", "devices"])
    assert devices.exit_code == 0 and "a2dp" in devices.output and "hfp" in devices.output
    assert runner.invoke(cli.app, ["audio", "play", "--seconds", "0.1"]).exit_code == 0
    rec = runner.invoke(cli.app, ["audio", "record", "--seconds", "0.2"])
    assert rec.exit_code == 0 and "peak" in rec.output
    assert (tmp_path / "captures" / "mic_test.wav").exists()


def test_cli_import_does_not_load_sounddevice() -> None:
    """sounddevice puts the thread in COM STA mode, which breaks bleak (see ADR 0004)."""
    code = "import sys, aiglasses.cli.main; sys.exit('sounddevice' in sys.modules)"
    assert subprocess.run([sys.executable, "-I", "-c", code], check=False).returncode == 0
