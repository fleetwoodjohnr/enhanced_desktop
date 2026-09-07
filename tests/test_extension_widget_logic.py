from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class ExtensionWidgetLogicTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gjs"), "GJS is not installed")
    def test_widget_layout_paging_meters_and_layer_policy(self) -> None:
        script = Path(__file__).with_name("extension_widget_logic_test.js")
        result = subprocess.run(
            ["gjs", "-m", str(script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
