"""Universe Pruner — automatically remove tickers that no longer belong.

Triggers for removal:
1. D-Tier for 30+ scoring days
2. Zero events for 60+ days (lost relevance)
3. Delisted or market cap < $500M
4. Negative sentiment accumulation (multiple SELL/AVOID verdicts)
5. Sector heat death (entire sector losing momentum)

Safety:
- Never remove tickers with open positions (sell first)
- Requires 2+ triggers to remove (no single-signal kicks)
- Logs all removals for review
- Can be overridden via /keep command
"""
import os
import yaml
from datetime import datetime, timedelta
from src import config, db
from src.utils import get_logger

logger = get_logger("universe_pruner")

UNIVERSE_FILE = os.path.join(config.BASE_DIR, "config", "universe.yaml")

# Thresholds
D_TIER_DAYS = 30          # D-rated for this many scoring days → removal-eligible trigger
D_TIER_MIN_SCORING_DAYS = 15  # minimum sample before D-tier can even enter watch
NO_EVENT_DAYS = 60        # No events for this long → trigger
MIN_MARKET_CAP = 500_000_000  # $500M floor
NEGATIVE_VERDICT_THRESHOLD = 3  # 3+ SELL/AVOID in 30 days → trigger if ratio also high
NEGATIVE_VERDICT_MIN_RATIO = 0.20  # avoid flagging high-volume names for a few bearish events
SECTOR_HEAT_DEATH_PCT = 0.7    # 70% of sector declining → flag sector

# Protected tickers (never auto-remove)
PROTECTED = {"QQQ", "SPY"}


def _get_open_positions() -> set:
    """Get tickers with open paper trading positions."""
    try:
        import sqlite3
        pt_db = os.environ.get("ASCR_H_DB_PATH", os.path.join(os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H"), "data", "ascr_h.sqlite"))
        conn = sqlite3.connect(pt_db)
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM paper_positions WHERE status='open'"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _get_kept_tickers() -> set:
    """Get tickers manually marked as 'keep' (override auto-removal)."""
    try:
        with open(UNIVERSE_FILE) as f:
            u = yaml.safe_load(f)
        return {str(t).upper() for t in u.get("keep", [])}
    except Exception:
        return set()


def _get_excluded_from_trading() -> set:
    """Get tickers tracked for intelligence only; they should not be auto-pruned."""
    try:
        excluded = config.universe().get("excluded_from_trading", {})
        if isinstance(excluded, dict):
            tickers = excluded.get("tickers", [])
        elif isinstance(excluded, list):
            tickers = excluded
        else:
            tickers = []
        return {str(t).upper() for t in tickers}
    except Exception:
        return set()


def check_d_tier(ticker: str, conn) -> dict | None:
    """Check if ticker has been D-rated across enough recent scoring days."""
    rows = conn.execute(
        "SELECT date, rating FROM scores WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, D_TIER_DAYS)
    ).fetchall()

    if len(rows) < D_TIER_MIN_SCORING_DAYS:  # Need enough data
        return None

    d_count = sum(1 for r in rows if r[1] == "D")
    if d_count >= len(rows) * 0.8:  # 80%+ D-rated
        removal_eligible = len(rows) >= D_TIER_DAYS
        return {
            "trigger": "d_tier",
            "detail": f"D-rated {d_count}/{len(rows)} of last scoring days",
            "severity": "medium" if removal_eligible else "low",
            "removal_eligible": removal_eligible,
        }
    return None


def check_no_events(ticker: str, conn) -> dict | None:
    """Check if ticker has had zero events for 60+ days."""
    cutoff = (datetime.now() - timedelta(days=NO_EVENT_DAYS)).strftime("%Y-%m-%d")
    count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE ticker=? AND date >= ?",
        (ticker, cutoff)
    ).fetchone()[0]

    if count == 0:
        return {
            "trigger": "no_events",
            "detail": f"Zero events in last {NO_EVENT_DAYS} days",
            "severity": "low",
        }
    return None


def _has_recent_local_price(ticker: str, conn, days: int = 10) -> bool:
    """Return True if local price history has a recent positive close."""
    if conn is None:
        return False
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM prices WHERE ticker=? AND date >= ? AND close > 0",
            (ticker, cutoff)
        ).fetchone()[0]
        return count > 0
    except Exception:
        return False


def check_delisted(ticker: str, conn=None) -> dict | None:
    """Check if ticker is delisted or below market cap floor."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info

        # Check if stock still trades
        price = info.get("regularMarketPrice") or info.get("previousClose", 0)
        if not price or price <= 0:
            if _has_recent_local_price(ticker, conn):
                return None
            return {
                "trigger": "delisted",
                "detail": "No market price available and no recent local price — possibly delisted",
                "severity": "critical",
            }

        # Check market cap
        mcap = info.get("marketCap", 0) or 0
        if 0 < mcap < MIN_MARKET_CAP:
            return {
                "trigger": "low_mcap",
                "detail": f"Market cap ${mcap/1e6:.0f}M < ${MIN_MARKET_CAP/1e6:.0f}M floor",
                "severity": "medium",
            }
    except Exception:
        pass
    return None


def check_negative_sentiment(ticker: str, conn) -> dict | None:
    """Check for accumulating negative verdicts (SELL/AVOID) in recent events."""
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT verdict, conviction FROM events WHERE ticker=? AND date >= ? AND verdict IS NOT NULL",
        (ticker, cutoff)
    ).fetchall()

    if not rows:
        return None

    negative = [r for r in rows if r[0] in ("SELL", "AVOID")]
    negative_ratio = len(negative) / len(rows)

    if (
        len(negative) >= NEGATIVE_VERDICT_THRESHOLD
        and negative_ratio >= NEGATIVE_VERDICT_MIN_RATIO
    ):
        return {
            "trigger": "negative_sentiment",
            "detail": (
                f"{len(negative)}/{len(rows)} events "
                f"({negative_ratio:.0%}) are SELL/AVOID in last 30d"
            ),
            "severity": "high",
        }

    # Also check if ALL recent events are bearish (even if < threshold count)
    if len(rows) >= 2 and all(r[0] in ("SELL", "AVOID") for r in rows):
        return {
            "trigger": "all_negative",
            "detail": f"All {len(rows)} recent events are bearish",
            "severity": "medium",
        }
    return None


def check_sector_heat_death(conn) -> dict:
    """Check if entire sectors are losing heat.
    Returns {sector: {declining_pct, tickers, status}}
    """
    universe = config.universe()
    sectors = {}

    cutoff_30d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cutoff_60d = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    for sname, sval in universe.get("sectors", {}).items():
        if not isinstance(sval, dict):
            continue
        tickers = sval.get("tickers", [])
        if not tickers:
            continue

        declining = 0
        for t in tickers:
            # Check: zero events in 30d AND price declining
            ev_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE ticker=? AND date >= ?",
                (t, cutoff_30d)
            ).fetchone()[0]

            prices = conn.execute(
                "SELECT close FROM prices WHERE ticker=? AND date >= ? ORDER BY date",
                (t, cutoff_60d)
            ).fetchall()

            if prices and len(prices) >= 10:
                start_price = float(prices[0][0])
                end_price = float(prices[-1][0])
                if end_price < start_price * 0.95 and ev_count == 0:
                    declining += 1

        pct = declining / len(tickers) if tickers else 0
        status = "dead" if pct >= SECTOR_HEAT_DEATH_PCT else "weak" if pct >= 0.5 else "healthy"
        sectors[sname] = {
            "declining_pct": round(pct * 100),
            "declining": declining,
            "total": len(tickers),
            "status": status,
        }

    return sectors


def evaluate_all() -> dict:
    """Run all pruning checks on entire universe.

    Returns {
        "remove": [{ticker, triggers, reason}],
        "watch": [{ticker, triggers, reason}],
        "sector_health": {sector: status},
        "protected": [tickers with positions],
    }
    """
    tickers = config.all_tickers()
    open_positions = _get_open_positions()
    kept = _get_kept_tickers()
    excluded = _get_excluded_from_trading()
    exempt = PROTECTED | kept | excluded

    with db.get_conn() as conn:
        remove = []
        watch = []

        for ticker in tickers:
            if ticker in exempt:
                continue

            triggers = []

            # Run all checks
            t1 = check_d_tier(ticker, conn)
            if t1:
                triggers.append(t1)

            t2 = check_no_events(ticker, conn)
            if t2:
                triggers.append(t2)

            t3 = check_negative_sentiment(ticker, conn)
            if t3:
                triggers.append(t3)

            # Skip yfinance check for speed (only check if already has triggers)
            if triggers:
                t4 = check_delisted(ticker, conn)
                if t4:
                    triggers.append(t4)

            if not triggers:
                continue

            has_position = ticker in open_positions
            removal_triggers = [t for t in triggers if t.get("removal_eligible", True)]
            has_critical = any(t["severity"] == "critical" for t in removal_triggers)

            reason = "; ".join(t["detail"] for t in triggers)

            entry = {
                "ticker": ticker,
                "triggers": triggers,
                "trigger_count": len(triggers),
                "removal_trigger_count": len(removal_triggers),
                "reason": reason,
                "has_position": has_position,
            }

            # 2+ triggers OR 1 critical → recommend removal
            if len(removal_triggers) >= 2 or has_critical:
                if has_position:
                    entry["action"] = "sell_then_remove"
                    watch.append(entry)  # Can't remove yet, has position
                else:
                    remove.append(entry)
            else:
                watch.append(entry)

        # Sector health
        sector_health = check_sector_heat_death(conn)

    result = {
        "remove": sorted(remove, key=lambda x: -x["trigger_count"]),
        "watch": sorted(watch, key=lambda x: -x["trigger_count"]),
        "sector_health": sector_health,
        "protected_positions": sorted(open_positions),
        "exempt_tickers": sorted(exempt & set(tickers)),
    }

    if remove:
        logger.info(f"Pruner recommends removing {len(remove)} tickers: "
                     f"{', '.join(r['ticker'] for r in remove)}")

    return result


def execute_removal(ticker: str, reason: str) -> bool:
    """Actually remove a ticker from universe.yaml.

    Returns True if removed successfully.
    """
    with open(UNIVERSE_FILE) as f:
        universe = yaml.safe_load(f)

    removed = False
    for sname, sval in universe.get("sectors", {}).items():
        if isinstance(sval, dict) and ticker in sval.get("tickers", []):
            sval["tickers"].remove(ticker)
            removed = True
            break

    if removed:
        # Track removal history
        if "removed" not in universe:
            universe["removed"] = []
        universe["removed"].append({
            "ticker": ticker,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "reason": reason,
        })

        with open(UNIVERSE_FILE, "w") as f:
            yaml.dump(universe, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Removed {ticker} from universe: {reason}")

    return removed


def auto_prune(dry_run: bool = True) -> dict:
    """Run evaluation and optionally execute removals.

    dry_run=True: only report, don't remove
    dry_run=False: actually remove tickers (use with caution)
    """
    result = evaluate_all()

    if not dry_run:
        for entry in result["remove"]:
            execute_removal(entry["ticker"], entry["reason"])

    return result


def format_telegram_report(result: dict = None) -> str:
    """Format pruner results for Telegram."""
    if result is None:
        result = evaluate_all()

    lines = ["🔍 **Universe Health Check**\n"]

    # Removals
    if result["remove"]:
        lines.append(f"🔴 **Recommend Removal** ({len(result['remove'])})")
        for r in result["remove"]:
            triggers = " + ".join(t["trigger"] for t in r["triggers"])
            lines.append(f"  `{r['ticker']}`: {triggers}")
            lines.append(f"    → {r['reason']}")

    # Watch list
    if result["watch"]:
        lines.append(f"\n🟡 **Watch List** ({len(result['watch'])})")
        for w in result["watch"]:
            triggers = " + ".join(t["trigger"] for t in w["triggers"])
            pos = " [📌 has position]" if w.get("has_position") else ""
            action = f" → {w['action']}" if w.get("action") else ""
            lines.append(f"  `{w['ticker']}`: {triggers}{pos}{action}")

    # Sector health
    sick_sectors = {k: v for k, v in result["sector_health"].items()
                    if v["status"] != "healthy"}
    if sick_sectors:
        lines.append(f"\n⚠️ **Sector Health**")
        for sector, data in sick_sectors.items():
            emoji = "💀" if data["status"] == "dead" else "🟡"
            lines.append(f"  {emoji} {sector}: {data['declining']}/{data['total']} declining ({data['declining_pct']}%)")

    # Protected
    if result["protected_positions"]:
        lines.append(f"\n📌 Protected (open positions): {', '.join(result['protected_positions'])}")

    if not result["remove"] and not result["watch"] and not sick_sectors:
        lines.append("✅ Universe is healthy — no removals needed")

    lines.append(f"\nTickers: {len(config.all_tickers())} | /prune execute to remove")

    return "\n".join(lines)
