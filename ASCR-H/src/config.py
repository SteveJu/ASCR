"""Configuration loader for ascr_h."""
import os
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

_cfg = None

def load() -> dict:
    global _cfg
    if _cfg is None:
        with open(CONFIG_PATH) as f:
            _cfg = yaml.safe_load(f)
    return _cfg

def db_path() -> str:
    override = os.environ.get("ASCR_H_DB_PATH")
    if override:
        os.makedirs(os.path.dirname(os.path.abspath(override)), exist_ok=True)
        return override
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "ascr_h.sqlite")

def radar_db_path() -> str:
    override = os.environ.get("ASCR_DB_PATH")
    if override:
        return override
    cfg = load()
    return cfg.get("ascr", cfg.get("ascr", {})).get("db_path", "../ASCR/data/ascr.sqlite")
