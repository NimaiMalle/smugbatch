"""Load and save ~/.smugbatch/config.yaml."""

from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".smugbatch"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Config not found: {CONFIG_PATH}\nCreate it first.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
