"""What CLIVE accepts as an attachment, and what actually reaches the model.

Attachments are the one path where a file the user names is read whole and put
in front of the model, so the boundary and the ceilings are the whole point.
"""
from __future__ import annotations

import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from desktop_forge.clive import attachments
from desktop_forge.clive.attachments import (MAX_FILES, MAX_TEXT, read_all,
                                             read_attachment, read_context, turns)


def png_bytes(width=8, height=8):
    """A real PNG, built the way service.probe_model builds its own fixture."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    header = struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0)
    rows = (b"\0" + b"\x00\x00\xff" * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(patch("pathlib.Path.home", return_value=self.root))

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return str(path)

    # -- classification ----------------------------------------------------

    def test_a_text_file_arrives_whole(self):
        result = read_attachment(self.write("notes.md", "# Notes\nhello"))
        self.assertEqual(result["kind"], "text")
        self.assertEqual(result["name"], "notes.md")
        self.assertEqual(result["text"], "# Notes\nhello")
        self.assertFalse(result["truncated"])

    def test_a_long_text_file_is_truncated_and_says_so(self):
        result = read_attachment(self.write("long.txt", "x" * (MAX_TEXT + 500)))
        self.assertEqual(len(result["text"]), MAX_TEXT)
        self.assertTrue(result["truncated"])

    def test_an_image_is_recognised_by_its_bytes_not_its_name(self):
        data = png_bytes()
        # Named .txt on purpose: an extension is a claim, the bytes are not.
        result = read_attachment(self.write("screenshot.txt", data))
        self.assertEqual(result["kind"], "image")
        self.assertEqual(result["type"], "image/png")
        self.assertEqual(base64.b64decode(result["image"]), data)

    def test_the_other_image_formats_are_recognised(self):
        for name, data in (("a.gif", b"GIF89a" + b"\0" * 20),
                           ("b.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 20),
                           ("c.webp", b"RIFF\0\0\0\0WEBP" + b"\0" * 20)):
            self.assertEqual(read_attachment(self.write(name, data))["kind"], "image", name)

    def test_a_binary_file_that_is_not_an_image_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            read_attachment(self.write("program.bin", b"\x00\x01\x02\xff\xfe"))
        self.assertIn("program.bin", str(caught.exception))

    # -- boundaries --------------------------------------------------------

    def test_the_file_tool_boundary_applies(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        stray = Path(outside.name) / "secret.txt"
        stray.write_text("nope")
        for path in (str(stray), "notes.md", self.write(".ssh/id_rsa", "key")):
            with self.assertRaises(ValueError, msg=path):
                read_attachment(path)

    def test_a_symlink_out_of_the_home_folder_is_refused_not_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "secret.txt"
        target.write_text("nope")
        link = self.root / "innocent.txt"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            read_attachment(str(link))

    def test_a_directory_is_not_a_file(self):
        (self.root / "folder").mkdir()
        with self.assertRaises(ValueError):
            read_attachment(str(self.root / "folder"))

    def test_an_oversized_file_is_refused_rather_than_truncated(self):
        with patch.object(attachments, "MAX_BYTES", 32):
            with self.assertRaises(ValueError) as caught:
                read_attachment(self.write("big.txt", "x" * 64))
        self.assertIn("big.txt", str(caught.exception))

    # -- batches -----------------------------------------------------------

    def test_too_many_files_are_refused_before_any_are_read(self):
        paths = [self.write(f"n{i}.txt", "x") for i in range(MAX_FILES + 1)]
        with self.assertRaises(ValueError):
            read_all(paths)

    def test_one_bad_file_takes_the_whole_batch_with_it(self):
        good = self.write("good.txt", "fine")
        with self.assertRaises(ValueError):
            read_all([good, str(self.root / "missing.txt")])

    def test_attachments_must_be_a_list_of_paths(self):
        for value in ("notes.md", [1], {"path": "x"}):
            with self.assertRaises(ValueError, msg=repr(value)):
                read_all(value)

    def test_a_context_file_that_vanished_is_skipped_and_reported(self):
        good = self.write("keep.txt", "still here")
        found, problems = read_context([good, str(self.root / "gone.txt")])
        self.assertEqual([f["name"] for f in found], ["keep.txt"])
        self.assertEqual(len(problems), 1)
        self.assertIn("gone.txt", problems[0])

    # -- what the model is handed ------------------------------------------

    def test_text_turns_carry_the_path_and_the_untrusted_framing(self):
        [message] = turns(read_all([self.write("notes.md", "hello")]))
        self.assertEqual(message["role"], "user")
        self.assertIn("untrusted data", message["content"])
        self.assertIn("notes.md", message["content"])
        self.assertIn("hello", message["content"])
        self.assertNotIn("images", message)

    def test_a_truncated_file_says_so_to_the_model_too(self):
        [message] = turns(read_all([self.write("long.txt", "x" * (MAX_TEXT + 1))]))
        self.assertIn('truncated="true"', message["content"])

    def test_images_ride_on_the_turn_rather_than_in_its_text(self):
        [message] = turns(read_all([self.write("shot.png", png_bytes())]))
        self.assertEqual(len(message["images"]), 1)
        self.assertIn("shot.png", message["content"])

    def test_always_attached_files_come_before_the_message_ones(self):
        context, _ = read_context([self.write("always.txt", "standing")])
        messages = turns(read_all([self.write("once.txt", "this time")]), context)
        self.assertEqual(len(messages), 2)
        self.assertIn("always have available", messages[0]["content"])
        self.assertIn("attached to this message", messages[1]["content"])

    def test_no_files_means_no_turns(self):
        self.assertEqual(turns([], []), [])


if __name__ == "__main__":
    unittest.main()
