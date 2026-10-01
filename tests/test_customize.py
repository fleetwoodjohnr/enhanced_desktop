from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from desktop_forge import config
from desktop_forge.customize import backends, presets, profiles, registry, rules
from desktop_forge.customize.backends import (ChromeBackend, DesktopStore, DockBackend, GSettingsBackend,
                                              Unavailable, render_window_css, sync_window_css)
from desktop_forge.backend.shell_chrome import DashToDock
from desktop_forge.customize.customizer import Customizer, PreviewSession

SCHEMA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<schemalist>
  <enum id="org.df.test.fit"><value nick="zoom" value="0"/><value nick="scaled" value="1"/></enum>
  <schema id="org.df.test" path="/org/df/test/">
    <key name="flag" type="b"><default>true</default></key>
    <key name="count" type="i"><default>4</default></key>
    <key name="delay" type="u"><default>300</default></key>
    <key name="speed" type="d"><default>0.0</default></key>
    <key name="fit" enum="org.df.test.fit"><default>'zoom'</default></key>
    <key name="color" type="s"><default>'#023c88'</default></key>
  </schema>
</schemalist>
"""


def gsetting(setting_id, kind, key, default, **extra):
    return registry.Setting(setting_id, "desktop", "Test", setting_id, kind, default, backend="gsettings",
                            schema="org.df.test", key=key, **extra)


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class RegistryTests(unittest.TestCase):
    def test_ids_are_unique_and_defaults_are_valid(self):
        self.assertEqual(len(registry.SETTINGS), len(registry.BY_ID))
        sections = {section for section, _title, _icon in registry.SECTIONS}
        for setting in registry.SETTINGS:
            with self.subTest(setting.id):
                self.assertIn(setting.section, sections)
                self.assertEqual(registry.validate(setting, setting.default), setting.default)
                if setting.requires:
                    self.assertIn(setting.requires, registry.BY_ID)
                if setting.backend == "gsettings":
                    self.assertTrue(setting.schema and setting.key)
                if setting.kind == "enum":
                    self.assertIn(setting.default, [c[0] for c in setting.choices])
                if setting.kind in ("int", "float"):
                    self.assertLessEqual(setting.minimum, setting.default)
                    self.assertGreaterEqual(setting.maximum, setting.default)

    def test_numbers_are_clamped_and_wrong_types_refused(self):
        radius = registry.BY_ID["windows.corner_radius"]
        self.assertEqual(registry.validate(radius, 400), 32)
        self.assertEqual(registry.validate(radius, -3), 0)
        self.assertEqual(registry.validate(radius, 7.6), 8)
        for bad in ("8", True, None):
            with self.assertRaises(ValueError):
                registry.validate(radius, bad)
        with self.assertRaises(ValueError):
            registry.validate(registry.BY_ID["tiling.enabled"], 1)
        with self.assertRaises(ValueError):
            registry.validate(registry.BY_ID["tiling.layout"], "spiral-ish")
        with self.assertRaises(ValueError):
            registry.validate(registry.BY_ID["windows.border_color"], "red")
        self.assertEqual(registry.validate(registry.BY_ID["windows.border_color"], "#AABBCC"), "#aabbcc")

    def test_string_lists_are_deduplicated(self):
        exclude = registry.BY_ID["windows.exclude"]
        self.assertEqual(registry.validate(exclude, ["a.desktop", "a.desktop", "b"]), ["a.desktop", "b"])
        with self.assertRaises(ValueError):
            registry.validate(exclude, ["ok", 3])

    def test_registry_gsettings_keys_exist_on_this_system(self):
        # A key GLib does not know aborts the process, so every key the
        # registry names must be checked before use; this verifies the list
        # itself against the installed schemas where they are present.
        source = Gio.SettingsSchemaSource.get_default()
        for setting in registry.SETTINGS:
            if setting.backend != "gsettings":
                continue
            schema = source.lookup(setting.schema, True) if source else None
            if schema is None:
                continue
            with self.subTest(setting.id):
                self.assertTrue(schema.has_key(setting.key), setting.key)
                key = schema.get_key(setting.key)
                kind, allowed = key.get_range().unpack()
                if setting.kind == "enum" and kind == "enum":
                    self.assertLessEqual({c[0] for c in setting.choices}, set(allowed))


class DesktopStoreTests(TemporaryDirectoryTest):
    def test_file_is_written_complete_and_grouped(self):
        store = DesktopStore(str(self.tmp / "desktop.json"))
        store.write({"windows.corner_radius": 12})
        data = json.loads((self.tmp / "desktop.json").read_text())
        self.assertEqual(data["version"], backends.FORMAT_VERSION)
        self.assertEqual(data["windows"]["corner_radius"], 12)
        self.assertEqual(data["tiling"]["gaps_inner"], 8)
        self.assertEqual(data["windows"]["shadow"], "default")  # gtkcss settings travel too
        self.assertNotIn("dock", data)  # GNOME settings do not
        self.assertNotIn("chrome", data)

    def test_invalid_values_read_as_defaults(self):
        path = self.tmp / "desktop.json"
        path.write_text(json.dumps({"windows": {"corner_radius": "huge", "border_width": 99},
                                    "tiling": "not a section"}))
        values = DesktopStore(str(path)).read()
        self.assertEqual(values["windows.corner_radius"], 0)
        self.assertEqual(values["windows.border_width"], 8)  # clamped
        self.assertEqual(values["tiling.layout"], "master")

    def test_normalize_adds_new_settings_once(self):
        path = self.tmp / "desktop.json"
        path.write_text(json.dumps({"version": 1, "windows": {"corner_radius": 6}}))
        store = DesktopStore(str(path))
        self.assertTrue(store.normalize())
        self.assertFalse(store.normalize())
        data = json.loads(path.read_text())
        self.assertEqual(data["windows"]["corner_radius"], 6)
        self.assertIn("animations", data)


class GSettingsBackendTests(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        (self.tmp / "org.df.test.gschema.xml").write_text(SCHEMA_XML)
        subprocess.run(["glib-compile-schemas", str(self.tmp)], check=True)
        source = Gio.SettingsSchemaSource.new_from_directory(str(self.tmp), None, False)
        self.backend = GSettingsBackend(source=source, backend=Gio.memory_settings_backend_new())

    def test_reads_writes_and_defaults(self):
        flag = gsetting("t.flag", "bool", "flag", True)
        delay = gsetting("t.delay", "int", "delay", 0, minimum=0, maximum=3600)
        speed = gsetting("t.speed", "float", "speed", 0.0, minimum=-1, maximum=1)
        fit = gsetting("t.fit", "enum", "fit", "zoom", choices=(("zoom", "Z"), ("scaled", "S")))
        self.assertTrue(self.backend.get(flag))
        self.backend.set(flag, False)
        self.assertFalse(self.backend.get(flag))
        self.assertTrue(self.backend.default(flag))
        self.backend.set(delay, 60)  # an unsigned key takes a Python int
        self.assertEqual(self.backend.get(delay), 60)
        self.backend.set(speed, 0.25)
        self.assertEqual(self.backend.get(speed), 0.25)
        self.backend.set(fit, "scaled")
        self.assertEqual(self.backend.get(fit), "scaled")
        with self.assertRaises(ValueError):
            self.backend.set(fit, "stretched")  # not in the schema's enum

    def test_missing_schema_or_key_is_reported_not_fatal(self):
        missing_key = gsetting("t.gone", "bool", "does-not-exist", False)
        missing_schema = replace(missing_key, schema="org.df.absent", key="flag")
        for setting in (missing_key, missing_schema):
            self.assertFalse(self.backend.available(setting))
            with self.assertRaises(Unavailable):
                self.backend.get(setting)
            with self.assertRaises(Unavailable):
                self.backend.set(setting, True)

    def test_customizer_reports_problem_and_skips_unavailable_values(self):
        present = gsetting("t.flag", "bool", "flag", True)
        absent = replace(present, id="t.absent", schema="org.df.absent")
        customizer = Customizer(desktop=DesktopStore(str(self.tmp / "d.json")), gsettings=self.backend,
                                dock=DockBackend(find=lambda: None))
        with mock.patch.dict(registry.BY_ID, {"t.flag": present, "t.absent": absent}), \
                mock.patch("desktop_forge.customize.customizer.BY_ID", registry.BY_ID):
            self.assertEqual(customizer.problem(absent), "org.df.absent is not installed")
            self.assertEqual(customizer.problem(present), "")
            self.assertEqual(customizer.values(["t.flag", "t.absent"]), {"t.flag": True})
            applied, problems = customizer.set_many({"t.flag": False, "t.absent": True})
            self.assertEqual(applied, {"t.flag": False})
            self.assertIn("not installed", problems["t.absent"])


class ChromeBackendTests(unittest.TestCase):
    def setUp(self):
        self.saved = config.default_config()
        self.backend = ChromeBackend(load=lambda: self.saved, save=self._save)

    def _save(self, value):
        self.saved = value

    def test_values_go_into_overrides_and_empty_colour_follows_palette(self):
        opacity = registry.BY_ID["chrome.top_bar.opacity"]
        background = registry.BY_ID["chrome.top_bar.background"]
        self.backend.write({opacity: 0.5, background: "#112233"})
        self.assertEqual(self.saved.chrome["top_bar"], {"opacity": 0.5, "background": "#112233"})
        self.assertEqual(self.backend.get(background), "#112233")
        self.backend.write({background: ""})
        self.assertEqual(self.saved.chrome["top_bar"], {"opacity": 0.5})
        self.assertEqual(self.backend.get(background), "")


class WindowCssTests(TemporaryDirectoryTest):
    def paths(self):
        return (self.tmp / "gtk-3.0" / "gtk.css", self.tmp / "gtk-4.0" / "gtk.css")

    def test_block_keeps_user_css_and_the_desktop_icon_block(self):
        gtk3, gtk4 = self.paths()
        gtk4.parent.mkdir()
        user = ("/* Desktop Forge desktop-icon styles: begin */\n@import url(\"x\");\n"
                "/* Desktop Forge desktop-icon styles: end */\n.mine { color: red; }\n")
        gtk4.write_text(user)
        self.assertTrue(sync_window_css({"windows.shadow": "strong"}, self.paths()))
        text = gtk4.read_text()
        self.assertTrue(text.startswith(user.rstrip("\n")))
        self.assertIn("window.csd {", text)
        self.assertIn("decoration {", gtk3.read_text())
        self.assertFalse(sync_window_css({"windows.shadow": "strong"}, self.paths()))
        # Back to the app default: only the user's own CSS is left, and the
        # file created just for the block is removed.
        self.assertTrue(sync_window_css({"windows.shadow": "default"}, self.paths()))
        self.assertEqual(gtk4.read_text(), user)
        self.assertFalse(gtk3.exists())

    def test_default_writes_nothing(self):
        self.assertFalse(sync_window_css({"windows.shadow": "default"}, self.paths()))
        self.assertEqual(render_window_css({"windows.shadow": "default"}, "gtk-4.0"), "")
        self.assertFalse(any(p.exists() for p in self.paths()))


class CustomizerTests(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        self.saved = config.default_config()
        self.css_calls = []
        from tests.test_shell_chrome import FakeSettings
        self.dock_settings = FakeSettings()
        self.gsettings = {"dock.position": "BOTTOM"}
        gsettings = mock.Mock(available=lambda s: s.id in self.gsettings, get=lambda s: self.gsettings[s.id],
                              set=lambda s, v: self.gsettings.__setitem__(s.id, v))
        self.customizer = Customizer(
            desktop=DesktopStore(str(self.tmp / "desktop.json")), gsettings=gsettings,
            chrome=ChromeBackend(load=lambda: self.saved, save=lambda c: setattr(self, "saved", c)),
            window_css=lambda values: self.css_calls.append(values["windows.shadow"]),
            dock=DockBackend(find=lambda: DashToDock(self.dock_settings)))

    def test_routes_and_validates(self):
        applied, problems = self.customizer.set_many({
            "windows.corner_radius": 99, "chrome.top_bar.height": 40, "tiling.layout": "nonsense",
            "no.such": 1})
        self.assertEqual(applied, {"windows.corner_radius": 32, "chrome.top_bar.height": 40})
        self.assertEqual(set(problems), {"tiling.layout", "no.such"})
        self.assertEqual(self.customizer.get("windows.corner_radius"), 32)
        self.assertEqual(self.saved.chrome["top_bar"]["height"], 40)
        self.assertEqual(self.css_calls, [])

    def test_dock_visibility_writes_all_four_keys_and_reverts(self):
        self.dock_settings.values.update({"dock-fixed": True, "autohide": False, "intellihide": False})
        session = PreviewSession(self.customizer)
        self.assertEqual(self.customizer.get("dock.visibility"), "always")
        session.apply({"dock.visibility": "auto"})
        self.assertEqual(self.dock_settings.values["autohide"], True)  # so the dock can reveal
        self.assertEqual(self.dock_settings.values["dock-fixed"], False)
        session.revert()
        self.assertEqual(self.dock_settings.values["dock-fixed"], True)
        self.assertEqual(self.dock_settings.values["autohide"], False)

    def test_dock_and_top_bar_keep_separate_edges(self):
        applied, problems = self.customizer.set_many({"chrome.top_bar.position": "bottom"})
        self.assertEqual(applied, {})
        self.assertIn("same screen edge", problems["chrome.top_bar.position"])
        applied, problems = self.customizer.set_many({"chrome.top_bar.position": "bottom",
                                                      "dock.position": "LEFT"})
        self.assertEqual(problems, {})
        self.assertEqual(self.gsettings["dock.position"], "LEFT")
        _applied, problems = self.customizer.set_many({"dock.position": "BOTTOM"})
        self.assertIn("dock.position", problems)

    def test_corner_snapping_replaces_gnome_edge_snapping(self):
        self.gsettings.update({"snap.edge_tiling": True})
        registry_edge = registry.BY_ID["snap.edge_tiling"]
        self.assertTrue(self.customizer.available(registry_edge))
        self.customizer.set_many({"snap.quarters": True})
        self.assertIs(self.gsettings["snap.edge_tiling"], False)
        self.customizer.set_many({"snap.gaps": 6})
        self.assertIs(self.gsettings["snap.edge_tiling"], False)
        self.customizer.set_many({"snap.quarters": False, "snap.gaps": 0})
        self.assertIs(self.gsettings["snap.edge_tiling"], True)
        self.customizer.set_many({"windows.corner_radius": 4})  # unrelated: untouched
        self.assertIs(self.gsettings["snap.edge_tiling"], True)

    def test_tiling_replaces_gnome_edge_snapping(self):
        self.gsettings.update({"snap.edge_tiling": True})
        self.customizer.set_many({"tiling.enabled": True})
        self.assertIs(self.gsettings["snap.edge_tiling"], False)
        self.customizer.set_many({"tiling.enabled": False})
        self.assertIs(self.gsettings["snap.edge_tiling"], True)

    def test_removed_window_blur_is_dropped_from_old_files(self):
        _values, report = profiles.check_values({"windows.blur_transparent": True, "windows.corner_radius": 4})
        self.assertIn("windows.blur_transparent", report.dropped)
        self.assertNotIn("windows.blur_transparent", registry.BY_ID)

    def test_snapping_coupling_is_revertible_and_remembers_gnome_off(self):
        self.gsettings.update({"snap.edge_tiling": True})
        session = PreviewSession(self.customizer)
        session.apply({"snap.gaps": 8})
        self.assertIs(self.gsettings["snap.edge_tiling"], False)
        session.revert()
        self.assertIs(self.gsettings["snap.edge_tiling"], True)
        self.assertEqual(self.customizer.get("snap.gaps"), 0)
        # Someone who had GNOME's snapping off keeps it off afterwards.
        self.gsettings["snap.edge_tiling"] = False
        self.customizer.set_many({"snap.quarters": True})
        self.customizer.set_many({"snap.quarters": False})
        self.assertIs(self.gsettings["snap.edge_tiling"], False)

    def test_shadow_change_rewrites_gtk_css(self):
        self.customizer.set("windows.shadow", "none")
        self.assertEqual(self.css_calls, ["none"])

    def test_preview_revert_restores_every_backend(self):
        session = PreviewSession(self.customizer)
        session.apply({"windows.corner_radius": 10, "chrome.top_bar.opacity": 0.4})
        session.apply({"windows.corner_radius": 20})  # the original is kept, not 10
        self.assertEqual(sorted(session.changed()), ["chrome.top_bar.opacity", "windows.corner_radius"])
        self.assertEqual(session.revert(), {})
        self.assertEqual(self.customizer.get("windows.corner_radius"), 0)
        self.assertEqual(self.customizer.get("chrome.top_bar.opacity"), 0.96)
        self.assertFalse(session.active)

    def test_keep_forgets_the_originals(self):
        session = PreviewSession(self.customizer)
        session.apply({"tiling.enabled": True})
        session.keep()
        self.assertEqual(session.revert(), {})
        self.assertTrue(self.customizer.get("tiling.enabled"))


class ProfileTests(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        self.store = profiles.ProfileStore(str(self.tmp / "profiles"))
        self.defaults = lambda setting_id: registry.BY_ID[setting_id].default

    def test_create_rename_duplicate_delete(self):
        created = self.store.create("  My   Setup ", {"windows.corner_radius": 12})
        self.assertEqual(created.name, "My Setup")
        self.assertEqual(self.store.get(created.id).values, {"windows.corner_radius": 12})
        self.store.rename(created.id, "Work")
        copy_ = self.store.duplicate(created.id)
        self.assertEqual(copy_.name, "Work (copy)")
        self.assertEqual([p.name for p in self.store.user_profiles()], ["Work", "Work (copy)"])
        self.store.set_active(created.id)
        self.store.delete(created.id)
        self.assertEqual(self.store.active(), "")
        self.assertEqual(len(self.store.user_profiles()), 1)

    def test_presets_are_read_only_but_can_be_duplicated(self):
        preset = self.store.get("preset-hyprland")
        self.assertTrue(preset.builtin)
        for operation in (lambda: self.store.rename(preset.id, "x"), lambda: self.store.delete(preset.id),
                          lambda: self.store.save_values(preset.id, {})):
            with self.assertRaises(ValueError):
                operation()
        editable = self.store.duplicate(preset.id, defaults=self.defaults)
        self.assertFalse(editable.builtin)
        # The copy is complete: it also resets what the preset leaves neutral.
        self.assertTrue(editable.values["tiling.enabled"])
        self.assertIn("windows.dim_inactive", editable.values)

    def test_export_import_round_trip(self):
        created = self.store.create("Round", {"tiling.enabled": True, "tiling.gaps_inner": 4,
                                              "wallpaper.light": "file:///home/me/p.jpg"})
        target = self.tmp / f"round{profiles.EXTENSION}"
        self.store.export(created.id, str(target))
        data = json.loads(target.read_text())
        self.assertEqual(data["format"], profiles.FORMAT)
        report = self.store.import_file(str(target))
        self.assertEqual(report.profile.name, "Round (imported)")
        self.assertEqual(report.profile.values, created.values)
        self.assertEqual(report.machine, ["wallpaper.light"])

    def test_import_clamps_and_drops(self):
        text = json.dumps({"format": profiles.FORMAT, "version": 1, "name": "Shared",
                           "values": {"windows.corner_radius": 500, "tiling.layout": "bogus",
                                      "not.a.setting": 1, "windows.border_width": 2}})
        report = self.store.import_text(text)
        self.assertEqual(report.profile.values, {"windows.corner_radius": 32, "windows.border_width": 2})
        self.assertEqual(report.clamped, ["windows.corner_radius"])
        self.assertEqual(sorted(report.dropped), ["not.a.setting", "tiling.layout"])
        self.assertIn("adjusted", report.summary())

    def test_import_refuses_other_files(self):
        for text in ("not json", json.dumps([1]), json.dumps({"format": "other", "version": 1}),
                     json.dumps({"format": profiles.FORMAT, "version": 99, "name": "x", "values": {}}),
                     json.dumps({"format": profiles.FORMAT, "version": 1, "name": "", "values": {}})):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.store.import_text(text)

    def test_hand_edited_profile_file_is_validated_on_load(self):
        folder = self.tmp / "profiles"
        folder.mkdir()
        (folder / "edited-1.json").write_text(json.dumps({
            "format": profiles.FORMAT, "version": 1, "name": "Edited",
            "values": {"windows.corner_radius": -40}}))
        (folder / "broken-2.json").write_text("{")
        loaded = self.store.user_profiles()
        self.assertEqual([p.name for p in loaded], ["Edited"])
        self.assertEqual(loaded[0].values, {"windows.corner_radius": 0})
        with self.assertRaises(ValueError):
            self.store.get("../escape")

    def test_modified_marker(self):
        preset = self.store.get("preset-minimal")
        target = self.store.target(preset, self.defaults)
        self.assertEqual(self.store.modified(preset, dict(target), self.defaults), [])
        changed = {**target, "windows.corner_radius": 30}
        self.assertEqual(self.store.modified(preset, changed, self.defaults), ["windows.corner_radius"])


class LayoutTests(unittest.TestCase):
    def test_registry_layouts_match_the_extension(self):
        import re
        source = (Path(__file__).parents[1] / "extension" / "tilingLogic.js").read_text()
        js = re.findall(r"'(\w+)'", re.search(r"export const LAYOUTS = \[(.*?)\];", source).group(1))
        self.assertEqual(js, [value for value, _label in registry.BY_ID["tiling.layout"].choices])

    def test_every_layout_has_its_own_preview(self):
        from desktop_forge.pages.customize import preview
        master = preview.tile("master", 0, 0, 400, 300, 8, 0.55)
        for value, _label in registry.BY_ID["tiling.layout"].choices:
            if value != "master":
                self.assertNotEqual(preview.tile(value, 0, 0, 400, 300, 8, 0.55), master, value)


class PresetTests(unittest.TestCase):
    def test_presets_only_use_registry_settings_in_range(self):
        self.assertEqual(presets.check(), [])
        self.assertEqual(len(presets.PRESETS), 8)

    def test_presets_leave_personal_settings_alone(self):
        for setting_id in presets.MANAGED:
            prefix = setting_id.split(".", 1)[0]
            self.assertNotIn(prefix, ("input", "touchpad", "mouse", "gestures", "clock", "lock", "rules"))
            self.assertFalse(registry.BY_ID[setting_id].machine, setting_id)
        for preset in presets.PRESETS:
            for setting_id in preset["values"]:
                self.assertIn(setting_id, presets.MANAGED, f"{preset['id']} sets {setting_id}")

    def test_switching_presets_resets_what_the_previous_one_set(self):
        store = profiles.ProfileStore("/nonexistent")
        defaults = lambda setting_id: registry.BY_ID[setting_id].default  # noqa: E731
        minimal = store.target(store.get("preset-minimal"), defaults)
        self.assertFalse(minimal["tiling.enabled"])
        self.assertEqual(minimal["windows.border_width"], 0)


class RuleTests(unittest.TestCase):
    def test_rule_is_completed_and_clamped(self):
        rule = rules.validate_rule({"match": {"app": "firefox.desktop"},
                                    "actions": {"workspace": 99, "mode": "float", "opacity": 0.01,
                                                "above": "yes"}})
        self.assertEqual(rule["actions"]["workspace"], 36)
        self.assertEqual(rule["actions"]["opacity"], 0.2)
        self.assertFalse(rule["actions"]["above"])
        self.assertTrue(rule["enabled"])
        self.assertEqual(rules.describe(rule), "firefox · floating, workspace 36, 20% opaque")

    def test_shared_fixtures_match_like_the_extension(self):
        fixtures = json.loads((Path(__file__).parent / "rules_fixtures.json").read_text())
        for case in fixtures:
            with self.subTest(case["name"]):
                rule = rules.validate_rule(case["rule"])
                self.assertEqual(rules.matches(rule, case["window"]), case["matches"])

    def test_python_only_pattern_syntax_is_refused(self):
        for pattern in ("(?P<name>x)", "(?i)firefox"):
            with self.assertRaises(ValueError):
                rules.validate_rule({"match": {"title": pattern, "title_regex": True}})

    def test_rule_needs_a_match_and_a_valid_pattern(self):
        with self.assertRaises(ValueError):
            rules.validate_rule({"match": {}, "actions": {}})
        with self.assertRaises(ValueError):
            rules.validate_rule({"match": {"title": "(", "title_regex": True}})
        with self.assertRaises(ValueError):
            rules.validate_rules([{}] * (rules.MAX_RULES + 1))


if __name__ == "__main__":
    unittest.main()


class ShortcutTests(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        from desktop_forge.customize import shortcuts
        self.shortcuts = shortcuts
        source = Path(__file__).resolve().parents[1] / "extension" / "schemas"
        target = self.tmp / "schemas"
        target.mkdir()
        for xml in source.glob("*.gschema.xml"):
            (target / xml.name).write_text(xml.read_text())
        subprocess.run(["glib-compile-schemas", str(target)], check=True)
        # The memory backend: nothing here touches the real session's keys.
        self.store = shortcuts.ShortcutStore(extension_dir=str(target), backend=Gio.memory_settings_backend_new())

    def test_every_listed_shortcut_exists(self):
        self.assertTrue(self.store.extension_available)
        for _title, _about, items in self.shortcuts.GROUPS:
            for shortcut in items:
                source = Gio.SettingsSchemaSource.get_default()
                if shortcut.schema != self.shortcuts.EXTENSION and source.lookup(shortcut.schema, True) is None:
                    continue
                with self.subTest(shortcut.key):
                    self.assertTrue(self.store.available(shortcut), shortcut)

    def test_set_reset_and_defaults(self):
        tiling = self.shortcuts.Shortcut(self.shortcuts.EXTENSION, "toggle-tiling", "Tiling")
        self.assertEqual(self.store.get(tiling), [])
        self.assertTrue(self.store.is_default(tiling))
        self.store.set(tiling, ["<Super><Alt>t"])
        self.assertEqual(self.store.get(tiling), ["<Super><Alt>t"])
        self.assertFalse(self.store.is_default(tiling))
        self.store.reset(tiling)
        self.assertEqual(self.store.get(tiling), [])

    def test_conflicts_are_found_however_written_and_released(self):
        close = self.shortcuts.Shortcut(self.shortcuts.WM, "close", "Close window")
        self.store.set(close, ["<Super>q"])
        found = self.store.conflicts("<super>Q")
        self.assertEqual([c["label"] for c in found], ["Close window"])
        self.assertEqual(self.store.conflicts("<Super>q", exclude=(close.schema, close.key)), [])
        self.store.release(found[0], "<Super>q")
        self.assertEqual(self.store.get(close), [])

    def test_custom_commands(self):
        if Gio.SettingsSchemaSource.get_default().lookup(self.shortcuts.CUSTOM, True) is None:
            self.skipTest("GNOME Settings Daemon's schemas are not installed")
        path = self.store.save_custom("Terminal", "ptyxis", "<Super>Return")
        self.assertEqual(self.store.custom(), [{"path": path, "name": "Terminal", "command": "ptyxis",
                                                "binding": "<Super>Return"}])
        self.assertEqual(self.store.conflicts("<Super>Return")[0]["label"], "Terminal")
        second = self.store.save_custom("Files", "nautilus", "")
        self.assertNotEqual(second, path)
        self.store.remove_custom(path)
        self.assertEqual([c["name"] for c in self.store.custom()], ["Files"])
        with self.assertRaises(ValueError):
            self.store.save_custom("", "x", "")

    def test_bare_letters_are_refused(self):
        self.assertIn("Super", self.shortcuts.acceptable("a"))
        self.assertEqual(self.shortcuts.acceptable("<Super>a"), "")
        self.assertEqual(self.shortcuts.acceptable("F5"), "")
        self.assertTrue(self.shortcuts.acceptable("not a key"))


class SlideshowTests(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        from desktop_forge.customize import slideshow
        self.slideshow = slideshow
        self.folder = self.tmp / "Pictures"
        self.folder.mkdir()
        for name in ("b.jpg", "a.png", "c.webp", ".hidden.jpg", "notes.txt"):
            (self.folder / name).write_bytes(b"x")
        (self.tmp / "outside.jpg").write_bytes(b"x")
        (self.folder / "link.jpg").symlink_to(self.tmp / "outside.jpg")
        self.store = DesktopStore(str(self.tmp / "desktop.json"))
        self.now = [1000.0]
        self.background = mock.Mock()
        self.background.get_string.return_value = ""
        self.show = slideshow.Slideshow(self.store, self.background, str(self.tmp / "state.json"),
                                        clock=lambda: self.now[0])

    def test_only_pictures_inside_the_folder(self):
        self.assertEqual([p.name for p in self.slideshow.pictures(str(self.folder))], ["a.png", "b.jpg", "c.webp"])
        self.assertEqual(self.slideshow.pictures(str(self.tmp / "missing")), [])

    def test_changes_in_order_on_schedule(self):
        self.assertIsNone(self.show.tick(force=True))  # switched off
        self.store.write({"wallpaper.slideshow": True, "wallpaper.folder": str(self.folder),
                          "wallpaper.interval": 5, "wallpaper.shuffle": False})
        first = self.show.tick(force=True)
        self.assertTrue(first.endswith("/a.png"))
        self.background.set_string.assert_any_call("picture-uri-dark", first)
        self.background.get_string.return_value = first
        self.now[0] += 60
        self.assertIsNone(self.show.tick())  # not due for five minutes
        self.now[0] += 5 * 60
        self.assertTrue(self.show.tick().endswith("/b.jpg"))

    def test_shuffle_never_repeats_the_current_picture(self):
        current = (self.folder / "a.png").resolve().as_uri()
        choices = self.slideshow.pictures(str(self.folder))
        for _ in range(20):
            self.assertNotEqual(self.slideshow.next_picture(choices, current, True).as_uri(), current)
