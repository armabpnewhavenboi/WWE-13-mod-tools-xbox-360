"""Remembered settings (x360resign.ini) so the IDs only have to be typed once."""

import configparser
import os
import sys

CONFIG_NAME = "x360resign.ini"
KEYS = ("profile_id", "console_id", "device_id", "kv", "cpu_key", "input", "output")


def default_config_path():
    """Next to the .exe when frozen, otherwise the current directory."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), CONFIG_NAME)
    return os.path.join(os.getcwd(), CONFIG_NAME)


def load_config(path=None):
    path = path or default_config_path()
    values = {}
    if not os.path.isfile(path):
        return values
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if parser.has_section("x360resign"):
        for key in KEYS:
            value = parser.get("x360resign", key, fallback="").strip()
            if value:
                values[key] = value
    return values


def save_config(values, path=None):
    path = path or default_config_path()
    parser = configparser.ConfigParser()
    parser["x360resign"] = {k: str(values.get(k, "") or "") for k in KEYS}
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)
    return path
