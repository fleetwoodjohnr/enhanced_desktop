from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ExtensionStoreTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gjs"), "GJS is not installed")
    def test_reminder_and_todo_desktop_writes(self) -> None:
        script = Path(__file__).with_name("extension_store_test.js")
        with tempfile.TemporaryDirectory() as data_home:
            environment = dict(os.environ)
            environment["XDG_DATA_HOME"] = data_home
            result = subprocess.run(
                ["gjs", "-m", str(script)],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
