"""Desktop customization: the settings registry, backends, profiles and presets."""
from .backends import DESKTOP_PATH, Unavailable
from .customizer import Customizer, PreviewSession
from .registry import BY_ID, SECTIONS, SETTINGS, Setting, validate

__all__ = ["BY_ID", "DESKTOP_PATH", "SECTIONS", "SETTINGS", "Customizer", "PreviewSession", "Setting",
           "Unavailable", "validate"]
