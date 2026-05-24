#!/usr/bin/env python3
"""Weekly health check for ASCR + ASCR-H.
Outputs a Telegram-ready summary.
"""

import importlib
import math
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RADAR = os.path.abspath(os.environ.get("ASCR_PROJECT_DIR", os.path.join(ROOT, "ASCR")))
PAPER = os.path.abspath(os.environ.get("ASCR_H_PROJECT_DIR", os.path.join(ROOT, "ASCR-H")))
FULL_COVERAGE_RATIO = 0.80

LAUNCHD_JOBS = [
    {"project": "ASCR", "name": "event-daemon", "label": "com.ASCR.event-daemon", "kind": "service"},
    {"project": "ASCR", "name": "daily", "label": "com.ascr.daily", "kind": "timer"},
    {"project": "ASCR", "name": "fast-scan", "label": "com.ASCR.fast-scan", "kind": "timer"},
    {"project": "ASCR", "name": "discovery", "label": "com.ASCR.discovery", "kind": "timer"},
    {"project": "ASCR", "name": "universe-scan", "label": "com.ASCR.universe-scan", "kind": "timer"},
    {"project": "ASCR-H", "name": "daily", "label": "com.ASCR-H.daily", "kind": "timer"},
    {"project": "ASCR-H", "name": "intraday", "label": "com.ASCR-H.intraday", "kind": "timer"},
    {"project": "ASCR-H", "name": "weekly", "label": "com.ASCR-H.weekly", "kind": "timer"},
]


def _reset_project_imports(path: str) -> None:
    for key in [k for k in sys.modules if k.startswith("src")]:
        del sys.modules[key]
    sys.path = [p for p in sys.path if "/ASCR" not in p and "/ASCR-H" not in p]
    sys.path.insert(0, path)
    os.chdir(path)


def _connect(dbpath: str) -> sqlite3.Connection:
    conn = sqlite3.connect(dbpath, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _short_error(exc: Exception) -> str:
    return str(exc).split("\n")[0]


def _empty_data() -> dict:
    return {
        "universe_count": 0,
        "db_ticker_count": 0,
        "coverage_threshold": 0,
        "target_market_date": _last_market_day(datetime.now()),
        "prices": {"latest_date": "", "latest_count": 0, "full_date": "", "full_count": 0},
        "scores": {"latest_date": "", "latest_count": 0, "full_date": "", "full_count": 0},
        "latest_event": "",
        "latest_event_count": 0,
        "latest_event_tickers": 0,
        "event_count": 0,
        "latest_equity": "",
        "positions": [],
        "cash": 0,
        "pos_value": 0,
        "total_equity": 0,
    }


def _check_imports(issues: list[str]) -> None:
    for project, path in [("ASCR", RADAR), ("ASCR-H", PAPER)]:
        try:
            _reset_project_imports(path)
            for fname in sorted(os.listdir("src")):
                if not fname.endswith(".py") or fname.startswith("__"):
                    continue
                mod = f"src.{fname[:-3]}"
                try:
                    importlib.import_module(mod)
                except Exception as exc:
                    issues.append(f"❌ {project} import {mod}: {_short_error(exc)}")
        except Exception as exc:
            issues.append(f"❌ {project} import sweep failed: {_short_error(exc)}")


def _check_shadowed_imports(issues: list[str]) -> None:
    for project, path in [("ASCR", RADAR), ("ASCR-H", PAPER)]:
        src_dir = os.path.join(path, "src")
        if not os.path.isdir(src_dir):
            issues.append(f"❌ {project} src missing: {src_dir}")
            continue
        for fname in sorted(os.listdir(src_dir)):
            if not fname.endswith(".py") or fname.startswith("__"):
                continue
            with open(os.path.join(src_dir, fname), encoding="utf-8") as handle:
                lines = handle.read().split("\n")

            top_imports = set()
            for line in lines[:30]:
                match = re.match(r"^import (\w+)", line.strip())
                if match:
                    top_imports.add(match.group(1))

            for idx, line in enumerate(lines[30:], start=30):
                match = re.match(r"\s+import (\w+)$", line)
                if match and match.group(1) in top_imports:
                    issues.append(f"❌ {project} {fname}:L{idx + 1}: shadowed 'import {match.group(1)}'")


def _check_db_integrity(issues: list[str]) -> None:
    for name, dbpath in [
        ("ASCR", f"{RADAR}/data/ascr.sqlite"),
        ("experience", f"{RADAR}/data/experience.sqlite"),
        ("ASCR-H", f"{PAPER}/data/ascr_h.sqlite"),
    ]:
        if not os.path.exists(dbpath):
            issues.append(f"❌ {name} DB missing")
            continue
        try:
            conn = _connect(dbpath)
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            if result != "ok":
                issues.append(f"❌ {name} DB: {result}")
        except Exception as exc:
            issues.append(f"❌ {name} DB check failed: {_short_error(exc)}")


def _last_market_day(now: datetime) -> str:
    _reset_project_imports(RADAR)
    try:
        from src.market_calendar import is_us_market_holiday
    except Exception:
        is_us_market_holiday = lambda day: False

    day = now.date()
    while day.weekday() >= 5 or is_us_market_holiday(day):
        day -= timedelta(days=1)
    return day.isoformat()


def _coverage_stats(conn: sqlite3.Connection, table: str, threshold: int) -> dict:
    if table not in {"prices", "scores"}:
        raise ValueError(f"Unsupported coverage table: {table}")

    rows = conn.execute(f"""
        SELECT date, COUNT(DISTINCT ticker) AS ticker_count
        FROM {table}
        WHERE date IS NOT NULL AND date != ''
        GROUP BY date
        ORDER BY date DESC
    """).fetchall()
    latest = rows[0] if rows else None
    full = next((row for row in rows if row["ticker_count"] >= threshold), None)
    return {
        "latest_date": latest["date"] if latest else "",
        "latest_count": latest["ticker_count"] if latest else 0,
        "full_date": full["date"] if full else "",
        "full_count": full["ticker_count"] if full else 0,
    }


def _fmt_ratio(count: int, total: int) -> str:
    return f"{count}/{total}" if total else str(count)


def _check_data_freshness(issues: list[str]) -> dict:
    data = _empty_data()

    _reset_project_imports(RADAR)
    from src import config

    universe = set(config.all_tickers())
    universe_count = len(universe)
    coverage_threshold = max(1, math.ceil(universe_count * FULL_COVERAGE_RATIO))
    target_market_date = _last_market_day(datetime.now())
    data.update({
        "universe_count": universe_count,
        "coverage_threshold": coverage_threshold,
        "target_market_date": target_market_date,
    })

    radar_db = f"{RADAR}/data/ascr.sqlite"
    if os.path.exists(radar_db):
        conn = _connect(radar_db)
        price_stats = _coverage_stats(conn, "prices", coverage_threshold)
        score_stats = _coverage_stats(conn, "scores", coverage_threshold)

        latest_event = conn.execute("SELECT MAX(date) FROM events").fetchone()[0] or ""
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        latest_event_count = 0
        latest_event_tickers = 0
        if latest_event:
            latest_event_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE date=?", (latest_event,)
            ).fetchone()[0]
            latest_event_tickers = conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM events WHERE date=?", (latest_event,)
            ).fetchone()[0]

        db_tickers = {
            row[0]
            for row in conn.execute(
                "SELECT ticker FROM tickers WHERE COALESCE(status, 'active') = 'active'"
            ).fetchall()
        }
        conn.close()

        missing_tickers = sorted(universe - db_tickers)
        if missing_tickers:
            shown = ", ".join(missing_tickers[:12])
            suffix = "..." if len(missing_tickers) > 12 else ""
            issues.append(f"❌ tickers table missing {len(missing_tickers)} configured tickers: {shown}{suffix}")

        if not price_stats["full_date"] or price_stats["full_date"] < target_market_date:
            issues.append(
                "❌ prices full coverage stale: "
                f"full={price_stats['full_date'] or 'none'} "
                f"({_fmt_ratio(price_stats['full_count'], universe_count)}), "
                f"target={target_market_date}; latest={price_stats['latest_date'] or 'none'} "
                f"({_fmt_ratio(price_stats['latest_count'], universe_count)})"
            )

        if not score_stats["full_date"] or score_stats["full_date"] < target_market_date:
            issues.append(
                "❌ scores full coverage stale: "
                f"full={score_stats['full_date'] or 'none'} "
                f"({_fmt_ratio(score_stats['full_count'], universe_count)}), "
                f"target={target_market_date}; latest={score_stats['latest_date'] or 'none'} "
                f"({_fmt_ratio(score_stats['latest_count'], universe_count)})"
            )

        data.update({
            "db_ticker_count": len(db_tickers),
            "prices": price_stats,
            "scores": score_stats,
            "latest_event": latest_event,
            "latest_event_count": latest_event_count,
            "latest_event_tickers": latest_event_tickers,
            "event_count": event_count,
        })

    paper_db = f"{PAPER}/data/ascr_h.sqlite"
    if os.path.exists(paper_db):
        conn = _connect(paper_db)
        latest_equity = conn.execute("SELECT MAX(date) FROM paper_equity_curve").fetchone()[0] or ""
        positions = conn.execute(
            """
            SELECT ticker, quantity, avg_entry_price, current_value
            FROM paper_positions
            WHERE status='open' AND quantity > 0
            ORDER BY ticker
            """
        ).fetchall()
        account = conn.execute("SELECT cash FROM account").fetchone()
        cash = account[0] if account else 0
        pos_value = sum(position[3] or 0 for position in positions)
        total_equity = cash + pos_value
        ghosts = conn.execute("SELECT ticker FROM paper_positions WHERE status='closed' AND quantity > 0").fetchall()
        orphans = conn.execute("SELECT ticker FROM paper_positions WHERE status='open' AND quantity <= 0").fetchall()
        conn.close()

        if ghosts:
            issues.append(f"❌ Ghost positions: {[row[0] for row in ghosts]}")
        if orphans:
            issues.append(f"❌ Orphan positions: {[row[0] for row in orphans]}")
        if cash < 0:
            issues.append(f"❌ Negative cash: ${cash:,.2f}")
        if latest_equity and latest_equity < target_market_date:
            issues.append(f"❌ equity curve stale: {latest_equity}, target={target_market_date}")

        data.update({
            "latest_equity": latest_equity,
            "positions": positions,
            "cash": cash,
            "pos_value": pos_value,
            "total_equity": total_equity,
        })

    return data


def parse_launchctl_list(output: str) -> dict[str, dict]:
    statuses = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pid, status, label = parts
        statuses[label] = {
            "pid": None if pid == "-" else pid,
            "status": status,
        }
    return statuses


def _check_launchd(issues: list[str]) -> dict[str, dict]:
    result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        issues.append(f"❌ launchctl list failed: {result.stderr.strip() or result.returncode}")
        return {}

    statuses = parse_launchctl_list(result.stdout)
    for job in LAUNCHD_JOBS:
        status = statuses.get(job["label"])
        if not status:
            issues.append(f"❌ launchd {job['label']} not loaded")
            continue

        if job["kind"] == "service":
            if not status["pid"]:
                issues.append(f"❌ launchd {job['label']} loaded but not running (last={status['status']})")
        elif status["status"] not in {"0", "-"}:
            issues.append(f"❌ launchd {job['label']} last exit={status['status']}")

    return statuses


def _check_recommender(issues: list[str]) -> None:
    _reset_project_imports(RADAR)
    try:
        from src.recommender import get_rankings

        rankings = get_rankings()
        if len(rankings) == 0:
            issues.append("❌ recommender: 0 rankings")
    except Exception as exc:
        issues.append(f"❌ recommender: {exc}")


def _format_coverage_line(name: str, stats: dict, universe_count: int, target_market_date: str) -> str:
    latest = stats["latest_date"] or "none"
    full = stats["full_date"] or "none"
    return (
        f"  {name}: latest={latest} ({_fmt_ratio(stats['latest_count'], universe_count)}), "
        f"full={full} ({_fmt_ratio(stats['full_count'], universe_count)}), "
        f"target={target_market_date}"
    )


def _format_service_line(job: dict, status: dict | None) -> str:
    if not status:
        return f"    🔴 {job['name']} not loaded"

    pid = status["pid"]
    last = status["status"]
    if job["kind"] == "service":
        if pid:
            return f"    🟢 {job['name']} running (PID {pid})"
        return f"    🔴 {job['name']} stopped (last={last})"

    if last in {"0", "-"}:
        return f"    🟡 {job['name']} loaded/idle (last={last})"
    return f"    🔴 {job['name']} failed (last={last})"


def build_report(issues: list[str], data: dict, launchd: dict[str, dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📋 <b>Weekly Health Check</b> — {today}\n"]
    if not issues:
        lines.append("✅ <b>All clear</b>\n")
    else:
        lines.append(f"⚠️ <b>{len(issues)} issues:</b>")
        for issue in issues:
            lines.append(f"  {issue}")
        lines.append("")

    universe_count = data["universe_count"]
    target = data["target_market_date"]
    lines.append(
        f"<b>Data:</b> universe={universe_count} tickers "
        f"(db_active={data['db_ticker_count']}, full_threshold={data['coverage_threshold']})"
    )
    lines.append(_format_coverage_line("prices", data["prices"], universe_count, target))
    lines.append(_format_coverage_line("scores", data["scores"], universe_count, target))
    lines.append(
        f"  events: latest={data['latest_event'] or 'none'} "
        f"({data['latest_event_count']} events/{data['latest_event_tickers']} tickers), "
        f"total={data['event_count']}"
    )
    lines.append(f"  equity_curve={data['latest_equity'] or 'none'}")

    positions = data["positions"]
    open_tickers = ", ".join(position[0] for position in positions) or "none"
    lines.append(
        f"\n<b>Portfolio:</b> cash=${data['cash']:,.0f} "
        f"pos=${data['pos_value']:,.0f} total=${data['total_equity']:,.0f}"
    )
    lines.append(f"  Open({len(positions)}): {open_tickers}")

    lines.append("\n<b>Services:</b>")
    current_project = None
    for job in LAUNCHD_JOBS:
        if job["project"] != current_project:
            current_project = job["project"]
            lines.append(f"  <b>{current_project}</b>")
        lines.append(_format_service_line(job, launchd.get(job["label"])))

    return "\n".join(lines)


def main() -> int:
    issues: list[str] = []
    _check_imports(issues)
    _check_shadowed_imports(issues)
    _check_db_integrity(issues)
    data = _check_data_freshness(issues)
    launchd = _check_launchd(issues)
    _check_recommender(issues)
    print(build_report(issues, data, launchd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
