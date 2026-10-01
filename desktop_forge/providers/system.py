"""CPU, memory, disk, battery, temperatures and network, read from the kernel.

No dependencies and no network, so this widget is the one that always works --
which is why it is part of the default first-run desktop. Connection details
(Wi-Fi name, signal, VPN) come from NetworkManager when it is available and
are simply absent when it is not.
"""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any

from .base import Provider

HWMON_ROOT = "/sys/class/hwmon"
THERMAL_ROOT = "/sys/class/thermal"
NET_ROOT = "/sys/class/net"
MAX_SENSORS = 8

# hwmon chip name -> (kind, label, preferred sensor labels in order). A chip
# exposes several readings; the preferred one is the figure people quote --
# Tctl for Ryzen, the package for Intel, "edge" for AMD graphics.
CHIPS = {
    "k10temp": ("cpu", "CPU", ("Tctl", "Tdie")),
    "zenpower": ("cpu", "CPU", ("Tdie", "Tctl")),
    "coretemp": ("cpu", "CPU", ("Package id 0",)),
    "cpu_thermal": ("cpu", "CPU", ()),
    "amdgpu": ("gpu", "GPU", ("edge", "junction")),
    "radeon": ("gpu", "GPU", ()),
    "nouveau": ("gpu", "GPU", ()),
    "i915": ("gpu", "GPU", ()),
    "xe": ("gpu", "GPU", ()),
    "nvme": ("disk", "Drive", ("Composite",)),
    "drivetemp": ("disk", "Drive", ()),
    "acpitz": ("board", "System", ()),
    "thinkpad": ("board", "System", ()),
    "iwlwifi": ("wifi", "Wi-Fi", ()),
}
KIND_ORDER = ("cpu", "gpu", "disk", "board", "wifi", "other")
# Interfaces that only carry traffic already counted on a physical one, or
# none at all. A VPN's bytes also cross the Wi-Fi card, so adding both doubled
# the rate the widget showed.
VIRTUAL_PREFIXES = ("lo", "tun", "tap", "wg", "veth", "docker", "virbr", "br-",
                    "vnet", "dummy", "ipv6leak", "proton", "tailscale", "zt")


class SystemProvider(Provider):
    name = "system"

    def __init__(self) -> None:
        # CPU utilisation is a delta between two samples of cumulative
        # counters, so the first call has nothing to compare against and the
        # previous sample has to survive between polls.
        self._prev_cpu: tuple[int, int] | None = None
        self._prev_net: tuple[float, int, int] | None = None
        self._nm_client = None
        self._nm_failed = False

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        return {
            "cpu_percent": self._cpu_percent(),
            "memory": self._memory(),
            "disk": self._disk(options.get("disk_path", "/")),
            "battery": self._battery(),
            "network": self._network(),
            "thermals": read_thermals(),
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
        try:
            with open("/proc/net/dev", encoding="ascii") as handle:
                counters = parse_net_dev(handle.read())
        except OSError:
            return {}
        physical = {name: value for name, value in counters.items() if is_physical(name)}
        # Fall back to everything but loopback on a machine whose only link
        # looks virtual (a container, a VM with a virtio name we do not know).
        counted = physical or {n: v for n, v in counters.items() if n != "lo"}
        total_rx = sum(rx for rx, _ in counted.values())
        total_tx = sum(tx for _, tx in counted.values())

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
            **self._connection(),
        }

    def _connection(self) -> dict[str, Any]:
        """Name, kind, address and VPN of the connection carrying traffic."""
        client = self._network_manager()
        if client is not None:
            try:
                return connection_from_nm(client)
            except Exception:  # noqa: BLE001 - details are optional
                pass
        return connection_from_sysfs()

    def _network_manager(self):
        if self._nm_client is not None or self._nm_failed:
            return self._nm_client
        try:
            import gi
            gi.require_version("NM", "1.0")
            from gi.repository import NM
            # Created once, on the daemon's main loop, which keeps its object
            # cache current; every later poll is a read from memory.
            self._nm_client = NM.Client.new(None)
        except Exception:  # noqa: BLE001 - libnm missing or NetworkManager not running
            self._nm_failed = True
            self._nm_client = None
        return self._nm_client

    def _uptime(self) -> float | None:
        try:
            with open("/proc/uptime", encoding="ascii") as handle:
                return float(handle.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _millidegrees(path: Path) -> float | None:
    value = _read(path)
    try:
        celsius = int(value) / 1000 if value is not None else None
    except ValueError:
        return None
    # A disconnected probe reads -273 or a huge sentinel; neither is a reading.
    if celsius is None or celsius <= -40 or celsius >= 150:
        return None
    return round(celsius, 1)


def classify_chip(name: str) -> tuple[str, str, tuple[str, ...]]:
    for prefix, info in CHIPS.items():
        rest = name[len(prefix):]
        # iwlwifi_1, nvme0: a suffix numbers instances; it never names a new chip.
        if name.startswith(prefix) and (not rest or rest[0] == "_" or rest.isdigit()):
            return info
    return ("other", name.replace("_", " ").title() or "Sensor", ())


def read_thermals(hwmon_root: str = HWMON_ROOT, thermal_root: str = THERMAL_ROOT) -> dict[str, Any]:
    """One headline temperature per sensor chip, hottest-first-kind ordered."""
    sensors = []
    for chip in sorted(Path(hwmon_root).glob("hwmon*")):
        name = _read(chip / "name") or chip.name
        kind, label, preferred = classify_chip(name)
        readings = []
        for source in sorted(chip.glob("temp*_input")):
            prefix = source.name[: -len("_input")]
            celsius = _millidegrees(source)
            if celsius is None:
                continue
            readings.append({
                "label": _read(chip / f"{prefix}_label") or "",
                "celsius": celsius,
                "high": _millidegrees(chip / f"{prefix}_max"),
                "critical": _millidegrees(chip / f"{prefix}_crit"),
            })
        if not readings:
            continue
        chosen = next((r for want in preferred for r in readings if r["label"] == want),
                      readings[0])
        high = chosen["high"] or chosen["critical"]
        sensors.append({"id": f"{name}:{chosen['label'] or 'temp'}", "kind": kind,
                        "label": label, "celsius": chosen["celsius"],
                        "high": high, "critical": chosen["critical"]})
    if not sensors:
        # No hwmon at all (some ARM boards, VMs): thermal zones still report.
        for zone in sorted(Path(thermal_root).glob("thermal_zone*")):
            celsius = _millidegrees(zone / "temp")
            if celsius is not None:
                zone_type = _read(zone / "type") or zone.name
                kind, label, _ = classify_chip(zone_type)
                sensors.append({"id": zone.name, "kind": kind, "label": label,
                                "celsius": celsius, "high": None, "critical": None})
    # Two drives or GPUs must not read as the same label twice.
    counts: dict[str, int] = {}
    for sensor in sensors:
        counts[sensor["label"]] = counts.get(sensor["label"], 0) + 1
    seen: dict[str, int] = {}
    for sensor in sensors:
        if counts[sensor["label"]] > 1:
            seen[sensor["label"]] = seen.get(sensor["label"], 0) + 1
            sensor["label"] = f"{sensor['label']} {seen[sensor['label']]}"
    sensors.sort(key=lambda s: (KIND_ORDER.index(s["kind"]) if s["kind"] in KIND_ORDER
                                else len(KIND_ORDER), s["label"]))
    sensors = sensors[:MAX_SENSORS]
    hottest = max(sensors, key=lambda s: s["celsius"], default=None)
    return {"sensors": sensors, "hottest": hottest["id"] if hottest else None}


def parse_net_dev(text: str) -> dict[str, tuple[int, int]]:
    counters = {}
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            counters[name.strip()] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return counters


def is_physical(name: str, net_root: str = NET_ROOT) -> bool:
    if name.startswith(VIRTUAL_PREFIXES):
        return False
    # Real network hardware has a backing device; tunnels and bridges do not.
    return os.path.exists(os.path.join(net_root, name, "device"))


def _ipv4(name: str) -> str | None:
    """The first IPv4 address of an interface, from the kernel's own table."""
    try:
        import fcntl
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = fcntl.ioctl(sock.fileno(), 0x8915,  # SIOCGIFADDR
                                 struct.pack("256s", name[:15].encode()))
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None


def _default_interface(route_path: str = "/proc/net/route") -> str | None:
    best = None
    try:
        with open(route_path, encoding="ascii") as handle:
            for line in handle.readlines()[1:]:
                fields = line.split()
                if len(fields) > 6 and fields[1] == "00000000":
                    metric = int(fields[6])
                    if best is None or metric < best[1]:
                        best = (fields[0], metric)
    except (OSError, ValueError):
        return None
    return best[0] if best else None


def connection_from_sysfs(net_root: str = NET_ROOT,
                          route_path: str = "/proc/net/route") -> dict[str, Any]:
    """What can be known without NetworkManager: the link and its address."""
    default = _default_interface(route_path)
    physical = [name for name in sorted(os.listdir(net_root)) if is_physical(name, net_root)] \
        if os.path.isdir(net_root) else []
    up = [name for name in physical
          if _read(Path(net_root, name, "operstate")) == "up"]
    interface = default if default in up else (up[0] if up else None)
    if interface is None:
        return {"state": "disconnected"}
    wireless = os.path.isdir(os.path.join(net_root, interface, "wireless"))
    vpn = [default] if default and not is_physical(default, net_root) else []
    return {"state": "connected", "interface": interface,
            "kind": "wifi" if wireless else "ethernet", "name": interface,
            "ipv4": _ipv4(interface), "vpn": vpn}


def connection_from_nm(client) -> dict[str, Any]:
    from gi.repository import NM
    active = list(client.get_active_connections() or [])
    tunnels = [c for c in active if c.get_connection_type() in ("vpn", "wireguard")
               or c.get_vpn()]
    links = [c for c in active if c.get_connection_type() in (
        "802-11-wireless", "802-3-ethernet", "gsm", "bluetooth", "pppoe", "cdma")]
    if not links:
        return {"state": "connecting" if client.get_state() == NM.State.CONNECTING
                else "disconnected", "vpn": [c.get_id() for c in tunnels]}
    # The link that owns the default route; ties go to the first listed.
    link = next((c for c in links if c.get_default() or c.get_default6()), links[0])
    devices = link.get_devices() or []
    device = devices[0] if devices else None
    kind = {"802-11-wireless": "wifi", "802-3-ethernet": "ethernet", "gsm": "mobile",
            "cdma": "mobile", "bluetooth": "bluetooth"}.get(link.get_connection_type(), "other")
    result: dict[str, Any] = {
        "state": "connected", "kind": kind, "name": link.get_id(),
        "interface": device.get_iface() if device else None,
        "vpn": [c.get_id() for c in tunnels],
        "metered": bool(device and device.get_metered() in (NM.Metered.YES, NM.Metered.GUESS_YES)),
    }
    config = link.get_ip4_config()
    addresses = config.get_addresses() if config else []
    if addresses:
        result["ipv4"] = addresses[0].get_address()
    if isinstance(device, NM.DeviceWifi):
        point = device.get_active_access_point()
        if point is not None:
            ssid = point.get_ssid()
            if ssid is not None:
                result["ssid"] = NM.utils_ssid_to_utf8(ssid.get_data())
            result["signal"] = point.get_strength()
    return result
