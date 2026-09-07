"""Provider contract.

A provider knows how to fetch one kind of data and nothing else. It does not
know where the result is stored, how often it runs, or who renders it -- the
daemon owns all three. That separation keeps providers independent from the
GNOME Shell extension that displays their state.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

# Yahoo returns 429 to requests without a browser-ish User-Agent, and
# Open-Meteo asks callers to identify themselves. One honest UA for both.
USER_AGENT = "desktop-forge/0.1 (+https://github.com/fleetwoodjohnr)"
TIMEOUT = 12


class ProviderError(Exception):
    """A fetch failed in a way the user should be told about."""


class Provider:
    name = ""

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def start(self, on_change: Callable[[], None]) -> None:
        """Offered a way to say "my data changed, poll me now".

        Most sources can only be asked, so the default does nothing and the
        daemon's interval is the only thing that drives them. A source that can
        push -- Evolution Data Server can -- overrides this so a new calendar
        entry shows up in seconds rather than at the end of the interval.
        """

    def stop(self) -> None:
        """Release anything start() acquired. Called when the daemon drops
        a provider because no widget needs it any more."""


def get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    """GET a URL and parse JSON, normalising every failure into ProviderError.

    Callers persist the last good value, so the distinction that matters to
    them is 'did this attempt work', not which layer of the stack broke.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code} from {_host(url)}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Cannot reach {_host(url)}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise ProviderError(f"Network error contacting {_host(url)}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{_host(url)} returned invalid JSON") from exc


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url
