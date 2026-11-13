"""System metrics for the GUI status bar — stdlib only (no psutil).

CPU usage sampling:
  Linux: /proc/stat delta between two reads.
  Windows: GetSystemTimes via ctypes.
  macOS: host_statistics fallback to None (not critical for the status bar).
"""

from __future__ import annotations

import os
import sys
import time


class CpuMeter:
    """Call ``sample()`` periodically (>= 0.5 s apart); returns 0..100."""

    def __init__(self) -> None:
        self._prev: tuple[float, float] | None = None
        self._backend = self._pick_backend()

    @staticmethod
    def _pick_backend():
        if sys.platform.startswith("linux") and os.path.exists("/proc/stat"):
            return "procfs"
        if sys.platform == "win32":
            return "win32"
        return None

    def _read_linux(self) -> tuple[float, float]:
        with open("/proc/stat", "rb") as fh:
            line = fh.readline().split()
        # user nice system idle iowait irq softirq steal
        vals = [int(x) for x in line[1:9]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        return float(total), float(idle)

    def _read_win32(self) -> tuple[float, float]:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        idle_t, kernel_t, user_t = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle_t), ctypes.byref(kernel_t), ctypes.byref(user_t)
        ):
            return (0.0, 0.0)

        def to_int(ft: FILETIME) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        idle = to_int(idle_t)
        # kernel time already includes idle time
        total = to_int(kernel_t) + to_int(user_t)
        return float(total), float(idle)

    def sample(self) -> float | None:
        """CPU usage percent since the previous sample; None when unsupported."""
        if self._backend == "procfs":
            cur = self._read_linux()
        elif self._backend == "win32":
            cur = self._read_win32()
        else:
            return None
        if self._prev is None:
            self._prev = cur
            return None
        d_total = cur[0] - self._prev[0]
        d_idle = cur[1] - self._prev[1]
        self._prev = cur
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total)))


def memory_available_mb() -> int | None:
    """Best-effort available memory in MB (Linux only for now)."""
    try:
        with open("/proc/meminfo", "rb") as fh:
            for line in fh:
                if line.startswith(b"MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


class EtaEstimator:
    """Remaining-time estimate for a batch of files.

    Strategy (transparent, no magic): rolling mean of per-file wall time for
    completed files, times remaining count. Before the first file completes,
    returns None (GUI shows "估算中…"). Optionally seeded with a per-byte
    rate from a previous run so the first estimate is not blind.
    """

    def __init__(self, seed_seconds_per_mb: float | None = None) -> None:
        self._durations: list[float] = []
        self._seed = seed_seconds_per_mb

    def record(self, seconds: float) -> None:
        if seconds > 0:
            self._durations.append(seconds)

    def remaining(self, files_left: int, next_file_mb: float | None = None) -> float | None:
        if files_left <= 0:
            return 0.0
        if self._durations:
            mean = sum(self._durations) / len(self._durations)
            return mean * files_left
        if self._seed and next_file_mb:
            return self._seed * next_file_mb
        return None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "估算中…"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60:02d}秒"
    return f"{seconds // 3600}时{seconds % 3600 // 60:02d}分"


if __name__ == "__main__":  # manual smoke: python -m dwg2pdf.sysinfo
    meter = CpuMeter()
    meter.sample()
    for _ in range(5):
        time.sleep(1.0)
        cpu = meter.sample()
        print(f"cpu={cpu:.1f}%  mem_avail={memory_available_mb()}MB")
