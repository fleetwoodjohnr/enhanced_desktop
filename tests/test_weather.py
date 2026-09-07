from __future__ import annotations

import unittest

from desktop_forge.providers import weather


PAYLOAD = {
    "current": {"time": "2026-09-04T15:30", "temperature_2m": 21.4, "weather_code": 0},
    "hourly": {
        "time": [
            "2026-09-04T14:00",
            "2026-09-04T15:00",
            "2026-09-04T16:00",
            "2026-09-04T17:00",
            "2026-09-04T18:00",
        ],
        "temperature_2m": [19.0, 21.0, 22.0, 21.5, 20.0],
        "weather_code": [0, 0, 3, 61, 95],
    },
}


class UpcomingHoursTests(unittest.TestCase):
    def test_starts_after_now_and_describes_each_hour(self) -> None:
        hours = weather._upcoming_hours(PAYLOAD)

        # 14:00 and 15:00 are already behind the 15:30 reading.
        self.assertEqual(
            [hour["time"] for hour in hours],
            ["2026-09-04T16:00", "2026-09-04T17:00", "2026-09-04T18:00"],
        )
        self.assertEqual(hours[0]["temperature"], 22.0)
        self.assertEqual(hours[0]["condition"], "Overcast")
        self.assertEqual(hours[1]["icon"], "weather-showers-scattered")
        self.assertEqual(hours[2]["condition"], "Thunderstorm")

    def test_respects_the_requested_count(self) -> None:
        self.assertEqual(len(weather._upcoming_hours(PAYLOAD, count=2)), 2)

    def test_missing_or_malformed_blocks_degrade_to_empty(self) -> None:
        self.assertEqual(weather._upcoming_hours({}), [])
        self.assertEqual(weather._upcoming_hours({"current": PAYLOAD["current"]}), [])
        self.assertEqual(weather._upcoming_hours({"hourly": PAYLOAD["hourly"]}), [])
        # Every hour already in the past leaves nothing to show.
        self.assertEqual(
            weather._upcoming_hours(
                {"current": {"time": "2026-09-05T00:00"}, "hourly": PAYLOAD["hourly"]}
            ),
            [],
        )

    def test_short_value_arrays_do_not_raise(self) -> None:
        payload = {
            "current": {"time": "2026-09-04T15:30"},
            "hourly": {"time": ["2026-09-04T16:00"], "temperature_2m": []},
        }
        hours = weather._upcoming_hours(payload)
        self.assertEqual(len(hours), 1)
        self.assertIsNone(hours[0]["temperature"])
        self.assertEqual(hours[0]["condition"], "Unknown")


if __name__ == "__main__":
    unittest.main()
