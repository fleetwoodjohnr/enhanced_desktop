"""Probe the Shell window bridge over its real session-bus interface."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_forge.clive.shell import ShellBridge


windows = ShellBridge().windows()
if not any(row.get("title") == "DF repaint test" and row.get("desktop_id") for row in windows):
    raise SystemExit("the synthetic application was missing from the Shell bridge")
print(json.dumps(windows), flush=True)
