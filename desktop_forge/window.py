import importlib
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from .backend import desktop_entry as de

# (attribute, module, class, id, title, icon). Imported lazily by _add_page so
# that one page failing cannot stop the others being built.
PAGES = [
    ("overall", "customize", "CustomizePage", "customize", "Customize",
     "preferences-desktop-appearance-symbolic"),
    ("shortcuts", "shortcuts", "ShortcutsPage", "shortcuts", "Shortcuts",
     "insert-link-symbolic"),
    ("widgets", "widgets", "WidgetsPage", "widgets", "Widgets",
     "preferences-desktop-wallpaper-symbolic"),
    ("reminders", "reminders", "RemindersPage", "reminders", "Reminders",
     "alarm-symbolic"),
    ("clive", "clive", "ClivePage", "clive", "CLIVE", "system-help-symbolic"),
]


class DesktopForgeWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(920, 760)
        self.set_title("Desktop Forge")

        self.toast_overlay = Adw.ToastOverlay()
        self._stack = Adw.ViewStack()
        self._pages: dict[str, Gtk.Widget] = {}

        for attribute, module, class_name, page_id, title, icon in PAGES:
            page = self._add_page(module, class_name, title)
            self._pages[attribute] = page
            self._stack.add_titled_with_icon(page, page_id, title, icon)

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        )
        header.pack_end(self._build_menu_button())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_bottom_bar(Adw.ViewSwitcherBar(stack=self._stack, reveal=False))
        toolbar.set_content(self._stack)

        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

    def _add_page(self, module_name: str, class_name: str, title: str) -> Gtk.Widget:
        """Build one page, degrading to a placeholder if it cannot be created.

        A tab is a feature; the application is not. Importing every page at
        module scope meant a single unimportable module -- an optional
        dependency, a package the installer forgot to ship -- raised before any
        window existed, so clicking the icon did nothing at all with no visible
        error. Anything that goes wrong in one page now costs that one tab.
        """
        try:
            module = importlib.import_module(f".pages.{module_name}", __package__)
            return getattr(module, class_name)(self.toast_overlay)
        except Exception:  # noqa: BLE001 - a broken page must never be fatal
            detail = traceback.format_exc()
            print(f"desktop-forge: could not load the {title} page:\n{detail}")
            return self._placeholder(title, detail)

    @staticmethod
    def _placeholder(title: str, detail: str) -> Gtk.Widget:
        status = Adw.StatusPage(
            title=f"{title} is unavailable",
            description=(
                "This part of the app failed to load. Everything else still works.\n"
                "Run desktop-forge from a terminal to see the full error."
            ),
            icon_name="dialog-warning-symbolic",
        )
        # The last line of a traceback is the actual cause and is short enough
        # to show without turning the page into a wall of text.
        cause = detail.strip().splitlines()[-1] if detail.strip() else ""
        if cause:
            label = Gtk.Label(label=cause, wrap=True, selectable=True)
            label.add_css_class("monospace-dim")
            status.set_child(label)
        return status

    def _build_menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Open Desktop Folder", "win.open-desktop")
        menu.append("Refresh", "win.refresh")
        menu.append("About Desktop Forge", "win.about")

        open_desktop = Gio.SimpleAction.new("open-desktop", None)
        open_desktop.connect("activate", self._on_open_desktop)
        self.add_action(open_desktop)

        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", lambda *_a: self._refresh_all())
        self.add_action(refresh)

        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)

        return Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu"
        )

    def _refresh_all(self) -> None:
        overall = self._pages.get("overall")
        if hasattr(overall, "reload"):
            overall.reload()
        # Any page here may be a placeholder, so nothing is assumed about what
        # methods it has.
        shortcuts = self._pages.get("shortcuts")
        manage = getattr(shortcuts, "manage", None)
        if manage is not None:
            manage.refresh()

        # Positions change on the desktop, not in this window, so the widget
        # list can be out of date whenever the user has dragged something.
        widgets = self._pages.get("widgets")
        if hasattr(widgets, "reload"):
            widgets.reload()

    def _on_open_desktop(self, *_args) -> None:
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(de.desktop_dir()))
        launcher.launch(self, None, None)

    def _on_about(self, *_args) -> None:
        about = Adw.AboutDialog(
            application_name="Desktop Forge",
            application_icon="org.jrf.DesktopForge",
            developer_name="jrf",
            version="0.1.0",
            comments=(
                "Create desktop shortcuts and add interactive widgets to your "
                "GNOME desktop."
            ),
        )
        about.present(self)
