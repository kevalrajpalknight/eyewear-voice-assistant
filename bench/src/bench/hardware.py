"""Report the machine the numbers were measured on."""

import json
import platform
import subprocess

import psutil


def _powershell(command: str) -> str:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return done.stdout.strip()


def report() -> dict[str, object]:
    gpus = _powershell("(Get-CimInstance Win32_VideoController).Name -join '; '")
    cpu = _powershell("(Get-CimInstance Win32_Processor).Name")
    return {
        "cpu": cpu or platform.processor(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "gpus": gpus,
        "nvidia_gpu": "nvidia" in gpus.lower(),
        "os": platform.platform(),
        "python": platform.python_version(),
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
