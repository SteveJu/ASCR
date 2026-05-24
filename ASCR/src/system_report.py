"""System architecture report generator.

Produces a comprehensive technical overview of both ASCR and ASCR-H
for sharing with other engineers/agents for review.
"""
import os
import json
import sqlite3
from datetime import datetime, timedelta

from src import db, config
from src.utils import get_logger

logger = get_logger("system_report")


def _db_stats(path):
    """Get table row counts and size for a SQLite DB."""
    if not os.path.exists(path):
        return {}
    size_mb = os.path.getsize(path) / 1e6
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    tables = {}
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cnt = conn.execute(f"SELECT COUNT(*) FROM {r[0]}").fetchone()[0]
        tables[r[0]] = cnt
    conn.close()
    return {"size_mb": round(size_mb, 2), "tables": tables}


def _launchd_jobs():
    """Parse launchd plists for schedule info."""
    import subprocess
    jobs = []
    la_dir = os.path.expanduser("~/Library/LaunchAgents")
    for fname in os.listdir(la_dir):
        if not (fname.startswith("com.ASCR") or
                fname.startswith("com.ascr") or
                fname.startswith("com.ASCR-H")):
            continue
        path = os.path.join(la_dir, fname)
        try:
            raw = subprocess.check_output(["plutil", "-convert", "json", "-o", "-", path])
            plist = json.loads(raw)
            if isinstance(plist, list):
                # plutil extracted array (StartCalendarInterval) — read manually
                with open(path) as f:
                    txt = f.read()
                label = fname.replace(".plist", "")
                workdir = ""
                if "<string>${ASCR_PROJECT_DIR}" in txt or "ascr" in fname:
                    workdir = "ASCR"
                else:
                    workdir = "ASCR-H"
                keep = "<key>KeepAlive</key>" in txt and "<true/>" in txt
                if keep:
                    schedule = "Always On (KeepAlive)"
                else:
                    intervals = plist if isinstance(plist, list) else [plist]
                    times = set()
                    weekdays_only = False
                    for iv in intervals:
                        h = iv.get("Hour", "?")
                        m = iv.get("Minute", 0)
                        if "Weekday" in iv:
                            weekdays_only = True
                        times.add("{:d}:{:02d}".format(h, m) if isinstance(m, int) else "{}:{}".format(h, m))
                    prefix = "Mon-Fri" if weekdays_only else "Daily"
                    schedule = "{} {}".format(prefix, ", ".join(sorted(times)))
                project = workdir
            else:
                label = plist.get("Label", fname)
                workdir = plist.get("WorkingDirectory", "")
                project = "ASCR" if "ASCR" in workdir or "ascr" in label else "ASCR-H"

                if plist.get("KeepAlive"):
                    schedule = "Always On (KeepAlive)"
                else:
                    intervals = plist.get("StartCalendarInterval", [])
                    if isinstance(intervals, dict):
                        intervals = [intervals]
                    times = set()
                    weekdays_only = False
                    for iv in intervals:
                        h = iv.get("Hour", "?")
                        m = iv.get("Minute", 0)
                        if "Weekday" in iv:
                            weekdays_only = True
                        times.add("{:d}:{:02d}".format(h, m) if isinstance(m, int) else "{}:{}".format(h, m))
                    prefix = "Mon-Fri" if weekdays_only else "Daily"
                    schedule = "{} {}".format(prefix, ", ".join(sorted(times)))

            jobs.append({"label": label, "project": project, "schedule": schedule})
        except Exception as e:
            jobs.append({"label": fname, "project": "?", "schedule": "error"})

    return sorted(jobs, key=lambda x: x["label"])


def generate_system_report() -> str:
    """Generate full system architecture report."""
    now = datetime.now()

    # ── Universe ──
    tickers = config.all_tickers()
    try:
        import yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config", "universe.yaml")) as f:
            u = yaml.safe_load(f)
        sectors = {}
        for sname, sval in u.get("sectors", {}).items():
            if isinstance(sval, dict):
                sectors[sname] = sval.get("tickers", [])
    except Exception:
        sectors = {}

    # ── DB Stats ──
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    radar_db = _db_stats(os.path.join(base, "data", "ascr.sqlite"))
    exp_db = _db_stats(os.path.join(base, "data", "experience.sqlite"))
    activity_db = _db_stats(os.path.join(base, "data", "activity_log.sqlite"))
    paper_db = _db_stats(os.environ.get("ASCR_H_DB_PATH", os.path.join(os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H"), "data", "ascr_h.sqlite")))

    # ── Positions ──
    try:
        import sqlite3 as s3
        pconn = s3.connect(os.environ.get("ASCR_H_DB_PATH", os.path.join(os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H"), "data", "ascr_h.sqlite")))
        pconn.row_factory = s3.Row
        positions = pconn.execute(
            "SELECT * FROM paper_positions WHERE status='open'"
        ).fetchall()
        account = pconn.execute("SELECT * FROM account LIMIT 1").fetchone()
        pconn.close()
        cash = account["cash"] if account else 0
        pos_list = [dict(p) for p in positions]
    except Exception:
        cash = 0
        pos_list = []

    # ── Experience stats ──
    exp_stats = {}
    try:
        econn = sqlite3.connect(os.path.join(base, "data", "experience.sqlite"))
        econn.row_factory = sqlite3.Row
        r = econn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN signal_accurate IS NOT NULL THEN 1 ELSE 0 END) as evaluated,
                   SUM(CASE WHEN signal_accurate=1 THEN 1 ELSE 0 END) as accurate,
                   SUM(CASE WHEN return_5d IS NOT NULL THEN 1 ELSE 0 END) as has_5d
            FROM signal_outcomes
        """).fetchone()
        exp_stats = dict(r)
        econn.close()
    except Exception:
        pass

    # ── Market regime ──
    regime = "?"
    try:
        from src.market_regime import get_regime
        r = get_regime()
        regime = r.get("regime", "?")
    except Exception:
        pass

    # ── Launchd ──
    jobs = _launchd_jobs()

    # ── Event stats ──
    event_stats = {}
    try:
        with db.get_conn() as conn:
            r = conn.execute("""
                SELECT COUNT(*) as total,
                       COUNT(DISTINCT ticker) as tickers,
                       MIN(date) as earliest,
                       MAX(date) as latest
                FROM events
            """).fetchone()
            event_stats = dict(r)
    except Exception:
        pass

    # ══════════════════════════════════════
    # BUILD REPORT
    # ══════════════════════════════════════
    lines = []
    lines.append("\U0001f3d7 <b>SYSTEM ARCHITECTURE REPORT</b>")
    lines.append("Generated: {} ET\n".format(now.strftime("%Y-%m-%d %H:%M")))

    # ── Overview ──
    lines.append("<b>═══ OVERVIEW ═══</b>")
    lines.append("AI Supply Chain Event-Driven Trading System")
    lines.append("Strategy: Event signals \u2192 Position management")
    lines.append("Bankroll: $10,000 (speculative, prepared to lose)")
    lines.append("Positions: {} of 10 slots | Cash: ${:,.0f}".format(len(pos_list), cash))
    lines.append("Market regime: {}".format(regime))
    lines.append("")

    # ── Architecture ──
    lines.append("<b>═══ ARCHITECTURE ═══</b>")
    lines.append("<b>ASCR</b> (Brain) \u2192 all analysis + decisions")
    lines.append("  \u251c Discovery Engine \u2014 find new stocks")
    lines.append("  \u251c Analysis Engine \u2014 analyze universe")
    lines.append("  \u251c Recommender \u2014 rank + buy/sell instructions")
    lines.append("  \u251c Experience Tracker \u2014 learn from outcomes")
    lines.append("  \u2514 Bubble Detector \u2014 3-level circuit breaker")
    lines.append("")
    lines.append("<b>ASCR-H</b> (Hands) \u2192 pure execution, zero judgment")
    lines.append("  \u251c Event Trader \u2014 execute radar instructions")
    lines.append("  \u251c Intraday Monitor \u2014 stop-loss / peak tracking")
    lines.append("  \u251c Trading Rules \u2014 market hours / PDT / settlement")
    lines.append("  \u2514 Regime Monitor \u2014 AI bubble kill switch")
    lines.append("")

    # ── Data Pipeline ──
    lines.append("<b>═══ DATA PIPELINE ═══</b>")
    lines.append("Sources:")
    lines.append("  \u2022 Google News RSS (41 queries)")
    lines.append("  \u2022 SEC 8-K filings (item-level parsing)")
    lines.append("  \u2022 SEC 13F (6 major funds)")
    lines.append("  \u2022 SEC Form 4 (insider trading)")
    lines.append("  \u2022 Yahoo Finance (earnings + analyst ratings)")
    lines.append("  \u2022 Reddit (WSB/stocks/investing)")
    lines.append("  \u2022 yfinance (real-time prices, 60s cache)")
    lines.append("")
    lines.append("LLM Processing:")
    lines.append("  \u2022 Filter: Haiku 4.5 (binary relevance)")
    lines.append("  \u2022 Analysis: Gemini 3.1 Flash Lite \u2192 2.5 Flash \u2192 Sonnet (fallback)")
    lines.append("  \u2022 Universe eval: Opus 4.6 (weekly)")
    lines.append("  \u2022 Monthly review: Opus 4.6 (experience tracker)")
    lines.append("  \u2022 Cap: 30 LLM calls/pipeline run")
    lines.append("")

    # ── Universe ──
    lines.append("<b>═══ UNIVERSE ({} tickers) ═══</b>".format(len(tickers)))
    for sname, stickers in sorted(sectors.items()):
        if stickers:
            lines.append("  {} ({}): {}".format(sname, len(stickers), ", ".join(stickers)))
    lines.append("")

    # ── Schedule ──
    lines.append("<b>═══ SCHEDULE ═══</b>")
    for j in jobs:
        icon = "\U0001f7e2" if "KeepAlive" in j["schedule"] else "\u23f0"
        lines.append("  {} {}: {}".format(icon, j["label"].replace("com.", ""), j["schedule"]))
    lines.append("")
    lines.append("Pipeline:")
    lines.append("  07:00 \u2192 Analysis AM (prices + scores)")
    lines.append("  12:30 \u2192 Fast scan (8-K urgent) + Intraday monitor")
    lines.append("  16:30 \u2192 Discovery Engine")
    lines.append("  17:30 \u2192 Analysis PM (full + Sonnet + report)")
    lines.append("  17:45 \u2192 ASCR-H (execute trades)")
    lines.append("  Fri 17:15 \u2192 Universe deep scan (Opus)")
    lines.append("  Fri 18:00 \u2192 Weekly report")
    lines.append("")

    # ── Positions ──
    if pos_list:
        lines.append("<b>═══ POSITIONS ═══</b>")
        for p in pos_list:
            lines.append("  {} {:.1f}sh @ ${:.2f}".format(
                p["ticker"], p["quantity"], p["avg_entry_price"]))
        lines.append("  Cash: ${:,.0f} | {}/10 slots".format(cash, len(pos_list)))
        lines.append("")

    # ── Trading Rules ──
    lines.append("<b>═══ TRADING RULES ═══</b>")
    lines.append("  \u2022 10 positions \u00d7 10% ($1K each)")
    lines.append("  \u2022 Buy: event signal required (ev_score \u2265 5)")
    lines.append("  \u2022 Rank: verdict_score \u2192 ev_score \u2192 evidence")
    lines.append("  \u2022 Hard stop: -20%")
    lines.append("  \u2022 Trailing stop: -25% from peak")
    lines.append("  \u2022 Auto-rotation: held stock not in top N \u2192 sell")
    lines.append("  \u2022 Market hours: Mon-Fri 9:30-16:00 ET")
    lines.append("  \u2022 PDT: no day trades if <$25K")
    lines.append("  \u2022 T+1 settlement enforced")
    lines.append("  \u2022 NO auto-trading / NO broker connection")
    lines.append("")

    # ── Bubble Protection ──
    lines.append("<b>═══ BUBBLE PROTECTION ═══</b>")
    lines.append("  \u26a0\ufe0f WARNING: >80% down + >50% drop >5%")
    lines.append("  \U0001f534 DANGER: >90% down + >60% drop >8%")
    lines.append("  \U0001f480 MELTDOWN: >95% down + >80% drop >10% \u2192 auto-liquidate")
    lines.append("")

    # ── Experience Tracker ──
    lines.append("<b>═══ EXPERIENCE TRACKER ═══</b>")
    if exp_stats:
        lines.append("  Signals: {} total | {} with 5d data | {} evaluated".format(
            exp_stats.get("total", 0), exp_stats.get("has_5d", 0),
            exp_stats.get("evaluated", 0)))
        if exp_stats.get("evaluated"):
            acc = (exp_stats.get("accurate", 0) / exp_stats["evaluated"]) * 100
            lines.append("  Accuracy: {:.0f}% ({}/{})".format(
                acc, exp_stats.get("accurate", 0), exp_stats["evaluated"]))
    else:
        lines.append("  Accumulating data (need 20 days)")
    lines.append("  Components: Signal Accuracy + Pattern Library + Monthly Opus Review")
    lines.append("  Rule: system suggests, human approves")
    lines.append("")

    # ── Databases ──
    lines.append("<b>═══ DATABASES ═══</b>")
    for name, stats in [("ascr", radar_db), ("experience", exp_db),
                        ("activity_log", activity_db), ("ascr_h", paper_db)]:
        if stats:
            top_tables = sorted(stats["tables"].items(), key=lambda x: x[1], reverse=True)[:5]
            tables_str = ", ".join(f"{t[0]}({t[1]})" for t in top_tables)
            lines.append("  {} ({:.1f}MB): {}".format(name, stats["size_mb"], tables_str))
    lines.append("")

    # ── Events ──
    if event_stats:
        lines.append("<b>═══ EVENTS ═══</b>")
        lines.append("  {} events across {} tickers ({} to {})".format(
            event_stats.get("total", 0), event_stats.get("tickers", 0),
            event_stats.get("earliest", "?"), event_stats.get("latest", "?")))
        lines.append("")

    # ── Cost ──
    lines.append("<b>═══ MONTHLY COST ESTIMATE ═══</b>")
    lines.append("  Gemini 3.1 Flash Lite (30/day): ~$0.25")
    lines.append("  Gemini 2.5 Flash (fallback): ~$0.06")
    lines.append("  Haiku 4.5 (filter): ~$0.01")
    lines.append("  Opus 4.6 (weekly+monthly): ~$0.20")
    lines.append("  Sonnet 4.6 (fallback only): ~$0.00")
    lines.append("  <b>Total: ~$0.52/month</b>")
    lines.append("")

    # ── Tech Stack ──
    lines.append("<b>═══ TECH STACK ═══</b>")
    lines.append("  Python 3.14 | SQLite (WAL mode)")
    lines.append("  macOS launchd scheduling")
    lines.append("  Telegram Bot API (long-polling)")
    lines.append("  yfinance (market data)")
    lines.append("  feedparser (Google News RSS)")
    lines.append("  Anthropic + Google Gemini APIs")
    lines.append("")

    # ── File Layout ──
    lines.append("<b>═══ FILE LAYOUT ═══</b>")
    lines.append("ASCR/ (43 .py, 4 configs)")
    lines.append("  Core: main, config, db, utils")
    lines.append("  Engines: analysis_engine, discovery_engine")
    lines.append("  Pipeline: event_pipeline, news_fetcher, sec_fetcher")
    lines.append("  Analysis: recommender, scoring, rating, price_events")
    lines.append("  Learning: experience_tracker, performance_feedback_loop")
    lines.append("  Safety: bubble_detector, market_regime, data_quality")
    lines.append("  Notifications: telegram_notifier")
    lines.append("")
    lines.append("ASCR-H/ (30 .py)")
    lines.append("  Core: main, config, db, event_trader")
    lines.append("  Execution: trading_rules, intraday_monitor")
    lines.append("  Validation: decision_logger, outcome_evaluator, decision_quality")
    lines.append("  Notifications: telegram_notifier")
    lines.append("")

    # ── Telegram ──
    lines.append("<b>═══ TELEGRAM ═══</b>")
    lines.append("  Public repo includes notification senders.")
    lines.append("  Interactive command bots can be added as a private/local overlay.")

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_system_report())
