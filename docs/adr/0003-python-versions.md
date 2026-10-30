# ADR 0003: Python versions

**Status:** accepted

`requires-python >=3.10`. Development and CI-style checks run on 3.12; nox runs the tests on 3.10 through 3.13.
Python 3.14 is not in the matrix: BLE, audio and capture dependencies (bleak, sounddevice, pyshark) often lag new
releases. Phase 0 happened to install and pass on 3.14, but that is not a supported configuration. If something
fails on 3.14, record it here rather than fixing it.
