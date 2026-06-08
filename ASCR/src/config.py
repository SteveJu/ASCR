"""Configuration loader for ascr."""
import os
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

def load_yaml(name: str) -> dict:
    path = os.path.join(CONFIG_DIR, name)
    with open(path) as f:
        return yaml.safe_load(f)

def universe() -> dict:
    return load_yaml("universe.yaml")

def scoring() -> dict:
    return load_yaml("scoring.yaml")

def models() -> dict:
    return load_yaml("models.yaml")

def chokepoints() -> dict:
    return load_yaml("chokepoints.yaml")

def frontier_domains() -> dict:
    return load_yaml("frontier_domains.yaml")

def news_sources() -> dict:
    return load_yaml("news_sources.yaml")

def telegram() -> dict:
    return load_yaml("telegram.yaml")

def all_tickers(include_benchmarks=False) -> list:
    """Return flat list of all tickers in universe."""
    u = universe()
    tickers = []
    for sector in u.get("sectors", {}).values():
        if isinstance(sector, dict):
            tickers.extend(sector.get("tickers", []))
        elif isinstance(sector, list):
            tickers.extend(sector)
    if include_benchmarks:
        benchmarks = u.get("benchmarks", {})
        if isinstance(benchmarks, dict):
            tickers.extend(benchmarks.get("tickers", []))
    return sorted(set(tickers))

def discovery_keywords() -> list:
    return universe().get("discovery_keywords", [])

def db_path() -> str:
    override = os.environ.get("ASCR_DB_PATH")
    if override:
        os.makedirs(os.path.dirname(os.path.abspath(override)), exist_ok=True)
        return override
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "ascr.sqlite")


def assert_universe_sync():
    """Ensure tickers table matches universe.yaml."""
    from src import db
    from datetime import datetime
    tickers = all_tickers()
    now = datetime.now().isoformat()
    with db.get_conn() as conn:
        existing = {r[0] for r in conn.execute("SELECT ticker FROM tickers").fetchall()}
        missing = set(tickers) - existing
        if missing:
            for t in missing:
                conn.execute(
                    "INSERT OR IGNORE INTO tickers (ticker, status, tracking_priority, added_at, updated_at) VALUES (?, 'active', 'normal', ?, ?)",
                    (t, now, now))
            conn.commit()
