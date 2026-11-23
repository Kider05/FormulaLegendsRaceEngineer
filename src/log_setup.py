"""log_setup.py -- logging for the app: console plus a rotating file in logs/."""

import logging
from logging.handlers import RotatingFileHandler

from paths import PROJECT_DIR

LOG_FILE = PROJECT_DIR / "logs" / "race_engineer.log"
QUIET_LIBRARIES = ("httpx", "httpx2", "httpcore", "huggingface_hub", "faster_whisper", "urllib3")


def setup(debug=False):
    LOG_FILE.parent.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=[console, file_handler], force=True)
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
