from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf

Floats = npt.NDArray[np.float32]


def load_mono(path: Path) -> tuple[Floats, int]:
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    return data.mean(axis=1).astype(np.float32), int(rate)


def normalize_peak(samples: Floats, target_dbfs: float = -3.0) -> Floats:
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if peak == 0.0:
        return samples
    return (samples * (10 ** (target_dbfs / 20) / peak)).astype(np.float32)


def peak_dbfs(samples: Floats) -> float:
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    return -120.0 if peak == 0.0 else 20 * float(np.log10(peak))
