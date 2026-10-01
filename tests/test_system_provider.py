from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop_forge.providers import system


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ThermalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def chip(self, index: int, name: str, readings: dict[int, tuple[str, int]], **limits) -> None:
        chip = self.root / "hwmon" / f"hwmon{index}"
        write(chip / "name", name + "\n")
        for number, (label, millidegrees) in readings.items():
            write(chip / f"temp{number}_input", f"{millidegrees}\n")
            if label:
                write(chip / f"temp{number}_label", label + "\n")
            for key, value in limits.items():
                write(chip / f"temp{number}_{key}", f"{value}\n")

    def test_one_headline_reading_per_chip_in_a_stable_order(self) -> None:
        self.chip(0, "acpitz", {1: ("", 51000)})
        self.chip(1, "nvme", {1: ("Composite", 41850), 2: ("Sensor 1", 44850)},
                  max=82850, crit=84850)
        self.chip(2, "amdgpu", {1: ("junction", 60000), 2: ("edge", 51000)})
        self.chip(3, "k10temp", {1: ("Tccd1", 55000), 2: ("Tctl", 51250)})
        self.chip(4, "iwlwifi_1", {1: ("", 52000)})
        result = system.read_thermals(str(self.root / "hwmon"), str(self.root / "thermal"))
        labels = [(s["label"], s["celsius"]) for s in result["sensors"]]
        self.assertEqual(labels, [("CPU", 51.2), ("GPU", 51.0), ("Drive", 41.9),
                                  ("System", 51.0), ("Wi-Fi", 52.0)])
        drive = result["sensors"][2]
        self.assertEqual((drive["high"], drive["critical"]), (82.8, 84.8))
        self.assertEqual(result["hottest"], "iwlwifi_1:temp")

    def test_disconnected_probes_and_duplicate_labels(self) -> None:
        self.chip(0, "nvme", {1: ("Composite", 40000)})
        self.chip(1, "nvme", {1: ("Composite", 45000)})
        self.chip(2, "k10temp", {1: ("Tctl", -273000)})
        sensors = system.read_thermals(str(self.root / "hwmon"), str(self.root / "thermal"))["sensors"]
        self.assertEqual([s["label"] for s in sensors], ["Drive 1", "Drive 2"])

    def test_thermal_zones_are_the_fallback(self) -> None:
        write(self.root / "thermal" / "thermal_zone0" / "type", "cpu_thermal\n")
        write(self.root / "thermal" / "thermal_zone0" / "temp", "47000\n")
        sensors = system.read_thermals(str(self.root / "hwmon"), str(self.root / "thermal"))["sensors"]
        self.assertEqual([(s["kind"], s["label"], s["celsius"]) for s in sensors],
                         [("cpu", "CPU", 47.0)])

    def test_chip_suffixes_number_instances_but_never_rename_chips(self) -> None:
        self.assertEqual(system.classify_chip("iwlwifi_1")[1], "Wi-Fi")
        self.assertEqual(system.classify_chip("nvme0")[1], "Drive")
        self.assertEqual(system.classify_chip("xenfoo")[0], "other")


class NetworkTests(unittest.TestCase):
    DEV = """Inter-|   Receive                            |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
wlp3s0: 5000 50 0 0 0 0 0 0 700 7 0 0 0 0 0 0
proton0: 4000 40 0 0 0 0 0 0 600 6 0 0 0 0 0 0
"""

    def test_parse_net_dev_reads_rx_and_tx(self) -> None:
        self.assertEqual(system.parse_net_dev(self.DEV),
                         {"lo": (1000, 1000), "wlp3s0": (5000, 700), "proton0": (4000, 600)})

    def test_vpn_tunnels_are_not_counted_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wlp3s0" / "device").mkdir(parents=True)
            (Path(tmp) / "proton0").mkdir()
            self.assertTrue(system.is_physical("wlp3s0", tmp))
            self.assertFalse(system.is_physical("proton0", tmp))
            self.assertFalse(system.is_physical("lo", tmp))

    def test_sysfs_fallback_names_the_link_and_the_vpn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            net = Path(tmp) / "net"
            write(net / "wlp3s0" / "operstate", "up\n")
            (net / "wlp3s0" / "device").mkdir()
            (net / "wlp3s0" / "wireless").mkdir()
            write(net / "proton0" / "operstate", "unknown\n")
            route = Path(tmp) / "route"
            write(route, "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\n"
                         "proton0\t00000000\t00000000\t0001\t0\t0\t50\n"
                         "wlp3s0\t00000000\t0101A8C0\t0003\t0\t0\t600\n")
            result = system.connection_from_sysfs(str(net), str(route))
        self.assertEqual((result["state"], result["kind"], result["interface"], result["vpn"]),
                         ("connected", "wifi", "wlp3s0", ["proton0"]))

    def test_no_link_is_reported_as_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(system.connection_from_sysfs(tmp, str(Path(tmp) / "route")),
                             {"state": "disconnected"})


if __name__ == "__main__":
    unittest.main()
