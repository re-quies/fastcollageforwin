# -*- coding: utf-8 -*-
"""Layout-independent keyboard matching for the Z / C / R / X modes.

The old code compared event.text() with a hardcoded pair of letters
(latin + cyrillic), so the modes were dead for anyone typing on a
spanish, german, french or arabic layout.  A key press is now matched by
three independent signals:

1. the native scan code of the key (its physical position),
2. the Qt key code (what the active layout says the key produces),
3. the produced text, compared with known layout aliases.

Any one of them is enough, so both "the key where Z sits on a QWERTY
board" and "the key labelled Z on this layout" work.
"""

import logging
import sys

try:  # Qt is unavailable in headless test runs
    from PySide6.QtCore import Qt
except ImportError:  # pragma: no cover
    Qt = None

logger = logging.getLogger(__name__)

MODE_KEYS = ("z", "c", "r", "x")

# Windows scan codes (set 1) for the physical keys of a QWERTY board.
WINDOWS_SCAN_CODES = {"z": 0x2C, "x": 0x2D, "c": 0x2E, "r": 0x13}
# X11 keycodes are the evdev codes shifted by 8.
X11_SCAN_CODES = {key: code + 8 for key, code in WINDOWS_SCAN_CODES.items()}
# macOS uses its own virtual key codes.
MAC_SCAN_CODES = {"z": 0x06, "x": 0x07, "c": 0x08, "r": 0x0F}

# Letters produced by those physical keys on non-latin layouts.
TEXT_ALIASES = {
    "z": ("z", "\u044f", "\u0626"),
    "x": ("x", "\u0447", "\u0621"),
    "c": ("c", "\u0441", "\u0624"),
    "r": ("r", "\u043a", "\u0642"),
}


def scan_codes_for_platform(platform=None):
    """Scan codes for one platform only: mixing the tables misfires."""
    name = platform if platform is not None else sys.platform
    if name.startswith("win"):
        return dict(WINDOWS_SCAN_CODES)
    if name.startswith("linux") or "bsd" in name:
        return dict(X11_SCAN_CODES)
    if name == "darwin":
        return dict(MAC_SCAN_CODES)
    logger.info("keymap: unknown platform %r, scan codes disabled", name)
    return {}


SCAN_CODES = scan_codes_for_platform()


def _qt_keys():
    if Qt is None:
        return {}
    keys = {}
    for letter in MODE_KEYS:
        value = getattr(Qt, "Key_" + letter.upper(), None)
        if value is not None:
            keys[letter] = value
    return keys


_QT_KEYS = _qt_keys()


def matches_scan_code(scan_code, letter, codes=None):
    """True when the scan code belongs to the physical `letter` key."""
    table = SCAN_CODES if codes is None else codes
    expected = table.get(letter.lower())
    return expected is not None and scan_code == expected


def matches_text(text, letter):
    """True when the typed text is a known alias of `letter`."""
    if not text:
        return False
    return text.lower() in TEXT_ALIASES.get(letter.lower(), ())


def _scan_code(event):
    getter = getattr(event, "nativeScanCode", None)
    if getter is None:
        return None
    try:
        return int(getter())
    except Exception:
        return None


def _matches_qt_key(event, letter):
    expected = _QT_KEYS.get(letter)
    getter = getattr(event, "key", None)
    if expected is None or getter is None:
        return False
    try:
        return getter() == expected
    except Exception:
        return False


def _text(event):
    getter = getattr(event, "text", None)
    if getter is None:
        return ""
    try:
        return getter() or ""
    except Exception:
        return ""


def matches(event, letter):
    """True when `event` is the key bound to the `letter` mode."""
    letter = letter.lower()
    scan_code = _scan_code(event)
    if scan_code is not None and matches_scan_code(scan_code, letter):
        return True
    if _matches_qt_key(event, letter):
        return True
    return matches_text(_text(event), letter)
