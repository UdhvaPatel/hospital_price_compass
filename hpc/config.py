"""Project paths and config loading."""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
REFERENCE_DIR = DATA_DIR / "reference"
PHASE0_DIR = DATA_DIR / "phase0"


def load_toml(name: str) -> dict:
    with open(CONFIG_DIR / name, "rb") as f:
        return tomllib.load(f)


def metro_config() -> dict:
    return load_toml("metro.toml")


def sites_config() -> dict:
    return load_toml("hospital_sites.toml")
