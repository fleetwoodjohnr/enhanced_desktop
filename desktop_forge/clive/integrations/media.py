"""Playback control for media apps, through the standard MPRIS D-Bus interface.

These are extra capabilities of an app's own App Access entry -- Spotify is
one switch, covering both its window and its playback -- so turning an app
off also stops CLIVE controlling what it plays.
"""
from __future__ import annotations

from .base import Capability, Tool
from .registry import app_integration_id
from .schemas import STRING

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
ROOT = "org.mpris.MediaPlayer2"
# Apps that play media: players, and browsers (which report their tabs).
MEDIA_CATEGORIES = frozenset({"AudioVideo", "Audio", "Video", "Player", "Music", "WebBrowser"})

NOW_PLAYING = Capability("now_playing", "See what is playing", "read", "low", default=False)
PLAYBACK = Capability("playback", "Control playback", "execute", "normal",
                      "Play, pause, skip, set the volume and open links in the app's player.",
                      default=False)
CAPABILITIES = (NOW_PLAYING, PLAYBACK)
ACTIONS = {"play": "Play", "pause": "Pause", "play_pause": "PlayPause", "next": "Next",
           "previous": "Previous", "stop": "Stop"}


def is_media_app(categories) -> bool:
    return bool(MEDIA_CATEGORIES.intersection(categories))


def _bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _call(bus, name, path, interface, method, parameters=None, reply=None):
    from gi.repository import Gio, GLib
    result = bus.call_sync(name, path, interface, method, parameters,
                           GLib.VariantType.new(reply) if reply else None,
                           Gio.DBusCallFlags.NONE, 3000, None)
    return result.unpack() if result is not None else None


def _property(bus, name, interface, key):
    from gi.repository import GLib
    try:
        return _call(bus, name, MPRIS_PATH, "org.freedesktop.DBus.Properties", "Get",
                     GLib.Variant("(ss)", (interface, key)), "(v)")[0]
    except Exception:  # noqa: BLE001 - a player without the property simply lacks it
        return None


def players(bus=None) -> list[dict]:
    """Every MPRIS player on the session bus, with the app it belongs to."""
    bus = bus or _bus()
    names = _call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                  "ListNames", None, "(as)")[0]
    result = []
    for name in names:
        if name.startswith(MPRIS_PREFIX):
            result.append({"bus_name": name,
                           "desktop_entry": str(_property(bus, name, ROOT, "DesktopEntry") or ""),
                           "identity": str(_property(bus, name, ROOT, "Identity") or "")})
    return result


def player_for(desktop_id: str, found: list[dict]) -> dict | None:
    """The player a desktop ID owns: by its DesktopEntry, else by its bus name."""
    wanted = desktop_id.removesuffix(".desktop").casefold()
    short = wanted.rsplit(".", 1)[-1]
    for player in found:
        entry = player["desktop_entry"].casefold()
        if entry and (entry == wanted or entry.rsplit(".", 1)[-1] == short):
            return player
    parts = set(wanted.split("."))
    for player in found:
        # org.mpris.MediaPlayer2.brave.instance2 belongs to com.brave.Browser.
        suffix = player["bus_name"][len(MPRIS_PREFIX):].casefold().split(".", 1)[0]
        if suffix and (suffix in (wanted, short) or suffix in parts - {"com", "org", "io", "net"}):
            return player
    return None


def _player(desktop_id):
    player = player_for(desktop_id, players())
    if player is None:
        raise ValueError("That app is not playing anything, or is not running")
    return player


def _status(_ctx, a):
    bus = _bus()
    player = _player(a["app"])
    metadata = _property(bus, player["bus_name"], PLAYER, "Metadata") or {}
    artists = metadata.get("xesam:artist") or []
    return {"app": a["app"], "player": player["identity"],
            "status": str(_property(bus, player["bus_name"], PLAYER, "PlaybackStatus") or ""),
            "title": str(metadata.get("xesam:title", "")),
            "artist": ", ".join(str(x) for x in artists),
            "album": str(metadata.get("xesam:album", "")),
            "volume": _property(bus, player["bus_name"], PLAYER, "Volume")}


def _control(_ctx, a):
    player = _player(a["app"])
    _call(_bus(), player["bus_name"], MPRIS_PATH, PLAYER, ACTIONS[a["action"]])
    return {"app": a["app"], "done": a["action"]}


def _volume(_ctx, a):
    from gi.repository import GLib
    player = _player(a["app"])
    _call(_bus(), player["bus_name"], MPRIS_PATH, "org.freedesktop.DBus.Properties", "Set",
          GLib.Variant("(ssv)", (PLAYER, "Volume", GLib.Variant("d", a["volume"] / 100))))
    return {"app": a["app"], "volume": a["volume"]}


def _open(_ctx, a):
    from gi.repository import GLib
    uri = a["uri"]
    if not uri.startswith(("spotify:", "https://", "http://")):
        raise ValueError("Open a spotify: link or a web address")
    player = _player(a["app"])
    _call(_bus(), player["bus_name"], MPRIS_PATH, PLAYER, "OpenUri", GLib.Variant("(s)", (uri,)))
    return {"app": a["app"], "opened": uri}


def _app(a):
    return app_integration_id(a.get("app") or "")


TOOLS = (
    Tool("media_status", "What a media app is playing, and whether it is playing.", {"app": STRING},
         "now_playing", _status, _app, "Checking {app}…", describe=lambda a: a["app"]),
    Tool("media_control", "Play, pause, skip or stop in a media app.",
         {"app": STRING, "action": {"type": "string", "enum": list(ACTIONS)}}, "playback", _control, _app,
         "Controlling {app}…", describe=lambda a: f"{a['action']} in {a['app']}"),
    Tool("media_volume", "Set a media app's volume, 0-100.",
         {"app": STRING, "volume": {"type": "number", "minimum": 0, "maximum": 100}}, "playback",
         _volume, _app, "Setting the volume in {app}…", describe=lambda a: f"{a['volume']}%"),
    Tool("media_open", "Open a link in a media app, such as spotify:search:jazz or a track link.",
         {"app": STRING, "uri": STRING}, "playback", _open, _app, "Opening in {app}…",
         describe=lambda a: a["uri"]),
)
