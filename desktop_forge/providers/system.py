"""CPU, memory, disk, battery and network, read straight from the kernel.

No dependencies and no network, so this widget is the one that always works --
which is why it is part of the default first-run desktop.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .base import Provider


class SystemProvider(Provider):
    name = "system"

    def __init__(self) -> None:
        # CPU utilisation is a delta between two samples of cumulative
        # counters, so the first call has nothing to compare against and the
        # previous sample has to survive between polls.
        self._prev_cpu: tuple[int, int] | None = None
        self._prev_net: tuple[float, int, int] | None = None

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        return {
            "cpu_percent": self._cpu_percent(),
            "memory": self._memory(),
            "disk": self._disk(options.get("disk_path", "/")),
            "battery": self._battery(),
            "network": self._network(),
            "uptime_seconds": self._uptime(),
            "hostname": os.uname().nodename,
        }

    def _cpu_percent(self) -> float | None:
        try:
            with open("/proc/stat", encoding="ascii") as handle:
                fields = [int(v) for v in handle.readline().split()[1:]]
        except (OSError, ValueError):
            return None

        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        previous, self._prev_cpu = self._prev_cpu, (idle, total)
        if previous is None:
            return None

        idle_delta = idle - previous[0]
        total_delta = total - previous[1]
        if total_delta <= 0:
            return None
        return round(100.0 * (1.0 - idle_delta / total_delta), 1)

    def _memory(self) -> dict[str, Any]:
        values: dict[str, int] = {}
        try:
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    values[key] = int(rest.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return {}

        total = values.get("MemTotal", 0)
        # MemAvailable is the kernel's own estimate of what a new workload
        # could claim; it is far more honest than total - free, which counts
        # reclaimable page cache as used.
        available = values.get("MemAvailable", values.get("MemFree", 0))
        used = total - available
        return {
            "total": total,
            "available": available,
            "used": used,
            "percent": round(100.0 * used / total, 1) if total else None,
            "swap_total": values.get("SwapTotal", 0),
            "swap_used": values.get("SwapTotal", 0) - values.get("SwapFree", 0),
        }

    def _disk(self, path: str) -> dict[str, Any]:
        try:
            stats = os.statvfs(path)
        except OSError:
            return {}
        total = stats.f_blocks * stats.f_frsize
        # f_bavail, not f_bfree: the reserved-for-root blocks are not space
        # this user can actually use.
        free = stats.f_bavail * stats.f_frsize
        used = total - free
        return {
            "path": path,
            "total": total,
            "free": free,
            "used": used,
            "percent": round(100.0 * used / total, 1) if total else None,
        }

    def _battery(self) -> dict[str, Any] | None:
        base = "/sys/class/power_supply"
        try:
            names = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
        except OSError:
            return None
        if not names:
            return None

        battery = os.path.join(base, names[0])

        def read(field: str) -> str | None:
            try:
                with open(os.path.join(battery, field), encoding="ascii") as handle:
                    return handle.read().strip()
            except OSError:
                return None

        capacity = read("capacity")
        return {
            "name": names[0],
            "percent": int(capacity) if capacity and capacity.isdigit() else None,
            "status": read("status") or "Unknown",
        }

    def _network(self) -> dict[str, Any]:
        total_rx = total_tx = 0
        try:
            with open("/proc/net/dev", encoding="ascii") as handle:
                for line in handle.readlines()[2:]:
                    name, _, rest = line.partition(":")
                    name = name.strip()
                    # Loopback traffic is not network activity in any sense
                    # the user cares about.
                    if name == "lo":
                        continue
                    fields = rest.split()
                    total_rx += int(fields[0])
                    total_tx += int(fields[8])
        except (OSError, ValueError, IndexError):
            return {}

        now = time.monotonic()
        previous, self._prev_net = self._prev_net, (now, total_rx, total_tx)
        rx_rate = tx_rate = None
        if previous is not None:
            elapsed = now - previous[0]
            if elapsed > 0:
                rx_rate = max(0, int((total_rx - previous[1]) / elapsed))
                tx_rate = max(0, int((total_tx - previous[2]) / elapsed))

        return {
            "rx_bytes": total_rx,
            "tx_bytes": total_tx,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
        }

    def _uptime(self) -> float | None:
        try:
            with open("/proc/uptime", encoding="ascii") as handle:
                return float(handle.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None
