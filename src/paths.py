"""paths.py -- the project folder, so every module finds data files the same way wherever it lives in src/."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
