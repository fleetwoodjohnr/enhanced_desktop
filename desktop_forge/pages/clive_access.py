"""App Access: every app CLIVE could use, each with its own switch.

The switches take effect the moment they change -- the service enforces them
on CLIVE's very next tool call -- so this page has no Save button. Everything
shown here comes from the service's own description of its integrations; the
page never decides what an app can do.
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ..backend import markup

STATE_LABELS = {"ready": "Ready", "needs_setup": "Needs setup", "unavailable": "Unavailable"}
RISK_NOTES = {"high": "Asks first", "normal": "", "low": ""}


def ago(timestamp) -> str:
    if not timestamp:
        return ""
    seconds = max(0, time.time() - timestamp)
    for limit, unit, size in ((60, "second", 1), (3600, "minute", 60), (86400, "hour", 3600),
                              (86400 * 30, "day", 86400)):
        if seconds < limit:
            count = int(seconds // size) or 1
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return time.strftime("%-d %b %Y", time.localtime(timestamp))


def summary_line(app: dict) -> str:
    """The row subtitle: what access it gives, whether it works, when it was used."""
    if not app["enabled"]:
        parts = ["Off"]
    else:
        parts = ["Read & write" if app["access"] == "read_write" else "Read only"]
        state = app.get("status", {}).get("state", "ready")
        parts.append(STATE_LABELS.get(state, state))
    used = (app.get("last_used") or {}).get("time")
    if used:
        parts.append(f"Last used {ago(used)}")
    return " · ".join(parts)


def app_icon(name: str) -> Gtk.Image:
    image = Gtk.Image(pixel_size=32)
    try:
        image.set_from_gicon(Gio.Icon.new_for_string(name)) if name else \
            image.set_from_icon_name("application-x-executable-symbolic")
    except GLib.Error:
        image.set_from_icon_name("application-x-executable-symbolic")
    return image


class AppAccessPage(Gtk.Box):
    """The App Access list, driven by the service's `access` description."""

    def __init__(self, client, toast=lambda message: None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.client = client
        self.toast = toast
        self.listing = {"apps": [], "categories": [], "paused": False}
        self.rows = {}
        self._syncing = False

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           margin_top=12, margin_bottom=6, margin_start=12, margin_end=12)
        self.count = Gtk.Label(xalign=0, wrap=True)
        self.count.add_css_class("title-4")
        controls.append(self.count)
        explain = Gtk.Label(xalign=0, wrap=True, label=(
            "CLIVE can only use apps switched on here. A switch takes effect on CLIVE's very "
            "next action, even in the middle of a task. Files you attach yourself are always "
            "available to that conversation."))
        explain.add_css_class("dim-label")
        controls.append(explain)

        self.pause = Adw.SwitchRow(title="Pause all app access",
                                   subtitle="The emergency stop: CLIVE can use nothing, and a running "
                                            "task stops, until you turn this off")
        self.pause.connect("notify::active", self._pause_toggled)
        pause_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        pause_list.add_css_class("boxed-list")
        pause_list.append(self.pause)
        controls.append(pause_list)

        filters = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Search apps", hexpand=True)
        self.search.connect("search-changed", lambda *_: self._apply_filter())
        filters.append(self.search)
        self.category_model = Gtk.StringList.new(["All categories"])
        self.category_ids = [""]
        self.category = Gtk.DropDown(model=self.category_model)
        self.category.connect("notify::selected", lambda *_: self._apply_filter())
        filters.append(self.category)
        controls.append(filters)

        bulk = Gtk.Box(spacing=6)
        connect = Gtk.Button(label="Connect an Email Account…")
        connect.connect("clicked", lambda *_: ConnectMailDialog(self).present(self))
        bulk.append(connect)
        bulk.append(Gtk.Box(hexpand=True))
        self.enable_all = Gtk.Button(label="Enable All")
        self.enable_all.connect("clicked", lambda *_: self._bulk(True))
        self.disable_all = Gtk.Button(label="Disable All")
        self.disable_all.connect("clicked", lambda *_: self._bulk(False))
        bulk.append(self.enable_all)
        bulk.append(self.disable_all)
        controls.append(bulk)
        self.append(controls)

        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                  margin_start=12, margin_end=12, margin_bottom=18)
        self.empty = Adw.StatusPage(icon_name="system-search-symbolic", title="No matching apps",
                                    visible=False)
        self.groups_box.append(self.empty)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    child=Adw.Clamp(maximum_size=760, child=self.groups_box))
        self.append(scroll)
        self.groups = {}
        self.reload()

    # -- data ----------------------------------------------------------------

    def reload(self):
        self.client.call("access", self._loaded)

    def _loaded(self, result, error):
        if error:
            self.toast(error)
            return
        # A service from before App Access answers without a listing.
        if not isinstance(result, dict) or not isinstance(result.get("apps"), list):
            return
        self.listing = result
        self._syncing = True
        self.pause.set_active(bool(result.get("paused")))
        self._syncing = False
        self._fill_categories()
        self._fill()
        self._apply_filter()

    def _call(self, op, **arguments):
        self.client.call(op, self._loaded, **arguments)

    # -- building ------------------------------------------------------------

    def _fill_categories(self):
        wanted = [c for c in self.listing.get("categories", [])
                  if any(app["category"] == c["id"] for app in self.listing["apps"])]
        ids = [""] + [c["id"] for c in wanted]
        if ids == self.category_ids:
            return
        selected = self.category_ids[self.category.get_selected()] \
            if self.category.get_selected() < len(self.category_ids) else ""
        self.category_ids = ids
        self.category_model.splice(0, self.category_model.get_n_items(),
                                   ["All categories"] + [c["name"] for c in wanted])
        self.category.set_selected(ids.index(selected) if selected in ids else 0)

    def _fill(self):
        names = {c["id"]: c["name"] for c in self.listing.get("categories", [])}
        seen = set()
        for app in sorted(self.listing["apps"], key=lambda a: (a["category"], a["name"].casefold())):
            seen.add(app["id"])
            group = self.groups.get(app["category"])
            if group is None:
                group = Adw.PreferencesGroup(title=markup(names.get(app["category"], "Other Apps")))
                self.groups[app["category"]] = group
                self.groups_box.append(group)
            row = self.rows.get(app["id"])
            if row is None:
                row = AppRow(self, app)
                self.rows[app["id"]] = row
                group.add(row)
            else:
                row.update(app)
        for key in [key for key in self.rows if key not in seen]:
            row = self.rows.pop(key)
            row.get_parent() and self._remove_row(row)
        apps = self.listing["apps"]
        on = sum(1 for app in apps if app["enabled"])
        if self.listing.get("paused"):
            self.count.set_label(f"All access paused · {on} of {len(apps)} apps switched on")
        else:
            self.count.set_label(f"{on} of {len(apps)} apps can be used by CLIVE")

    def _remove_row(self, row):
        for group in self.groups.values():
            try:
                group.remove(row)
                return
            except (TypeError, GLib.Error):
                continue

    def _visible_ids(self):
        query = self.search.get_text().strip().casefold()
        index = self.category.get_selected()
        category = self.category_ids[index] if index < len(self.category_ids) else ""
        return [app["id"] for app in self.listing["apps"]
                if (not category or app["category"] == category) and
                (not query or query in app["name"].casefold() or query in app["description"].casefold())]

    def _apply_filter(self):
        visible = set(self._visible_ids())
        for key, row in self.rows.items():
            row.set_visible(key in visible)
        for category, group in self.groups.items():
            group.set_visible(any(app["category"] == category and app["id"] in visible
                                  for app in self.listing["apps"]))
        self.empty.set_visible(not visible)
        filtered = bool(self.search.get_text().strip()) or self.category.get_selected() > 0
        self.enable_all.set_label("Enable Shown" if filtered else "Enable All")
        self.disable_all.set_label("Disable Shown" if filtered else "Disable All")

    # -- actions -------------------------------------------------------------

    def set_enabled(self, app_id, enabled):
        self._call("access_set", id=app_id, enabled=enabled)

    def set_capability(self, app_id, capability, enabled):
        self._call("access_set", id=app_id, capabilities={capability: enabled})

    def set_confirm(self, app_id, capability, ask):
        self._call("access_set", id=app_id, confirm={capability: ask})

    def set_option(self, app_id, key, value):
        self._call("integration_option", id=app_id, key=key, value=value)

    def remove_account(self, app_id, name):
        dialog = Adw.AlertDialog(heading=f"Disconnect {name}?",
                                 body="CLIVE forgets this account and its saved password. Your mail "
                                      "stays on the server and in Online Accounts.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Disconnect")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "remove" and
                       self._call("mail_account_remove", id=app_id))
        dialog.present(self)

    def check(self, app_id, row):
        row.set_subtitle("Checking…")

        def checked(result, error):
            if error:
                row.set_subtitle(markup(error))
                return
            label = STATE_LABELS.get(result.get("state", ""), result.get("state", ""))
            row.set_subtitle(markup(" — ".join(p for p in (label, result.get("detail", "")) if p)))
        self.client.call("access_check", checked, id=app_id)

    def _pause_toggled(self, row, _pspec):
        if self._syncing:
            return
        self.client.call("access_pause", lambda _r, error: (self.toast(error) if error else None,
                                                           self.reload()), paused=row.get_active())

    def _bulk(self, enabled):
        ids = self._visible_ids()
        apps = {app["id"]: app for app in self.listing["apps"]}
        changing = [apps[i]["name"] for i in ids if apps[i]["enabled"] != enabled]
        if not changing:
            self.toast("Nothing to change")
            return
        verb = "Turn on" if enabled else "Turn off"
        names = ", ".join(changing[:6]) + (f" and {len(changing) - 6} more" if len(changing) > 6 else "")
        body = (f"{verb} access for {names}?" + (
            "\n\nTheir own settings still apply: actions such as sending email or deleting files "
            "keep asking first, and new capabilities stay as they are." if enabled else ""))
        dialog = Adw.AlertDialog(heading=f"{verb} {len(changing)} app{'s' if len(changing) != 1 else ''}?",
                                 body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", verb)
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED if enabled
                                       else Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        filtered = len(ids) != len(apps)
        dialog.connect("response", lambda _d, response: response == "apply" and self._call(
            "access_bulk", enabled=enabled, **({"ids": ids} if filtered else {})))
        # Held so the confirmation can be driven from the UI smoke test.
        self.bulk_dialog = dialog
        dialog.present(self)


class AppRow(Adw.ExpanderRow):
    """One app: its main switch, and on expanding, what each permission allows."""

    def __init__(self, page: AppAccessPage, app: dict):
        super().__init__(title=markup(app["name"]))
        self.page = page
        self.app = app
        self._built = False
        self._syncing = False
        self.add_prefix(app_icon(app.get("icon", "")))
        self.badge = Gtk.Label(label="New", valign=Gtk.Align.CENTER, visible=False)
        self.badge.add_css_class("accent")
        self.badge.add_css_class("caption-heading")
        self.add_suffix(self.badge)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER,
                                 tooltip_text="Let CLIVE use this app")
        self.switch.connect("notify::active", self._toggled)
        self.add_suffix(self.switch)
        self.set_subtitle_lines(2)
        # Built on first expand: a hundred apps with their permission rows
        # would otherwise all be constructed before the page could appear.
        self.connect("notify::expanded", lambda *_: self.get_expanded() and self._build())
        self.update(app)

    def update(self, app: dict):
        self.app = app
        self._syncing = True
        self.switch.set_active(app["enabled"])
        self._syncing = False
        self.set_subtitle(markup(summary_line(app)))
        self.badge.set_visible(bool(app.get("new")))
        if self._built:
            self._sync_permissions()

    def _capability(self, capability):
        return next((c for c in self.app["capabilities"] if c["id"] == capability), {})

    # Each handler sends only a real change. A switch set to match the state
    # the service just reported must never echo back as a change of its own:
    # when that report arrives while GTK is still delivering the user's
    # toggle, the echo would flip the switch straight back.
    def _toggled(self, switch, _pspec):
        if not self._syncing and switch.get_active() != self.app["enabled"]:
            self.page.set_enabled(self.app["id"], switch.get_active())

    def _build(self):
        if self._built:
            return
        self._built = True
        if self.app.get("description"):
            about = Adw.ActionRow(title="About", subtitle=markup(self.app["description"]))
            about.set_subtitle_lines(0)
            self.add_row(about)
        self.status_row = Adw.ActionRow(title="Status")
        self.status_row.set_subtitle_lines(0)
        check = Gtk.Button(label="Check", valign=Gtk.Align.CENTER,
                           tooltip_text="Ask whether CLIVE can reach this app right now")
        check.connect("clicked", lambda *_: self.page.check(self.app["id"], self.status_row))
        self.status_row.add_suffix(check)
        self.add_row(self.status_row)
        for option in self.app.get("options", []):
            self.add_row(self._option_row(option))
        if self.app["id"].startswith("mail:"):
            remove = Adw.ButtonRow(title="Disconnect this account")
            remove.add_css_class("destructive-action")
            remove.connect("activated", lambda *_: self.page.remove_account(self.app["id"], self.app["name"]))
            self.add_row(remove)
        self.capability_rows = {}
        self.confirm_rows = {}
        for capability in self.app["capabilities"]:
            row = Adw.SwitchRow(title=markup(capability["label"]))
            note = RISK_NOTES.get(capability["risk"], "")
            subtitle = " · ".join(part for part in (capability.get("description", ""), note) if part)
            if subtitle:
                row.set_subtitle(markup(subtitle))
                row.set_subtitle_lines(0)
            row.connect("notify::active", self._capability_toggled, capability["id"])
            self.add_row(row)
            self.capability_rows[capability["id"]] = row
            if capability["risk"] == "high":
                ask = Adw.SwitchRow(title=f"Ask before: {markup(capability['label']).lower()}",
                                    subtitle="Shows exactly what will happen and waits for Confirm")
                if capability.get("confirm_locked"):
                    ask.set_subtitle("Always asks: this can bypass every other restriction")
                ask.connect("notify::active", self._confirm_toggled, capability["id"])
                self.add_row(ask)
                self.confirm_rows[capability["id"]] = ask
        self._sync_permissions()

    def _option_row(self, option):
        row = Adw.ActionRow(title=markup(option["label"]), subtitle=markup(option.get("value", "")))
        row.set_subtitle_lines(0)
        if option.get("kind") == "folder":
            choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)

            def chosen(dialog, result):
                try:
                    folder = dialog.select_folder_finish(result)
                except GLib.Error:
                    return  # cancelled
                if folder and folder.get_path():
                    self.page.set_option(self.app["id"], option["key"], folder.get_path())

            choose.connect("clicked", lambda *_: Gtk.FileDialog(title=option["label"]).select_folder(
                self.get_root(), None, chosen))
            row.add_suffix(choose)
        if option.get("detail"):
            row.set_tooltip_text(option["detail"])
        return row

    def _sync_permissions(self):
        self._syncing = True
        status = self.app.get("status", {})
        self.status_row.set_subtitle(markup(" — ".join(part for part in (
            STATE_LABELS.get(status.get("state", "ready"), status.get("state", "")),
            status.get("detail", "")) if part)))
        on = self.app["enabled"]
        for capability in self.app["capabilities"]:
            row = self.capability_rows.get(capability["id"])
            if row:
                row.set_active(capability["enabled"])
                # Stored values stay visible, but the app switch overrides them.
                row.set_sensitive(on)
            ask = self.confirm_rows.get(capability["id"])
            if ask:
                ask.set_active(capability.get("confirm") is not False)
                ask.set_sensitive(on and capability["enabled"] and not capability.get("confirm_locked"))
        self._syncing = False

    def _capability_toggled(self, row, _pspec, capability):
        if not self._syncing and row.get_active() != self._capability(capability).get("enabled"):
            self.page.set_capability(self.app["id"], capability, row.get_active())

    def _confirm_toggled(self, row, _pspec, capability):
        asks = self._capability(capability).get("confirm") is not False
        if not self._syncing and row.get_active() != asks:
            self.page.set_confirm(self.app["id"], capability, row.get_active())


class ConnectMailDialog(Adw.Dialog):
    """Connect an email account: from Online Accounts, or by IMAP with an app password."""

    def __init__(self, page: AppAccessPage):
        super().__init__(title="Connect an Email Account", content_width=520, content_height=620)
        self.page = page
        self.client = page.client
        prefs = Adw.PreferencesPage()
        self.online = Adw.PreferencesGroup(
            title="From Online Accounts",
            description="Google and Microsoft accounts sign in through GNOME, so CLIVE never "
                        "stores their password.")
        self.online_rows = []
        settings = Adw.ButtonRow(title="Add an account in Online Accounts…",
                                 end_icon_name="adw-external-link-symbolic")
        settings.connect("activated", lambda *_: self._open_online_accounts())
        self.online.add(settings)
        prefs.add(self.online)

        manual = Adw.PreferencesGroup(
            title="Other account",
            description="For any IMAP account. Use an app password from your provider rather than "
                        "your main password; it is kept in GNOME Keyring.")
        self.fields = {}
        for key, title in (("name", "Name, such as Work"), ("address", "Email address"),
                           ("imap_host", "IMAP server, such as imap.example.com"),
                           ("smtp_host", "SMTP server, such as smtp.example.com"),
                           ("username", "User name (if not the address)")):
            row = Adw.EntryRow(title=title)
            manual.add(row)
            self.fields[key] = row
        self.security = Adw.ComboRow(title="Sending security",
                                     model=Gtk.StringList.new(["SSL (port 465)", "STARTTLS (port 587)"]))
        manual.add(self.security)
        self.password = Adw.PasswordEntryRow(title="App password")
        manual.add(self.password)
        connect = Adw.ButtonRow(title="Connect")
        connect.add_css_class("suggested-action")
        connect.connect("activated", lambda *_: self._connect_manual())
        manual.add(connect)
        prefs.add(manual)

        toolbar = Adw.ToolbarView(content=prefs)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_child(self.toasts)
        self.client.call("goa_accounts", self._listed)

    def _open_online_accounts(self):
        try:
            Gio.Subprocess.new(["gnome-control-center", "online-accounts"], Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.toasts.add_toast(Adw.Toast(title=markup(error.message)))

    def _listed(self, result, error):
        for row in self.online_rows:
            self.online.remove(row)
        self.online_rows = []
        accounts = (result or {}).get("accounts", []) if not error else []
        for account in accounts:
            row = Adw.ActionRow(title=markup(account["address"]), subtitle=markup(account["provider"]))
            button = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda *_b, goa=account["goa_id"]: self._connect({"goa_id": goa}))
            row.add_suffix(button)
            self.online.add(row)
            self.online_rows.append(row)
        if not accounts:
            row = Adw.ActionRow(title="No mail accounts in Online Accounts yet",
                                subtitle=markup(error) if error else "Add one, then reopen this dialog")
            self.online.add(row)
            self.online_rows.append(row)

    def _connect_manual(self):
        request = {key: row.get_text().strip() for key, row in self.fields.items()}
        request["smtp_security"] = ("ssl", "starttls")[self.security.get_selected()]
        request["password"] = self.password.get_text()
        self._connect(request)

    def _connect(self, request):
        def done(result, error):
            if error:
                self.toasts.add_toast(Adw.Toast(title=markup(error)))
                return
            self.page._loaded(result, None)
            self.page.toast("Account connected. CLIVE can use it now; adjust what it may do below.")
            self.close()
        self.client.call("mail_account_add", done, account=request)


class AskFirstGroup(Adw.PreferencesGroup):
    """Every high-risk action across the switched-on apps, and whether it asks."""

    def __init__(self, client, toast=lambda message: None):
        super().__init__(title="Ask before",
                         description="Actions that change or send things for good. These ask "
                                     "even when Task approval lets everything else run without "
                                     "stopping. Administrator commands always ask.")
        self.client = client
        self.toast = toast
        self.rows = []
        self.empty = Adw.ActionRow(title="No switched-on app has actions that need asking")
        self.add(self.empty)
        self.reload()

    def reload(self):
        self.client.call("access", self._loaded)

    def _loaded(self, result, error):
        if error or not isinstance(result, dict) or not isinstance(result.get("apps"), list):
            return
        for row in self.rows:
            self.remove(row)
        self.rows = []
        for app in result.get("apps", []):
            if not app["enabled"]:
                continue
            for capability in app["capabilities"]:
                if capability["risk"] != "high" or not capability["enabled"]:
                    continue
                row = Adw.SwitchRow(title=markup(capability["label"]), subtitle=markup(app["name"]),
                                    active=capability.get("confirm") is not False,
                                    sensitive=not capability.get("confirm_locked"))
                row.connect("notify::active", self._toggled, app["id"], capability["id"])
                self.add(row)
                self.rows.append(row)
        self.empty.set_visible(not self.rows)

    def _toggled(self, row, _pspec, app_id, capability):
        def done(_result, error):
            if error:
                self.toast(error)
                self.reload()
        self.client.call("access_set", done, id=app_id, confirm={capability: row.get_active()})


__all__ = ["AppAccessPage", "AskFirstGroup", "ago", "summary_line"]
