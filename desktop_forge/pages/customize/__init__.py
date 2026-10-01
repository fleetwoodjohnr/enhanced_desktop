"""Customize: every desktop setting in one place, by section, with search.

Rows are generated from the settings registry; the existing Top Bar, Dock,
Desktop Icons and Folder Colors controls are moved in from OverallPage
rather than rewritten. Changes apply live, and a bar offers to revert them;
switching profile reverts by itself unless kept.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from ...backend import markup
from ...customize import SECTIONS, SETTINGS, Customizer, PreviewSession
from ...customize.backends import DesktopStore
from ...customize.profiles import ProfileStore
from ...customize.registry import in_section
from ..overall import OverallPage
from .editors import ExcludedApps, LayoutPicker, PictureRow, RulesEditor, running_features
from .profiles_page import ProfilesSection
from .rows import SettingsController, groups_for
from .shortcuts_page import ShortcutsSection

KEEP_SECONDS = 20

NOTES = {
    "windows": {
        "Transparency": "Applied by Desktop Forge's Shell extension to app windows.",
        "Shape": "Rounded corners are drawn over each window; maximized and fullscreen windows stay square.",
        "Shadows": "Applies to GTK apps opened after the change.",
    },
    "animations": {
        "Windows": "GNOME default keeps GNOME's own animation; the other styles replace it.",
    },
    "tiling": {
        "Tiling": "Windows on each workspace and display are arranged for you and glide into place. "
                  "Dialogs, fixed-size windows and windows a rule makes floating are left free; a "
                  "maximized window covers its tile until you restore it.",
        "Snapping": "Drag a window to an edge or corner to fill that part of the screen. Tiling, corners "
                    "and gaps use Desktop Forge's own snapping, which switches GNOME's edge snapping off "
                    "while any of them is on.",
    },
    "wallpaper": {
        "Slideshow": "Desktop Forge's background service changes the picture while you are logged in.",
        "Lock screen": "GNOME draws the lock screen; these are the settings it offers.",
    },
    "notifications": {
        "Banners": "Pop-up notifications. They also collect in the list under the clock.",
    },
    "input": {
        "Touchpad gestures": "Changing any three-finger swipe hands all three-finger swipes to Desktop "
                             "Forge (the same for four fingers); the ones left on GNOME default still do "
                             "GNOME's action, without following your fingers. Two-finger gestures stay "
                             "with apps.",
    },
}

# Hand-built controls search can find, as (section, words).
SEARCH_EXTRAS = (
    ("top_bar", "Top bar visibility position height hiding auto-hide reveal colors opacity foreground"),
    ("dock", "Dock visibility position icon size length colors opacity foreground"),
    ("desktop", "Desktop icons size icon pack theme glass material label color"),
    ("desktop", "Folder colors"),
    ("profiles", "Profiles presets save import export share switch look"),
    ("rules", "Window rules float workspace display size always on top"),
    ("wallpaper", "Wallpaper picture background dark style slideshow folder lock screen picture"),
    ("windows", "Apps left as they are exceptions games"),
    ("shortcuts", "Keyboard shortcuts keys keybindings tiling snap custom command terminal"),
)

# Modules this version's Shell extension has; an older running one lacks some.
EXPECTED_MODULES = {"notifications", "background", "top-bar", "animations", "window-effects",
                    "window-rules", "workspaces", "widget-grid", "tiling", "snap", "scratchpad", "gestures",
                    "keybindings"}

TITLES = {key: title for key, title, _icon in SECTIONS}


class PreviewBar(Gtk.Revealer):
    """"Keep these changes?" with Revert and Keep, and an optional countdown."""

    def __init__(self, revert, keep):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, reveal_child=False)
        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=16, margin_end=12)
        box.add_css_class("preview-bar")
        self.label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        box.append(self.label)
        revert_button = Gtk.Button(label="Revert")
        revert_button.connect("clicked", lambda _b: revert())
        keep_button = Gtk.Button(label="Keep")
        keep_button.add_css_class("suggested-action")
        keep_button.connect("clicked", lambda _b: keep())
        box.append(revert_button)
        box.append(keep_button)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("toolbar")
        frame.append(box)
        self.set_child(frame)

    def show(self, text: str) -> None:
        self.label.set_label(text)
        self.set_reveal_child(True)

    def hide(self) -> None:
        self.set_reveal_child(False)


class CustomizePage(Adw.BreakpointBin):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__(width_request=360, height_request=360)
        self._toasts = toast_overlay
        self.store = ProfileStore()
        try:
            DesktopStore().normalize()
        except OSError as exc:
            print(f"desktop-forge: could not update desktop.json: {exc}")
        self.controller = SettingsController(Customizer(), on_applied=self._applied, on_problem=self.toast)
        self._profile_session: PreviewSession | None = None
        self._profile_name = ""
        self._previous_active = ""
        self._countdown = 0
        self._timer = 0
        self._search_scope: list = []
        self._current = "profiles"

        self.overall = OverallPage(toast_overlay)
        self.overall.host = self
        self.pages: dict[str, Adw.PreferencesPage] = {}
        self._build_sections()
        self.set_child(self._build_layout())
        self.controller.watch_gsettings()
        self.connect("unrealize", self._closing)

    # -- sections ------------------------------------------------------------

    def _moved(self, group) -> Adw.PreferencesGroup:
        self.overall.remove(group)
        return group

    def _generated(self, key, before=(), after=(), leading=None) -> dict:
        page = Adw.PreferencesPage()
        for group in before:
            page.add(group)
        groups = groups_for(self.controller, in_section(key), page=page, descriptions=NOTES.get(key),
                            leading=leading)
        for group in after:
            page.add(group)
        self.pages[key] = page
        return groups

    def _build_sections(self) -> None:
        self.profiles = ProfilesSection(self)
        self.pages["profiles"] = self.profiles.page

        exceptions = Adw.PreferencesGroup(title="Exceptions")
        self.excluded = ExcludedApps(self.controller)
        exceptions.add(self.excluded.row)
        self._generated("windows", after=[exceptions])
        self.layout_picker = LayoutPicker(self.controller)
        self._generated("tiling", before=[self.layout_picker.group])
        for key in ("animations", "workspaces", "notifications", "input"):
            self._generated(key)
        self._generated("top_bar", before=[self._moved(self.overall._top_group)])
        self._generated("dock", before=[self._moved(self.overall._dock_group)])
        self._generated("desktop", before=[self._moved(self.overall._icons_group),
                                           self._moved(self.overall._group)])
        c = self.controller
        self._pictures = [
            PictureRow(c, "wallpaper.light", "Picture"),
            PictureRow(c, "wallpaper.dark", "Picture in dark style"),
            PictureRow(c, "wallpaper.folder", "Folder of pictures", folder=True, uri=False),
            PictureRow(c, "lock.picture", "Lock screen picture", subtitle_empty="Same as the wallpaper"),
        ]
        groups = self._generated("wallpaper", leading={
            "Wallpaper": [self._pictures[0].row, self._pictures[1].row],
            "Lock screen": [self._pictures[3].row]})
        slideshow = groups.get("Slideshow")
        if slideshow is not None:
            slideshow.add(self._pictures[2].row)
            self.controller.depend("wallpaper.slideshow", lambda: self._pictures[2].row.set_sensitive(
                bool(self.controller.get("wallpaper.slideshow"))))
            self._pictures[2].row.set_sensitive(bool(self.controller.get("wallpaper.slideshow")))
        self.shortcuts = ShortcutsSection(self)
        self.pages["shortcuts"] = self.shortcuts.page
        rules_page = Adw.PreferencesPage()
        self.rules = RulesEditor(self.controller, rules_page)
        self.pages["rules"] = rules_page
        self._explain_edge_snapping()

    def _explain_edge_snapping(self) -> None:
        """GNOME's edge snapping is off while Desktop Forge's is on; say so."""
        row = self.controller.rows.get("snap.edge_tiling")
        if row is None or not row.get_sensitive():
            return
        subtitle = row.get_subtitle()

        def update():
            ours = (self.controller.get("snap.quarters") is True or (self.controller.get("snap.gaps") or 0) > 0
                    or self.controller.get("tiling.enabled") is True)
            row.set_sensitive(not ours)
            row.set_subtitle("Replaced by tiling, corner snapping and gaps" if ours else subtitle)
        for setting_id in ("snap.quarters", "snap.gaps", "tiling.enabled"):
            self.controller.depend(setting_id, update)
        update()

    # -- layout -----------------------------------------------------------------

    def _build_layout(self):
        self.search = Gtk.SearchEntry(placeholder_text="Search settings", margin_top=8, margin_bottom=4,
                                      margin_start=8, margin_end=8)
        self.search.connect("search-changed", self._searched)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class("navigation-sidebar")
        self._section_keys = [key for key, _t, _i in SECTIONS if key in self.pages]
        for key, title, icon in SECTIONS:
            if key not in self.pages:
                continue
            box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            self.sidebar.append(Gtk.ListBoxRow(child=box, name=key))
        self.sidebar.connect("row-selected", self._section_selected)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.append(self.search)
        sidebar_box.append(Gtk.ScrolledWindow(child=self.sidebar, vexpand=True,
                                              hscrollbar_policy=Gtk.PolicyType.NEVER))

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for key, page in self.pages.items():
            self.stack.add_named(page, key)
        self.search_page = Adw.PreferencesPage()
        self.stack.add_named(self.search_page, "search")

        self.title = Adw.WindowTitle(title="")
        header = Adw.HeaderBar(title_widget=self.title, show_start_title_buttons=False,
                               show_end_title_buttons=False)
        header.add_css_class("flat")
        self.reset_button = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Reset this section")
        self.reset_button.connect("clicked", lambda _b: self._confirm_reset())
        header.pack_end(self.reset_button)
        self.preview_bar = PreviewBar(self._revert, self._keep)
        self.update_banner = Adw.Banner(title=self._update_notice(), revealed=bool(self._update_notice()))
        content = Adw.ToolbarView(content=self.stack)
        content.add_top_bar(header)
        content.add_top_bar(self.update_banner)
        content.add_top_bar(self.preview_bar)
        self.content_page = Adw.NavigationPage(title="", child=content)
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="Customize", child=sidebar_box), content=self.content_page,
            min_sidebar_width=250, max_sidebar_width=280)
        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)
        self.show_section("profiles")
        return self.split

    @staticmethod
    def _update_notice() -> str:
        """Why settings here may not take effect yet, or ""."""
        features = running_features()
        if features is None:
            return ("Desktop Forge's Shell extension is not running, so window, animation, tiling and "
                    "gesture settings wait until it is. Log out and back in after installing.")
        missing = EXPECTED_MODULES - set(features.get("modules", []))
        if missing:
            return "Log out and back in to finish updating: some settings here need the new Shell extension."
        if features.get("failed"):
            return "Some desktop features stopped after an error; they start again at your next login."
        return ""

    def show_section(self, key: str) -> None:
        if key not in self._section_keys:
            return
        self.sidebar.select_row(self.sidebar.get_row_at_index(self._section_keys.index(key)))

    def _section_selected(self, _list, row) -> None:
        if row is None:
            return
        key = row.get_name()
        self._current = key
        if self.search.get_text():
            self.search.set_text("")
        self.stack.set_visible_child_name(key)
        self.title.set_title(TITLES[key])
        self.content_page.set_title(TITLES[key])
        self.reset_button.set_visible(any(s.generated_ui for s in in_section(key)))
        if key == "profiles":
            self.profiles.refresh_state()
        self.split.set_show_content(True)

    # -- search -------------------------------------------------------------------

    def _searched(self, entry) -> None:
        text = entry.get_text().strip().casefold()
        self.controller.release(self._search_scope)
        self.stack.remove(self.search_page)
        self.search_page = Adw.PreferencesPage()
        self.stack.add_named(self.search_page, "search")
        if not text:
            self.stack.set_visible_child_name(self._current)
            self.title.set_title(TITLES.get(self._current, ""))
            self.reset_button.set_visible(any(s.generated_ui for s in in_section(self._current)))
            return
        self.sidebar.unselect_all()
        self.title.set_title("Search")
        self.reset_button.set_visible(False)
        words = text.split()
        found = 0
        self._search_scope = self.controller.collect()
        try:
            groups: dict[str, Adw.PreferencesGroup] = {}

            def group(section):
                if section not in groups:
                    groups[section] = Adw.PreferencesGroup(title=markup(TITLES.get(section, section)))
                    self.search_page.add(groups[section])
                return groups[section]

            for setting in SETTINGS:
                haystack = " ".join((setting.label, setting.description, setting.group,
                                     TITLES.get(setting.section, ""))).casefold()
                if not all(word in haystack for word in words) or setting.section not in self.pages:
                    continue
                if setting.generated_ui:
                    group(setting.section).add(self.controller.row(setting))
                    found += 1
            for section, keywords in SEARCH_EXTRAS:
                if all(word in keywords.casefold() for word in words):
                    row = Adw.ActionRow(title=markup(self._extra_title(keywords)),
                                        subtitle=markup(f"In {TITLES[section]}"), activatable=True)
                    row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                    row.connect("activated", lambda _r, s=section: self.show_section(s))
                    group(section).add(row)
                    found += 1
        finally:
            self.controller.stop_collecting()
        if not found:
            status = Adw.StatusPage(icon_name="edit-find-symbolic", title="No matching settings",
                                    description="Try another word, such as blur, gaps or clock.")
            holder = Adw.PreferencesGroup()
            holder.add(status)
            self.search_page.add(holder)
        self.stack.set_visible_child_name("search")
        self.split.set_show_content(True)

    @staticmethod
    def _extra_title(keywords: str) -> str:
        for name in ("Top bar", "Dock", "Desktop icons", "Folder colors", "Profiles", "Window rules",
                     "Wallpaper", "Apps left as they are"):
            if keywords.casefold().startswith(name.casefold()):
                return name
        return keywords.split(" ")[0]

    # -- applying and previewing ----------------------------------------------------

    def _applied(self, ids) -> None:
        if any(i.startswith(("chrome.", "dock.")) for i in ids):
            self.overall.reload_chrome()
        self.profiles.refresh_state()
        if self._profile_session is None and self.controller.session.active:
            self.preview_bar.show("Your changes are live on the desktop.")

    def apply_profile(self, profile) -> None:
        """Switch to a profile, going back after KEEP_SECONDS unless kept."""
        if self._profile_session is not None:
            self._revert()
        # Edits made before switching are kept rather than folded into the
        # profile preview, so Revert only undoes the switch.
        self.controller.flush()
        self.controller.session.keep()
        self._previous_active = self.store.active()
        self._profile_session = PreviewSession(self.controller.customizer)
        target = self.store.target(profile, self.controller.customizer.default)
        self.store.set_active(profile.id)
        problems = self.controller.apply_now(target, self._profile_session)
        self._profile_name = profile.name
        self._countdown = KEEP_SECONDS
        self._tick()
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add_seconds(1, self._tick)
        if problems:
            self.toast(f"{len(problems)} setting{'s' if len(problems) != 1 else ''} could not be applied here")

    def _tick(self) -> bool:
        if self._profile_session is None:
            self._timer = 0
            return GLib.SOURCE_REMOVE
        if self._countdown <= 0:
            self._timer = 0
            self._revert()
            return GLib.SOURCE_REMOVE
        self.preview_bar.show(f"Showing “{self._profile_name}”. Keep this look? "
                              f"Going back in {self._countdown} s.")
        self._countdown -= 1
        return GLib.SOURCE_CONTINUE

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _revert(self) -> None:
        self._stop_timer()
        if self._profile_session is not None:
            session, self._profile_session = self._profile_session, None
            problems = session.revert()
            self.store.set_active(self._previous_active)
            self.controller.reload()
            self._applied(["chrome.", "dock."])
            self.toast(f"Went back from “{self._profile_name}”" if not problems
                       else "Some settings could not be put back")
        elif self.controller.session.active:
            self.controller.flush()
            problems = self.controller.session.revert()
            self.controller.reload()
            self._applied(["chrome.", "dock."])
            self.toast("Changes reverted" if not problems else "Some changes could not be reverted")
        self.preview_bar.hide()

    def _keep(self) -> None:
        self._stop_timer()
        if self._profile_session is not None:
            self._profile_session.keep()
            self._profile_session = None
            self.toast(f"Now using “{self._profile_name}”")
        self.controller.flush()
        self.controller.session.keep()
        self.preview_bar.hide()
        self.profiles.refresh_state()

    def _confirm_reset(self) -> None:
        key = self._current
        ids = [s.id for s in in_section(key) if s.generated_ui]
        if not ids:
            return
        dialog = Adw.AlertDialog(heading=f"Reset {TITLES[key]}?",
                                 body="Every setting in this section goes back to its default. You can "
                                      "revert this afterwards.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def answered(_d, response):
            if response == "reset":
                self.controller.apply_now({i: self.controller.customizer.default(i) for i in ids
                                           if self.controller.customizer.available(
                                               self.controller.customizer.setting(i))})
        dialog.connect("response", answered)
        dialog.present(self)

    # -- window hooks ---------------------------------------------------------------

    def _closing(self, *_args) -> None:
        # The bar promised to go back unless kept; closing the window is not
        # keeping it.
        if self._profile_session is not None:
            self._revert()
        self.controller.flush()

    def reload(self) -> None:
        self.overall.reload()
        self.controller.reload()
        self.profiles.reload()

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=markup(message)))
