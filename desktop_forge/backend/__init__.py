"""Backend helpers with no GTK dependency, plus one shared UI utility."""
from gi.repository import GLib


def markup(text: str) -> str:
    """Escape text destined for an Adw row title/subtitle or a toast.

    Those all render Pango markup, so an ampersand in a file path or a shell
    command (``dnf install a && b``) raises a parse error and the label falls
    back to empty. Anything not written as a literal in the source gets escaped.
    """
    return GLib.markup_escape_text(str(text))
