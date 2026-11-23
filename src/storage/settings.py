"""settings.py -- user settings saved as JSON in config/settings.json."""

import json

from paths import PROJECT_DIR

SETTINGS_PATH = PROJECT_DIR / "config" / "settings.json"
DEFAULTS = {"voice_on": True, "voice_volume": 80, "beep_on": True, "beep_volume": 80}


def load():
    """Saved settings over DEFAULTS; a missing or broken file gives DEFAULTS."""
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    return {k: saved.get(k, v) for k, v in DEFAULTS.items()}


def save(values):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(values, indent=2))
