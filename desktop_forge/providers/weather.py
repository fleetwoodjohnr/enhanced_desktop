"""Weather from Open-Meteo.

Chosen because it needs no API key and no signup, so a fresh install works
with nothing but a location.
"""
from __future__ import annotations

from typing import Any

from .base import Provider, ProviderError, get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# WMO 4677 weather codes, collapsed to the granularity a small widget can
# actually show, each mapped to an icon name from the standard freedesktop
# weather set so the extension can theme it.
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "weather-clear"),
    1: ("Mainly clear", "weather-few-clouds"),
    2: ("Partly cloudy", "weather-few-clouds"),
    3: ("Overcast", "weather-overcast"),
    45: ("Fog", "weather-fog"),
    48: ("Rime fog", "weather-fog"),
    51: ("Light drizzle", "weather-showers-scattered"),
    53: ("Drizzle", "weather-showers-scattered"),
    55: ("Heavy drizzle", "weather-showers-scattered"),
    56: ("Freezing drizzle", "weather-freezing-rain"),
    57: ("Freezing drizzle", "weather-freezing-rain"),
    61: ("Light rain", "weather-showers-scattered"),
    63: ("Rain", "weather-showers"),
    65: ("Heavy rain", "weather-showers"),
    66: ("Freezing rain", "weather-freezing-rain"),
    67: ("Freezing rain", "weather-freezing-rain"),
    71: ("Light snow", "weather-snow"),
    73: ("Snow", "weather-snow"),
    75: ("Heavy snow", "weather-snow"),
    77: ("Snow grains", "weather-snow"),
    80: ("Rain showers", "weather-showers"),
    81: ("Rain showers", "weather-showers"),
    82: ("Violent showers", "weather-showers"),
    85: ("Snow showers", "weather-snow"),
    86: ("Snow showers", "weather-snow"),
    95: ("Thunderstorm", "weather-storm"),
    96: ("Thunderstorm, hail", "weather-storm"),
    99: ("Thunderstorm, hail", "weather-storm"),
}


def describe(code: int | None) -> tuple[str, str]:
    if code is None:
        return ("Unknown", "weather-severe-alert")
    return WMO_CODES.get(int(code), ("Unknown", "weather-severe-alert"))


def geocode(place: str) -> list[dict[str, Any]]:
    """Look up candidate locations by name, for the settings UI."""
    if not place.strip():
        return []
    url = f"{GEOCODE_URL}?name={_quote(place)}&count=5&language=en&format=json"
    payload = get_json(url)
    results = []
    for item in payload.get("results") or []:
        label = ", ".join(
            part for part in (item.get("name"), item.get("admin1"), item.get("country")) if part
        )
        results.append(
            {
                "label": label,
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
            }
        )
    return results


class WeatherProvider(Provider):
    name = "weather"

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        latitude = options.get("latitude")
        longitude = options.get("longitude")
        if latitude is None or longitude is None:
            raise ProviderError("No location set — choose one in Desktop Forge")

        fahrenheit = options.get("units") == "fahrenheit"
        url = (
            f"{FORECAST_URL}?latitude={latitude}&longitude={longitude}"
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
            "weather_code,wind_speed_10m,is_day"
            "&hourly=temperature_2m,weather_code"
            "&daily=temperature_2m_max,temperature_2m_min,weather_code"
            "&forecast_days=5&timezone=auto"
        )
        if fahrenheit:
            url += "&temperature_unit=fahrenheit&wind_speed_unit=mph"

        payload = get_json(url)
        current = payload.get("current") or {}
        condition, icon = describe(current.get("weather_code"))

        daily = payload.get("daily") or {}
        days = []
        for index, date in enumerate(daily.get("time") or []):
            day_condition, day_icon = describe(_at(daily.get("weather_code"), index))
            days.append(
                {
                    "date": date,
                    "high": _at(daily.get("temperature_2m_max"), index),
                    "low": _at(daily.get("temperature_2m_min"), index),
                    "condition": day_condition,
                    "icon": day_icon,
                }
            )

        return {
            "place": options.get("place") or f"{latitude:.2f}, {longitude:.2f}",
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "is_day": bool(current.get("is_day", 1)),
            "condition": condition,
            "icon": icon,
            "unit": "°F" if fahrenheit else "°C",
            "wind_unit": "mph" if fahrenheit else "km/h",
            "forecast": days,
            "hourly": _upcoming_hours(payload),
            "forecast_mode": options.get("forecast_mode") or "hourly",
        }


def _upcoming_hours(payload: dict[str, Any], count: int = 12) -> list[dict[str, Any]]:
    """The next `count` hourly entries, starting after the current hour.

    Open-Meteo returns hourly data from midnight of the current local day, so
    the first half of the array is usually in the past. `forecast_hours` would
    trim it, but it counts from the start of the day rather than from now, so
    the slice is done here where "now" is unambiguous: `current.time` and
    `hourly.time` are both local wall-clock stamps from the same response.
    """
    current = payload.get("current") or {}
    hourly = payload.get("hourly") or {}
    times = hourly.get("time")
    now = current.get("time")
    if not isinstance(times, list) or not isinstance(now, str):
        return []

    entries = []
    for index, stamp in enumerate(times):
        if not isinstance(stamp, str) or stamp <= now:
            continue
        condition, icon = describe(_at(hourly.get("weather_code"), index))
        entries.append(
            {
                "time": stamp,
                "temperature": _at(hourly.get("temperature_2m"), index),
                "condition": condition,
                "icon": icon,
            }
        )
        if len(entries) >= count:
            break
    return entries


def _at(sequence: Any, index: int) -> Any:
    if isinstance(sequence, list) and index < len(sequence):
        return sequence[index]
    return None


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text.strip())
