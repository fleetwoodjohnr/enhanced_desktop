"""JSON-schema fragments shared by tool definitions."""

STRING = {"type": "string", "minLength": 1, "maxLength": 2000}
TEXT = {"type": "string", "maxLength": 50000}
INPUT_TEXT = {"type": "string", "minLength": 1, "maxLength": 8000}
COORD = {"type": "number", "minimum": 0, "maximum": 1000}
NAVIGATION_KEYS = ["Tab", "Return", "Escape", "BackSpace", "Delete", "Home", "End",
                   "PageUp", "PageDown", "Left", "Right", "Up", "Down"]
SHORTCUT_KEY = {"anyOf": [
    {"type": "string", "enum": NAVIGATION_KEYS},
    {"type": "string", "minLength": 1, "maxLength": 1},
]}
