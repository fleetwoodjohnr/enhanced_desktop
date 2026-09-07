from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from gi.repository import Gio

from desktop_forge.backend.folder_colors import FolderColors, normalize_color, render_icon


class FolderColorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folders = [self.root / "A & B #1", self.root / "資料"]
        for folder in self.folders:
            folder.mkdir()
        self.uris = [Gio.File.new_for_path(str(folder)).get_uri() for folder in self.folders]
        self.store = FolderColors(str(self.root / "data"))
        self.metadata = {}
        # Exercise real files, SVGs and persistence with an isolated metadata adapter.
        for method, replacement in (
            ("_read_icon", lambda folder: self.metadata.get(folder.get_uri())),
            ("_write_icon", lambda folder, icon: self.metadata.__setitem__(folder.get_uri(), icon)),
        ):
            mock = patch.object(FolderColors, method, staticmethod(replacement))
            mock.start()
            self.addCleanup(mock.stop)

    def test_independent_colors_and_restart(self):
        first = self.store.apply(self.uris[0], "#123ABC")
        second = self.store.apply(self.uris[1], "#ef3291")
        entries = FolderColors(str(self.root / "data")).load()
        self.assertEqual(entries, [first, second])
        self.assertEqual(first.color, "#123abc")
        self.assertNotEqual(self.metadata[first.uri], self.metadata[second.uri])
        for entry in entries:
            ET.parse(Gio.File.new_for_uri(entry.icon_uri).get_path())
        self.store.reset(first.uri)
        self.assertIsNone(self.metadata[first.uri])
        self.assertEqual(self.metadata[second.uri], second.icon_uri)
        self.assertEqual(self.store.load(), [second])

    def test_multiple_edits_restore_original_icon(self):
        self.metadata[self.uris[0]] = "file:///tmp/my-original-icon.svg"
        self.store.apply(self.uris[0], "#112233")
        self.store.apply(self.uris[0], "#445566")
        self.store.reset(self.uris[0])
        self.assertEqual(self.metadata[self.uris[0]], "file:///tmp/my-original-icon.svg")

    def test_reset_preserves_external_change_and_reapply_updates_history(self):
        self.store.apply(self.uris[0], "#112233")
        self.metadata[self.uris[0]] = "file:///tmp/external.svg"
        self.store.reset(self.uris[0])
        self.assertEqual(self.metadata[self.uris[0]], "file:///tmp/external.svg")
        self.store.apply(self.uris[0], "#112233")
        self.metadata[self.uris[0]] = "file:///tmp/new-external.svg"
        self.store.apply(self.uris[0], "#445566")
        self.store.reset(self.uris[0])
        self.assertEqual(self.metadata[self.uris[0]], "file:///tmp/new-external.svg")

    def test_failed_metadata_write_retains_saved_color(self):
        first = self.store.apply(self.uris[0], "#112233")
        with patch.object(self.store, "_write_icon", side_effect=OSError("metadata unavailable")):
            with self.assertRaises(OSError):
                self.store.apply(self.uris[0], "#445566")
        self.assertEqual(self.store.load(), [first])
        self.assertEqual(self.metadata[first.uri], first.icon_uri)

    def test_failed_store_write_restores_current_icon(self):
        first = self.store.apply(self.uris[0], "#112233")
        with patch.object(self.store, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.apply(self.uris[0], "#445566")
            self.assertEqual(self.metadata[first.uri], first.icon_uri)
            with self.assertRaises(OSError):
                self.store.reset(first.uri)
        self.assertEqual(self.store.load(), [first])
        self.assertEqual(self.metadata[first.uri], first.icon_uri)

    def test_invalid_inputs_never_write_metadata(self):
        for color in ("red", "#123", "#abcdex", '<svg onload="bad">'):
            with self.assertRaises(ValueError):
                self.store.apply(self.uris[0], color)
        with self.assertRaises(ValueError):
            self.store.apply("sftp://example.com/folder", "#112233")
        file = self.root / "file.txt"
        file.write_text("content")
        with self.assertRaises(ValueError):
            self.store.apply(file.as_uri(), "#112233")
        self.assertEqual(self.metadata, {})
        self.assertFalse(self.store.store_path.exists())

    def test_moved_folder_can_be_located_and_retains_reset_history(self):
        self.metadata[self.uris[0]] = "file:///tmp/original.svg"
        first = self.store.apply(self.uris[0], "#112233")
        destination = self.root / "Moved"
        self.folders[0].rename(destination)
        self.metadata[destination.as_uri()] = self.metadata.pop(first.uri)
        self.store.relocate(first.uri, destination.as_uri())
        self.assertEqual(self.store.load()[0].uri, destination.as_uri())
        self.store.reset(destination.as_uri())
        self.assertEqual(self.metadata[destination.as_uri()], "file:///tmp/original.svg")

    def test_forget_only_removes_unavailable_folders(self):
        first = self.store.apply(self.uris[0], "#112233")
        with self.assertRaises(ValueError):
            self.store.forget(first.uri)
        self.folders[0].rmdir()
        self.store.forget(first.uri)
        self.assertEqual(self.store.load(), [])

    def test_corrupt_history_is_not_silently_overwritten(self):
        self.store.apply(self.uris[0], "#112233")
        self.store.store_path.write_text("broken json")
        with self.assertRaises(ValueError):
            self.store.apply(self.uris[1], "#445566")
        self.assertEqual(self.store.store_path.read_text(), "broken json")

    def test_symlink_selection_uses_one_canonical_folder(self):
        link = self.root / "Alias"
        link.symlink_to(self.folders[0], target_is_directory=True)
        self.store.apply(link.as_uri(), "#112233")
        self.store.apply(self.uris[0], "#445566")
        self.assertEqual(len(self.store.load()), 1)
        self.assertEqual(self.store.load()[0].uri, self.uris[0])

    def test_vector_template_supports_extreme_and_custom_colors(self):
        for color in ("#000000", "#ffffff", "#ef34cb"):
            svg = render_icon(color)
            ET.fromstring(svg)
            self.assertIn(color.encode(), svg)
            self.assertNotIn(b"$", svg)
        self.assertEqual(normalize_color(" #ABCDEF "), "#abcdef")


if __name__ == "__main__":
    unittest.main()
