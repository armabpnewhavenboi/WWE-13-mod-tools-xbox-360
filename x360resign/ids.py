"""Parsing and validating the IDs a save is locked to."""

import re

PROFILE_ID_SIZE = 8
CONSOLE_ID_SIZE = 5
DEVICE_ID_SIZE = 0x14


class IdError(ValueError):
    pass


def _clean(text):
    text = text.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    return re.sub(r"[\s\-:]", "", text)


def parse_hex_id(text, size, label):
    cleaned = _clean(text)
    if not cleaned:
        raise IdError("%s is empty" % label)
    if not re.fullmatch(r"[0-9a-fA-F]+", cleaned):
        raise IdError("%s must be hexadecimal (0-9, A-F): %r" % (label, text))
    if len(cleaned) != size * 2:
        raise IdError("%s must be %d hex characters, got %d (%r)"
                      % (label, size * 2, len(cleaned), text))
    return bytes.fromhex(cleaned)


def parse_profile_id(text):
    return parse_hex_id(text, PROFILE_ID_SIZE, "Profile ID")


def parse_console_id(text):
    return parse_hex_id(text, CONSOLE_ID_SIZE, "Console ID")


def parse_device_id(text):
    return parse_hex_id(text, DEVICE_ID_SIZE, "Device ID")


def profile_id_warning(profile_id):
    """Return a human-readable warning for odd-looking profile IDs, or ''."""
    if not any(profile_id):
        return "Profile ID is all zeros - that is the shared/no-profile ID, not a gamer profile."
    first = profile_id[0]
    if first == 0xE0 or (first >> 4) == 0xE:
        return ""  # offline / local profile (E0000...)
    if profile_id[:2] == b"\x00\x09":
        return ""  # Xbox LIVE-enabled profile (0009...)
    return ("Profile ID %s does not look like an Xbox 360 profile (they normally start with "
            "E0 or 0009). Double-check it." % profile_id.hex().upper())


def fmt(value):
    return value.hex().upper() if value else "-"
