"""Pixel regressions for the isolated compositor's synthetic widget scene."""
import json
from pathlib import Path
import sys

from PIL import Image, ImageChops

artifacts = Path(sys.argv[1])
result = json.loads((artifacts / "result.json").read_text())
assert result["ok"], result


def identical(first, second, rectangle, message):
    a = Image.open(artifacts / f"{first}.png").convert("RGB").crop(rectangle)
    b = Image.open(artifacts / f"{second}.png").convert("RGB").crop(rectangle)
    difference = ImageChops.difference(a, b)
    assert difference.getbbox() is None, f"{message}: changed bounds {difference.getbbox()}"


identical("before-window", "after-window", (80, 100, 376, 430),
          "Window movement left pixels on the news widget")
identical("covered-window", "typed-window", (80, 100, 255, 430),
          "Typing repainted the uncovered part of the news widget")
identical("before-window", "covered-window", (80, 100, 255, 430),
          "Covering a widget changed its exposed surface")
x, y, width, height = result["batteryRect"]
identical("after-window", "network-refresh", tuple(round(v) for v in (x, y, x + width, y + height)),
          "Network update flashed the battery text")
identical("before-window", "wallpaper-only", (80, 100, 85, 105),
          "Glass escaped the rounded top-left corner")

log = (artifacts / "shell.log").read_text()
for failure in ("Clutter-CRITICAL", "St-CRITICAL", "JS ERROR", "has been already disposed", "needs an allocation",
                "desktop-forge: wallpaper glass unavailable", "desktop-forge: native glass blur unavailable",
                "cogl_texture_2d_new_with_size", "Failed to compile", "*** BUG ***"):
    assert failure not in log, f"Shell log contains {failure}"
print("Pixel comparisons and Shell rendering/lifecycle checks passed.")
