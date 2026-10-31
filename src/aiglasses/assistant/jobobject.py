"""A Windows job object that kills its processes when the last handle to it closes.

Standard library only, so scripts outside the package can load this file by path. On other
systems the object does nothing.
"""

import ctypes
import subprocess
import sys
from ctypes import wintypes
from typing import Any

JOB_KILL_ON_CLOSE = 0x2000
JOB_EXTENDED_LIMIT_INFORMATION = 9


class JobObject:
    """Windows job that kills its processes when the last handle to it closes."""

    def __init__(self) -> None:
        self._handle: Any = None
        if sys.platform != "win32":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE

        basic_fields = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]
        io_fields = [(name, ctypes.c_uint64) for name in "abcdef"]
        basic = type("Basic", (ctypes.Structure,), {"_fields_": basic_fields})
        io_counters = type("IoCounters", (ctypes.Structure,), {"_fields_": io_fields})
        extended_fields = [
            ("BasicLimitInformation", basic),
            ("IoInfo", io_counters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]
        extended = type("Extended", (ctypes.Structure,), {"_fields_": extended_fields})

        handle = kernel.CreateJobObjectW(None, None)
        info = extended()
        info.BasicLimitInformation.LimitFlags = JOB_KILL_ON_CLOSE
        kernel.SetInformationJobObject(
            handle, JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        )
        self._handle, self._kernel = handle, kernel

    def add(self, process: "subprocess.Popen[bytes]") -> None:
        if self._handle is not None:
            self._kernel.AssignProcessToJobObject(self._handle, int(process._handle))  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
