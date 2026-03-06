"""Settings management for Constellation V3."""

import os
import shutil
import yaml

DEFAULT_CONFIG = {
    'source': {
        'path': '',
        'provider': 'claude',
    },
    'embedding': {
        'model': 'all-MiniLM-L6-v2',
    },
    'server': {
        'port': 8420,
        'host': '127.0.0.1',
    },
    'display': {
        'auto_rotate': True,
        'colorblind_mode': False,
        'layout': '3d-force',
    },
    'mcp': {
        'enabled': True,
    },
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.yaml')
CONFIG_EXAMPLE_PATH = os.path.join(PROJECT_ROOT, 'config.yaml.example')
DATA_DIR = os.environ.get('CONSTELLATION_DATA_DIR', os.path.join(PROJECT_ROOT, 'data'))


def ensure_config() -> dict:
    """Load config.yaml, creating from example if needed."""
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(CONFIG_EXAMPLE_PATH):
            shutil.copy2(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
            print("Created config.yaml from config.yaml.example")
        else:
            return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f) or {}

    # Merge with defaults
    merged = DEFAULT_CONFIG.copy()
    for section, values in config.items():
        if section in merged and isinstance(merged[section], dict) and isinstance(values, dict):
            merged[section].update(values)
        else:
            merged[section] = values
    return merged


def save_config(config: dict):
    """Write config back to config.yaml."""
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
