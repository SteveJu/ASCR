"""Experience Tracker — structured post-hoc learning system.

Four components:
1. Signal Accuracy: track each event signal's predicted vs actual impact
2. Decision Journal: auto-record buy/sell reasoning, score after 5/10/20/30d
3. Pattern Library: extract recurring patterns from success/failure
4. Monthly Review: Opus-powered meta-analysis with human-approved adjustments

NOT reinforcement learning. This is structured experience accumulation
with pattern recognition. All adjustments require human approval.
"""
import os
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

from src import db, config
from src.utils import get_logger

logger = get_logger("experience")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "experience.sqlite")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create experience tracking tables."""
    conn = get_conn()
    conn.executescript("""
    -- Signal accuracy: every event signal → did it actually move the stock?
    CREATE TABLE IF NOT EXISTS signal_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER,
        ticker TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        event_type TEXT,
        source TEXT,
        headline TEXT,
        -- Prediction at signal time
        predicted_verdict TEXT,
        predicted_conviction TEXT,
        predicted_evidence_delta REAL,
        predicted_priced_in_pct REAL,
        price_at_signal REAL,
        -- Actual outcomes (filled later)
        price_5d REAL,
        price_10d REAL,
        price_20d REAL,
        price_30d REAL,
        return_5d REAL,
        return_10d REAL,
        return_20d REAL,
        return_30d REAL,
        -- Benchmark comparison
        benchmark_return_20d REAL,
        alpha_20d REAL,
        -- Evaluation
        signal_accurate INTEGER,  -- 1=direction correct, 0=wrong, NULL=pending
        accuracy_score REAL,      -- 0-100
        evaluation_notes TEXT,
        evaluated_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Pattern library: discovered patterns from experience
    CREATE TABLE IF NOT EXISTS patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_name TEXT UNIQUE NOT NULL,
        pattern_type TEXT NOT NULL,  -- 'bullish', 'bearish', 'neutral'
        description TEXT,
        -- Pattern definition
        event_types TEXT,            -- JSON array of event types
        market_cap_range TEXT,       -- 'small/mid/large'
        supply_chain_area TEXT,
        min_evidence_delta REAL,
        -- Performance stats
        sample_count INTEGER DEFAULT 0,
        avg_return_20d REAL,
        win_rate REAL,               -- % of positive 20d returns
        avg_alpha_20d REAL,
        best_case TEXT,              -- JSON: {ticker, return, date}
        worst_case TEXT,             -- JSON: {ticker, return, date}
        -- Reliability
        confidence_level TEXT,       -- 'high' (n>=10), 'medium' (5-9), 'low' (<5)
        last_updated TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Monthly review: Opus meta-analysis results
    CREATE TABLE IF NOT EXISTS monthly_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        review_date TEXT NOT NULL,
        review_period TEXT,          -- '2026-04'
        -- Analysis
        total_signals INTEGER,
        accurate_signals INTEGER,
        accuracy_rate REAL,
        total_trades INTEGER,
        winning_trades INTEGER,
        win_rate REAL,
        avg_return REAL,
        avg_alpha REAL,
        -- Insights (from Opus)
        top_patterns TEXT,           -- JSON: best performing patterns
        weak_patterns TEXT,          -- JSON: underperforming patterns
        missed_opportunities TEXT,   -- JSON: signals we should've acted on
        false_positives TEXT,        -- JSON: signals that led to losses
        recommendations TEXT,        -- JSON: suggested adjustments
        -- Approval
        approved INTEGER DEFAULT 0,  -- 0=pending, 1=approved, -1=rejected
        approved_at TEXT,
        applied_changes TEXT,        -- JSON: what was actually changed
        raw_opus_response TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Signal type performance summary (materialized view, refreshed periodically)
    CREATE TABLE IF NOT EXISTS signal_type_stats (
        event_type TEXT PRIMARY KEY,
        sample_count INTEGER,
        avg_return_5d REAL,
        avg_return_10d REAL,
        avg_return_20d REAL,
        avg_return_30d REAL,
        win_rate_20d REAL,
        avg_alpha_20d REAL,
        avg_predicted_delta REAL,
        prediction_accuracy REAL,   -- correlation between predicted_delta and actual return
        last_updated TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_so_ticker ON signal_outcomes(ticker);
    CREATE INDEX IF NOT EXISTS idx_so_date ON signal_outcomes(signal_date);
    CREATE INDEX IF NOT EXISTS idx_so_type ON signal_outcomes(event_type);
    CREATE INDEX IF NOT EXISTS idx_so_pending ON signal_outcomes(signal_accurate)
        WHERE signal_accurate IS NULL;
    """)
    conn.commit()
    conn.close()
    logger.info("Experience DB initialized")


# ============================================================
# 1. SIGNAL ACCURACY TRACKER
# ============================================================

def record_signal(event_id: int, ticker: str, signal_date: str,
                  event_type: str, source: str, headline: str,
                  verdict: str, conviction: str, evidence_delta: float,
                  priced_in_pct: float, price_at_signal: float):
    """Record a new signal for future accuracy tracking."""
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO signal_outcomes
        (event_id, ticker, signal_date, event_type, source, headline,
         predicted_verdict, predicted_conviction, predicted_evidence_delta,
         predicted_priced_in_pct, price_at_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, ticker, signal_date, event_type, source, headline,
          verdict, conviction, evidence_delta, priced_in_pct, price_at_signal))
    conn.commit()
    conn.close()


def backfill_signals():
    """Backfill signal_outcomes from existing events table."""
    from src.recommender import get_live_price

    conn = get_conn()
    _radar_cm = db.get_conn()
    radar_conn = _radar_cm.__enter__()

    existing = {r[0] for r in conn.execute(
        "SELECT event_id FROM signal_outcomes WHERE event_id IS NOT NULL"
    ).fetchall()}

    events = radar_conn.execute("""
        SELECT id, ticker, date, event_type, source, headline,
               verdict, conviction, evidence_delta, priced_in_pct
        FROM events WHERE ticker IS NOT NULL
        ORDER BY date
    """).fetchall()

    added = 0
    for e in events:
        if e["id"] in existing:
            continue
        # Get price at signal date
        price_row = radar_conn.execute("""
            SELECT close FROM prices WHERE ticker=? AND date<=?
            ORDER BY date DESC LIMIT 1
        """, (e["ticker"], e["date"])).fetchone()
        price = price_row["close"] if price_row else 0

        conn.execute("""
            INSERT INTO signal_outcomes
            (event_id, ticker, signal_date, event_type, source, headline,
             predicted_verdict, predicted_conviction, predicted_evidence_delta,
             predicted_priced_in_pct, price_at_signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (e["id"], e["ticker"], e["date"], e["event_type"], e["source"],
              e["headline"], e["verdict"], e["conviction"],
              e["evidence_delta"], e["priced_in_pct"], price))
        added += 1

    conn.commit()
    conn.close()
    _radar_cm.__exit__(None, None, None)
    logger.info(f"Backfilled {added} signals")
    return added


def evaluate_signals():
    """Evaluate pending signals where enough time has passed."""
    conn = get_conn()
    _radar_cm2 = db.get_conn()
    radar_conn = _radar_cm2.__enter__()
    today = datetime.now().strftime("%Y-%m-%d")

    pending = conn.execute("""
        SELECT id, ticker, signal_date, price_at_signal, predicted_evidence_delta
        FROM signal_outcomes
        WHERE signal_accurate IS NULL AND price_at_signal > 0
    """).fetchall()

    evaluated = 0
    for sig in pending:
        sig_date = datetime.strptime(sig["signal_date"], "%Y-%m-%d")
        days_since = (datetime.now() - sig_date).days

        updates = {}
        # Fill in price at each horizon
        for horizon in [5, 10, 20, 30]:
            if days_since >= horizon:
                target_date = (sig_date + timedelta(days=horizon)).strftime("%Y-%m-%d")
                price_row = radar_conn.execute("""
                    SELECT close FROM prices WHERE ticker=? AND date<=?
                    ORDER BY date DESC LIMIT 1
                """, (sig["ticker"], target_date)).fetchone()
                if price_row and price_row["close"]:
                    ret = (price_row["close"] / sig["price_at_signal"] - 1) * 100
                    updates[f"price_{horizon}d"] = price_row["close"]
                    updates[f"return_{horizon}d"] = round(ret, 2)

        # Benchmark (QQQ) for 20d alpha
        if "return_20d" in updates:
            target_20d = (sig_date + timedelta(days=20)).strftime("%Y-%m-%d")
            qqq_start = radar_conn.execute(
                "SELECT close FROM prices WHERE ticker='QQQ' AND date<=? ORDER BY date DESC LIMIT 1",
                (sig["signal_date"],)).fetchone()
            qqq_end = radar_conn.execute(
                "SELECT close FROM prices WHERE ticker='QQQ' AND date<=? ORDER BY date DESC LIMIT 1",
                (target_20d,)).fetchone()
            if qqq_start and qqq_end and qqq_start["close"]:
                bench_ret = (qqq_end["close"] / qqq_start["close"] - 1) * 100
                updates["benchmark_return_20d"] = round(bench_ret, 2)
                updates["alpha_20d"] = round(updates["return_20d"] - bench_ret, 2)

        # Score accuracy at 20d mark
        if days_since >= 20 and "return_20d" in updates:
            ret_20d = updates["return_20d"]
            pred_delta = sig["predicted_evidence_delta"] or 0

            # Signal accurate if: positive prediction → positive return (or vice versa)
            if pred_delta > 0:
                accurate = 1 if ret_20d > 0 else 0
            else:
                accurate = 1 if ret_20d <= 0 else 0

            # Accuracy score: magnitude match
            # Perfect: predicted +8, got +8% return → 100
            # Good: predicted +8, got +4% → 70
            # Bad: predicted +8, got -5% → 0
            if pred_delta > 0 and ret_20d > 0:
                score = min(100, 50 + (ret_20d / max(pred_delta, 1)) * 50)
            elif pred_delta > 0 and ret_20d <= 0:
                score = max(0, 50 + ret_20d * 5)  # -10% → 0
            else:
                score = 50  # neutral prediction

            updates["signal_accurate"] = accurate
            updates["accuracy_score"] = round(score, 1)
            updates["evaluated_at"] = today
            evaluated += 1

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE signal_outcomes SET {set_clause} WHERE id=?",
                        list(updates.values()) + [sig["id"]])

    conn.commit()
    conn.close()
    _radar_cm2.__exit__(None, None, None)
    logger.info(f"Evaluated {evaluated} signals, {len(pending)} total pending")
    return evaluated


# ============================================================
# 2. PATTERN LIBRARY
# ============================================================

def refresh_patterns():
    """Analyze signal outcomes to discover/update patterns."""
    conn = get_conn()

    # Only use evaluated signals with 20d data
    rows = conn.execute("""
        SELECT event_type, predicted_evidence_delta,
               predicted_verdict, return_20d, alpha_20d, signal_accurate,
               ticker, signal_date
        FROM signal_outcomes
        WHERE return_20d IS NOT NULL
    """).fetchall()

    if len(rows) < 5:
        logger.info(f"Only {len(rows)} evaluated signals, need 5+ for patterns")
        conn.close()
        return

    # Group by event_type
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["event_type"]].append(dict(r))

    # Update signal_type_stats
    today = datetime.now().strftime("%Y-%m-%d")
    for etype, signals in by_type.items():
        n = len(signals)
        returns_20d = [s["return_20d"] for s in signals if s["return_20d"] is not None]
        returns_5d = [s.get("return_5d", 0) or 0 for s in signals]
        returns_10d = [s.get("return_10d", 0) or 0 for s in signals]
        returns_30d = [s.get("return_30d", 0) or 0 for s in signals]
        alphas = [s["alpha_20d"] for s in signals if s["alpha_20d"] is not None]

        if not returns_20d:
            continue

        win_rate = len([r for r in returns_20d if r > 0]) / len(returns_20d) * 100
        avg_pred = sum(s.get("predicted_evidence_delta", 0) or 0 for s in signals) / n

        # Prediction accuracy: correlation between predicted delta and actual return
        pred_deltas = [s.get("predicted_evidence_delta", 0) or 0 for s in signals]
        if len(set(pred_deltas)) > 1 and len(set(returns_20d)) > 1:
            # Simple Pearson correlation
            mean_p = sum(pred_deltas) / len(pred_deltas)
            mean_r = sum(returns_20d) / len(returns_20d)
            cov = sum((p - mean_p) * (r - mean_r) for p, r in zip(pred_deltas, returns_20d))
            std_p = (sum((p - mean_p) ** 2 for p in pred_deltas)) ** 0.5
            std_r = (sum((r - mean_r) ** 2 for r in returns_20d)) ** 0.5
            pred_acc = cov / (std_p * std_r) if std_p * std_r > 0 else 0
        else:
            pred_acc = 0

        conn.execute("""
            INSERT OR REPLACE INTO signal_type_stats
            (event_type, sample_count, avg_return_5d, avg_return_10d, avg_return_20d,
             avg_return_30d, win_rate_20d, avg_alpha_20d, avg_predicted_delta,
             prediction_accuracy, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (etype, n,
              round(sum(returns_5d) / max(len(returns_5d), 1), 2),
              round(sum(returns_10d) / max(len(returns_10d), 1), 2),
              round(sum(returns_20d) / len(returns_20d), 2),
              round(sum(returns_30d) / max(len(returns_30d), 1), 2),
              round(win_rate, 1),
              round(sum(alphas) / len(alphas), 2) if alphas else 0,
              round(avg_pred, 1),
              round(pred_acc, 3),
              today))

    # Discover compound patterns
    _discover_compound_patterns(conn, rows)

    conn.commit()
    conn.close()
    logger.info(f"Refreshed patterns from {len(rows)} signals across {len(by_type)} event types")


def _discover_compound_patterns(conn, rows):
    """Find compound patterns like 'contract + small cap = big returns'."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Pattern: event_type + high evidence_delta
    high_delta = [r for r in rows if (r.get("predicted_evidence_delta") or 0) >= 7]
    low_delta = [r for r in rows if (r.get("predicted_evidence_delta") or 0) < 4]

    for name, group, desc in [
        ("high_conviction_signals", high_delta, "Events with evidence_delta >= 7"),
        ("low_conviction_signals", low_delta, "Events with evidence_delta < 4"),
    ]:
        if len(group) >= 3:
            returns = [r["return_20d"] for r in group if r["return_20d"] is not None]
            alphas = [r["alpha_20d"] for r in group if r["alpha_20d"] is not None]
            if not returns:
                continue

            win_rate = len([r for r in returns if r > 0]) / len(returns) * 100
            avg_ret = sum(returns) / len(returns)
            avg_alpha = sum(alphas) / len(alphas) if alphas else 0

            best = max(group, key=lambda x: x.get("return_20d", -999))
            worst = min(group, key=lambda x: x.get("return_20d", 999))
            conf = "high" if len(group) >= 10 else "medium" if len(group) >= 5 else "low"

            conn.execute("""
                INSERT OR REPLACE INTO patterns
                (pattern_name, pattern_type, description, sample_count,
                 avg_return_20d, win_rate, avg_alpha_20d, best_case, worst_case,
                 confidence_level, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name,
                  "bullish" if avg_ret > 2 else "bearish" if avg_ret < -2 else "neutral",
                  desc, len(group), round(avg_ret, 2), round(win_rate, 1),
                  round(avg_alpha, 2),
                  json.dumps({"ticker": best["ticker"], "return": best.get("return_20d"),
                             "date": best["signal_date"]}),
                  json.dumps({"ticker": worst["ticker"], "return": worst.get("return_20d"),
                             "date": worst["signal_date"]}),
                  conf, today))


def get_signal_stats() -> list:
    """Get signal type performance stats for display."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM signal_type_stats ORDER BY sample_count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_patterns() -> list:
    """Get discovered patterns."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM patterns ORDER BY sample_count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# 3. MONTHLY REVIEW (Opus)
# ============================================================

def generate_monthly_review(month: str = None) -> dict:
    """Generate monthly review using Opus. Returns review data.

    month: 'YYYY-MM' format, defaults to last month.
    """
    if not month:
        last = datetime.now().replace(day=1) - timedelta(days=1)
        month = last.strftime("%Y-%m")

    conn = get_conn()

    # Gather data for the month
    month_start = f"{month}-01"
    month_end = f"{month}-31"

    signals = conn.execute("""
        SELECT * FROM signal_outcomes
        WHERE signal_date BETWEEN ? AND ?
        AND return_20d IS NOT NULL
        ORDER BY signal_date
    """, (month_start, month_end)).fetchall()

    stats = conn.execute("SELECT * FROM signal_type_stats").fetchall()
    patterns = conn.execute("SELECT * FROM patterns").fetchall()

    # Summary stats
    total = len(signals)
    if total == 0:
        conn.close()
        return {"error": f"No evaluated signals for {month}"}

    accurate = len([s for s in signals if s["signal_accurate"] == 1])
    returns = [s["return_20d"] for s in signals if s["return_20d"] is not None]
    alphas = [s["alpha_20d"] for s in signals if s["alpha_20d"] is not None]

    # Build context for Opus
    signal_summary = []
    for s in signals:
        signal_summary.append({
            "ticker": s["ticker"], "date": s["signal_date"],
            "type": s["event_type"], "verdict": s["predicted_verdict"],
            "evidence_delta": s["predicted_evidence_delta"],
            "return_20d": s["return_20d"], "alpha_20d": s["alpha_20d"],
            "accurate": bool(s["signal_accurate"]),
        })

    type_stats = [dict(s) for s in stats]
    pattern_list = [dict(p) for p in patterns]

    prompt = f"""You are analyzing the performance of an AI supply chain event-driven stock trading system for {month}.

SIGNAL OUTCOMES THIS MONTH:
{json.dumps(signal_summary, indent=2)}

SIGNAL TYPE HISTORICAL STATS:
{json.dumps(type_stats, indent=2)}

DISCOVERED PATTERNS:
{json.dumps(pattern_list, indent=2)}

Analyze and respond with ONLY valid JSON (no markdown):
{{
    "summary": "2-3 sentence overall assessment",
    "top_performing_signals": [
        {{"type": "event_type", "reason": "why it worked", "avg_return": X}}
    ],
    "worst_performing_signals": [
        {{"type": "event_type", "reason": "why it failed", "avg_return": X}}
    ],
    "missed_opportunities": [
        {{"ticker": "X", "what_happened": "description", "suggestion": "what to do next time"}}
    ],
    "false_positives": [
        {{"ticker": "X", "what_happened": "description", "lesson": "what to learn"}}
    ],
    "recommendations": [
        {{
            "category": "signal_weight/threshold/new_signal/remove_signal",
            "description": "what to change",
            "expected_impact": "what this would improve",
            "confidence": "high/medium/low",
            "priority": 1-5
        }}
    ],
    "system_health": "healthy/degrading/critical",
    "key_insight": "single most important learning"
}}"""

    # Call Opus
    try:
        import anthropic
        from dotenv import load_dotenv
        load_dotenv()
        client = anthropic.Anthropic(max_retries=3, timeout=120.0)
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        from src.llm_usage import log_ai_call
        log_ai_call(
            model="claude-opus-4-6",
            purpose="monthly_experience_review",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
        text = resp.content[0].text.strip()
        if "```" in text:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                if block.startswith("{"):
                    text = block
                    break

        analysis = json.loads(text)
    except Exception as e:
        logger.error(f"Opus review failed: {e}")
        analysis = {"error": str(e), "summary": "Opus analysis failed"}

    # Store review
    review = {
        "review_date": datetime.now().strftime("%Y-%m-%d"),
        "review_period": month,
        "total_signals": total,
        "accurate_signals": accurate,
        "accuracy_rate": round(accurate / total * 100, 1) if total else 0,
        "avg_return": round(sum(returns) / len(returns), 2) if returns else 0,
        "avg_alpha": round(sum(alphas) / len(alphas), 2) if alphas else 0,
        "recommendations": json.dumps(analysis.get("recommendations", [])),
        "raw_opus_response": json.dumps(analysis),
    }

    conn.execute("""
        INSERT INTO monthly_reviews
        (review_date, review_period, total_signals, accurate_signals, accuracy_rate,
         avg_return, avg_alpha, recommendations, raw_opus_response)
        VALUES (:review_date, :review_period, :total_signals, :accurate_signals,
                :accuracy_rate, :avg_return, :avg_alpha, :recommendations, :raw_opus_response)
    """, review)
    conn.commit()
    conn.close()

    logger.info(f"Monthly review for {month}: {total} signals, "
                f"{accurate}/{total} accurate ({review['accuracy_rate']}%)")
    return {**review, "analysis": analysis}


# ============================================================
# 4. EXPERIENCE-INFORMED SCORING ADJUSTMENTS
# ============================================================

def get_signal_weight_adjustments() -> dict:
    """Based on accumulated experience, suggest weight adjustments.

    Returns {event_type: multiplier} where multiplier adjusts evidence_delta.
    >1.0 = this signal type outperforms prediction → trust more
    <1.0 = this signal type underperforms → trust less
    """
    conn = get_conn()
    stats = conn.execute("""
        SELECT event_type, sample_count, avg_return_20d, win_rate_20d,
               prediction_accuracy, avg_predicted_delta
        FROM signal_type_stats
        WHERE sample_count >= 5
    """).fetchall()
    conn.close()

    adjustments = {}
    for s in stats:
        etype = s["event_type"]
        win_rate = s["win_rate_20d"] or 50
        pred_acc = s["prediction_accuracy"] or 0
        n = s["sample_count"]

        # Confidence-weighted adjustment
        # High win rate + high prediction accuracy → boost
        # Low win rate → dampen
        confidence = min(n / 20, 1.0)  # full confidence at 20+ samples

        if win_rate >= 70 and pred_acc > 0.3:
            multiplier = 1.0 + 0.3 * confidence  # up to 1.3x
        elif win_rate >= 60:
            multiplier = 1.0 + 0.1 * confidence  # up to 1.1x
        elif win_rate <= 40:
            multiplier = 1.0 - 0.3 * confidence  # down to 0.7x
        elif win_rate <= 50:
            multiplier = 1.0 - 0.1 * confidence  # down to 0.9x
        else:
            multiplier = 1.0

        adjustments[etype] = round(multiplier, 2)

    return adjustments


# ============================================================
# DAILY HOOK — called from analysis engine
# ============================================================

def daily_update():
    """Run daily experience tracking tasks."""
    init_db()

    # 1. Backfill any new signals
    added = backfill_signals()

    # 2. Evaluate signals that have matured
    evaluated = evaluate_signals()

    # 3. Refresh pattern library
    refresh_patterns()

    result = {
        "signals_added": added,
        "signals_evaluated": evaluated,
    }

    # 4. Check if monthly review is due (first of month)
    if datetime.now().day <= 3:
        last_month = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        conn = get_conn()
        existing = conn.execute(
            "SELECT id FROM monthly_reviews WHERE review_period=?", (last_month,)
        ).fetchone()
        conn.close()
        if not existing:
            logger.info(f"Monthly review due for {last_month}")
            result["monthly_review"] = generate_monthly_review(last_month)

    return result


# ============================================================
# TELEGRAM FORMATTING
# ============================================================

def format_signal_report() -> str:
    """Format signal accuracy report for Telegram."""
    conn = get_conn()
    stats = conn.execute("""
        SELECT * FROM signal_type_stats ORDER BY sample_count DESC
    """).fetchall()

    total = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN signal_accurate=1 THEN 1 ELSE 0 END) as accurate,
               AVG(return_20d) as avg_ret,
               AVG(alpha_20d) as avg_alpha
        FROM signal_outcomes WHERE signal_accurate IS NOT NULL
    """).fetchone()

    # Recent signals pending evaluation
    pending = conn.execute("""
        SELECT COUNT(*) FROM signal_outcomes WHERE signal_accurate IS NULL
    """).fetchone()[0]

    conn.close()

    lines = ["\U0001f9e0 <b>Experience Tracker</b>\n"]

    if total and total["total"]:
        acc_rate = (total["accurate"] or 0) / total["total"] * 100
        lines.append("Overall: {}/{} accurate ({:.0f}%)".format(
            total["accurate"] or 0, total["total"], acc_rate))
        lines.append("Avg 20d return: {:+.1f}% | Alpha: {:+.1f}%".format(
            total["avg_ret"] or 0, total["avg_alpha"] or 0))
        lines.append("{} signals pending\n".format(pending))

    if stats:
        lines.append("<b>Signal Type Performance:</b>")
        for s in stats[:10]:
            icon = "\U0001f7e2" if (s["win_rate_20d"] or 0) >= 60 else "\U0001f534" if (s["win_rate_20d"] or 0) < 40 else "\U0001f7e1"
            lines.append("  {} {} (n={}): {:+.1f}% | WR {:.0f}% | \u03b1 {:+.1f}%".format(
                icon, s["event_type"] or "?", s["sample_count"],
                s["avg_return_20d"] or 0, s["win_rate_20d"] or 0, s["avg_alpha_20d"] or 0))

    # Weight adjustments
    adj = get_signal_weight_adjustments()
    if adj:
        lines.append("\n<b>Experience Adjustments:</b>")
        for etype, mult in sorted(adj.items(), key=lambda x: x[1], reverse=True):
            if mult != 1.0:
                arrow = "\u2b06" if mult > 1.0 else "\u2b07"
                lines.append("  {} {}: {:.0f}%".format(arrow, etype, (mult - 1) * 100))

    return "\n".join(lines)


def format_monthly_review(review: dict) -> str:
    """Format monthly review for Telegram."""
    analysis = review.get("analysis", {})
    lines = ["\U0001f4ca <b>Monthly Review: {}</b>\n".format(review.get("review_period", "?"))]

    lines.append("Signals: {} | Accuracy: {:.0f}%".format(
        review.get("total_signals", 0), review.get("accuracy_rate", 0)))
    lines.append("Avg Return: {:+.1f}% | Alpha: {:+.1f}%".format(
        review.get("avg_return", 0), review.get("avg_alpha", 0)))
    lines.append("Health: {}".format(analysis.get("system_health", "?")))

    if analysis.get("key_insight"):
        lines.append("\n\U0001f4a1 <b>Key Insight:</b>")
        lines.append(analysis["key_insight"])

    recs = analysis.get("recommendations", [])
    if recs:
        lines.append("\n\U0001f527 <b>Recommendations:</b>")
        for r in recs[:5]:
            lines.append("  P{}: {} [{}]".format(
                r.get("priority", "?"), r.get("description", ""),
                r.get("confidence", "?")))

    lines.append("\n\u26a0\ufe0f Pending approval. Use /approve or /reject")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "backfill", "evaluate", "patterns",
                                            "stats", "review", "daily"])
    parser.add_argument("--month", default=None)
    args = parser.parse_args()

    init_db()
    if args.command == "init":
        print("Experience DB initialized")
    elif args.command == "backfill":
        n = backfill_signals()
        print(f"Backfilled {n} signals")
    elif args.command == "evaluate":
        n = evaluate_signals()
        print(f"Evaluated {n} signals")
    elif args.command == "patterns":
        refresh_patterns()
        for p in get_patterns():
            print(f"  {p['pattern_name']}: {p['avg_return_20d']:+.1f}% "
                  f"(n={p['sample_count']}, WR={p['win_rate']}%)")
    elif args.command == "stats":
        for s in get_signal_stats():
            print(f"  {s['event_type']:>20}: {s['avg_return_20d']:+.1f}% "
                  f"(n={s['sample_count']}, WR={s['win_rate_20d']:.0f}%)")
    elif args.command == "review":
        r = generate_monthly_review(args.month)
        print(json.dumps(r.get("analysis", r), indent=2))
    elif args.command == "daily":
        r = daily_update()
        print(f"Daily: +{r['signals_added']} signals, {r['signals_evaluated']} evaluated")
