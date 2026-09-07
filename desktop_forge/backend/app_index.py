"""Enumerates the applications installed on this system.

Uses Gio.AppInfo.get_all() rather than globbing .desktop files by hand: it
already merges every XDG_DATA_DIRS location (including both flatpak export
dirs), applies desktop-entry localization so names come back in the user's
language, and hands back a ready-to-use Gio.Icon per entry. Re-implementing
that with glob + configparser gets the flatpak and localization cases wrong.

The same approach is proven in the sibling PkgCenter project; the rpm -qf
package-ownership resolution it also does is deliberately not carried over,
since nothing here needs to know which package owns an entry.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


@dataclass
class InstalledApp:
    desktop_id: str
    name: str
    comment: str
    filename: str
    icon: Optional[Gio.Icon]
    is_flatpak: bool

    @property
    def sort_key(self) -> str:
        return self.name.casefold()

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        needle = needle.casefold()
        return (
            needle in self.name.casefold()
            or needle in self.comment.casefold()
            or needle in self.desktop_id.casefold()
        )


class AppIndex:
    def __init__(self) -> None:
        self.apps: list[InstalledApp] = []

    def build_async(self, on_done: Callable[[], None]) -> None:
        """Enumerate off the main loop.

        get_all() parses every entry on disk (~140 here) and resolving icons
        touches the theme cache, which is enough to show up as a stutter on a
        cold cache. Completion is marshalled back with GLib.idle_add so the
        caller only ever touches GTK from the main thread.
        """

        def _worker() -> None:
            apps = self._build()
            GLib.idle_add(self._finish, apps, on_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _finish(self, apps: list[InstalledApp], on_done: Callable[[], None]) -> bool:
        self.apps = apps
        on_done()
        return GLib.SOURCE_REMOVE

    def _build(self) -> list[InstalledApp]:
        apps: list[InstalledApp] = []
        seen: set[str] = set()

        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            # NoDisplay entries are plumbing (mime handlers, url handlers,
            # settings panels). They are installed, but they are not things a
            # user would ever want a desktop icon for.
            if info.get_nodisplay() or info.get_is_hidden():
                continue

            desktop_id = info.get_id() or ""
            filename = info.get_filename() or ""
            if not filename or desktop_id in seen:
                continue
            seen.add(desktop_id)

            apps.append(
                InstalledApp(
                    desktop_id=desktop_id,
                    name=info.get_display_name() or desktop_id,
                    comment=info.get_description() or "",
                    filename=filename,
                    icon=info.get_icon(),
                    is_flatpak="/flatpak/exports/share/applications/" in filename,
                )
            )

        apps.sort(key=lambda app: app.sort_key)
        return apps

    def search(self, needle: str) -> list[InstalledApp]:
        return [app for app in self.apps if app.matches(needle)]
