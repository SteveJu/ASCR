"""Stock Recommender — core ranking and recommendation engine.

This is ASCR's primary output: a ranked list of stocks to buy.
Paper-trader and any other consumer reads this.

Ranking logic:
  1. Event signal (avg_delta × min(count, 5)) — primary
  2. Momentum — tiebreaker
  3. Must have event_evidence >= 5 OR momentum >= 50
"""
import os
import sqlite3
from datetime import datetime, timedelta
from src import config
from src.utils import get_logger

logger = get_logger("recommender")

# Real-time price cache (60s TTL)
_price_cache = {}
_CACHE_TTL = 60


def get_live_price(ticker: str) -> float:
    """Real-time price via yfinance, cached 60s, fallback to DB."""
    import time as _time
    now = _time.time()
    if ticker in _price_cache:
        p, t = _price_cache[ticker]
        if now - t < _CACHE_TTL:
            return p
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if not price:
            hist = t.history(period="1d")
            if len(hist) > 0:
                price = float(hist["Close"].iloc[-1])
        if price and price > 0:
            _price_cache[ticker] = (price, now)
            return price
    except Exception:
        pass
    # Fallback DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    conn.close()
    price = row["close"] if row else 0.0
    if price > 0:
        _price_cache[ticker] = (price, now)
    return price

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "ascr.sqlite")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn



# Event-type confidence multipliers (seeded priors, updated by experience tracker)
# > 1.0 = trust more, < 1.0 = trust less
EVENT_TYPE_MULTIPLIERS = {
    # Hard signals (verifiable, specific)
    "contract": 1.20,
    "sec_8k": 1.15,
    "insider_buy": 1.15,
    "earnings_beat": 1.10,
    "investment": 1.10,
    "partnership": 1.05,
    "expansion": 1.05,
    # Neutral baseline
    "shortage": 1.00,
    "upgrade": 0.95,        # Analyst upgrades are lagging
    "other": 1.00,
    # Soft signals (noisy, herding)
    "downgrade": 0.95,
    "insider_sell": 0.90,   # Could be tax/diversification
    "price_surge_1w": 0.85, # Reactive, not predictive
    "price_surge_1m": 0.85,
    "price_drop_1w": 0.85,
    "price_crash_1m": 0.85,
    "reddit": 0.80,         # Noisy, herding risk
    "supply_chain_propagation": 0.90,  # Derived, not direct
    "leveraged_etf_launch": 0.70,     # Lagging indicator, confirmation only
    "leveraged_etf_bubble": 1.10,     # Overheating warning is actionable
}


def _get_event_multiplier(event_type: str) -> float:
    """Get confidence multiplier for an event type.
    Falls back to experience tracker if available."""
    # Try experience tracker first
    try:
        from src.experience_tracker import get_signal_weight_adjustments
        adj = get_signal_weight_adjustments()
        if event_type in adj:
            return adj[event_type]
    except Exception:
        pass
    return EVENT_TYPE_MULTIPLIERS.get(event_type, 1.0)


def get_rankings(days=30, min_event=5) -> list:
    """Return ranked list of stock recommendations.

    Each entry:
      ticker, ev_score, momentum, ev_count, event_types, heat,
      evidence, rating, price, signal_type
    """
    conn = _conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Event scores with verdict analysis
    events = conn.execute("""
        SELECT ticker,
               ROUND(AVG(evidence_delta) * MIN(COUNT(*), 8), 1) as ev_score,
               COUNT(*) as ev_count,
               COUNT(DISTINCT event_type) as unique_types,
               GROUP_CONCAT(DISTINCT event_type) as event_types,
               -- Verdict scoring: STRONG_BUY=3, BUY=2, HOLD=0, AVOID=-2, SELL=-3
               ROUND(AVG(CASE verdict
                   WHEN 'STRONG_BUY' THEN 3 WHEN 'BUY' THEN 2
                   WHEN 'HOLD' THEN 0 WHEN 'AVOID' THEN -2 WHEN 'SELL' THEN -3
                   ELSE 0 END), 1) as avg_verdict,
               -- Conviction: HIGH=3, MEDIUM=2, LOW=1
               ROUND(AVG(CASE conviction
                   WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 1
                   ELSE 0 END), 1) as avg_conviction,
               GROUP_CONCAT(DISTINCT upside_potential) as upside_potentials
        FROM events WHERE date >= ?
        GROUP BY ticker
    """, (cutoff,)).fetchall()
    ev_map = {r["ticker"]: dict(r) for r in events}

    # Get best thesis for each ticker (highest evidence_delta event)
    thesis_map = {}
    for ticker in [r["ticker"] for r in events]:
        row = conn.execute("""
            SELECT investment_thesis, verdict, conviction, upside_potential,
                   moat, catalyst, summary, headline
            FROM events WHERE ticker=? AND date >= ? AND investment_thesis IS NOT NULL
            ORDER BY evidence_delta DESC LIMIT 1
        """, (ticker, cutoff)).fetchone()
        if row:
            thesis_map[ticker] = dict(row)

    # Latest scores
    scores = conn.execute("""
        SELECT ticker, evidence_score as evidence, asymmetry_score as asymmetry, momentum_score as momentum, risk_score as risk, opportunity_score as opportunity, rating
        FROM scores
        WHERE date = (SELECT MAX(date) FROM scores)
    """).fetchall()
    score_map = {r["ticker"]: dict(r) for r in scores}

    conn.close()

    # Filter to universe only — don't recommend tickers we can't trade
    try:
        universe_tickers = set(config.all_tickers())
    except Exception:
        universe_tickers = set()

    all_tickers = {
        t for t, ev in ev_map.items()
        if (ev.get("ev_score", 0) or 0) >= min_event
    }
    if universe_tickers:
        all_tickers = all_tickers & universe_tickers

    # Latest prices — real-time via yfinance (cached 60s), fallback to DB.
    # Fetch only tickers that can survive the event-signal filter below.
    price_map = {}
    for ticker in all_tickers:
        price_map[ticker] = get_live_price(ticker)

    # Combine
    combined = []
    for t in all_tickers:
        ev = ev_map.get(t, {})
        sc = score_map.get(t, {})
        ev_score = ev.get("ev_score", 0) or 0
        mom = sc.get("momentum", 0) or 0

        if ev_score >= min_event:  # event signal required, momentum alone is not enough
            count = ev.get("ev_count", 0) or 0
            heat = min(count / 10.0, 3.0)

            signal = "EVENT"

            thesis = thesis_map.get(t, {})
            avg_verdict = ev.get("avg_verdict", 0) or 0
            avg_conviction = ev.get("avg_conviction", 0) or 0

            combined.append({
                "ticker": t,
                "ev_score": ev_score,
                "verdict_score": round(avg_verdict * avg_conviction, 1),  # -9 to +9
                "avg_verdict": avg_verdict,
                "avg_conviction": avg_conviction,
                "momentum": mom,
                "ev_count": count,
                "event_types": ev.get("event_types", ""),
                "heat": round(heat, 1),
                "evidence": sc.get("evidence", 0) or 0,
                "asymmetry": sc.get("asymmetry", 0) or 0,
                "risk": sc.get("risk", 0) or 0,
                "opportunity": sc.get("opportunity", 0) or 0,
                "rating": sc.get("rating", "?"),
                "price": price_map.get(t, 0),
                "signal_type": signal,
                "upside": ev.get("upside_potentials", ""),
                "thesis": thesis.get("investment_thesis", ""),
                "moat": thesis.get("moat", ""),
                "catalyst": thesis.get("catalyst", ""),
                "top_summary": thesis.get("summary", ""),
            })

    # Primary: verdict_score (analyst conviction × verdict), fallback: ev_score
    combined.sort(key=lambda x: (x["verdict_score"], x["ev_score"], x["evidence"]), reverse=True)

    # Log picks generation
    try:
        from src.activity_log import log as alog
        top5 = combined[:5]
        alog("recommender", "picks_generated",
             count=len(combined),
             top5=",".join(s["ticker"] for s in top5),
             top_ev=top5[0]["ev_score"] if top5 else 0,
             top_verdict=top5[0]["verdict_score"] if top5 else 0)
    except Exception:
        pass

    return combined


def get_top_picks(n=10, **kwargs) -> list:
    """Return top N picks."""
    return get_rankings(**kwargs)[:n]


def get_heat_ranking(days=30) -> list:
    """Return tickers ranked by article count (heat)."""
    conn = _conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT ticker, COUNT(*) as cnt,
               ROUND(AVG(evidence_delta), 1) as avg_ev,
               COUNT(DISTINCT event_type) as types
        FROM events WHERE date >= ?
        GROUP BY ticker ORDER BY cnt DESC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def check_sell_signals(ticker: str, days=30) -> dict:
    """Check if a ticker has negative event signals.

    Returns: {should_sell, reason, neg_ev, risk, heat}
    """
    conn = _conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    neg = conn.execute("""
        SELECT SUM(evidence_delta) as total_neg_ev, SUM(risk_delta) as total_risk
        FROM events WHERE ticker=? AND date >= ? AND evidence_delta < 0
    """, (ticker, cutoff)).fetchone()

    heat_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM events WHERE ticker=? AND date >= ?
    """, (ticker, cutoff)).fetchone()

    conn.close()

    total_neg_ev = neg["total_neg_ev"] or 0 if neg else 0
    total_risk = neg["total_risk"] or 0 if neg else 0
    heat = heat_row["cnt"] or 0 if heat_row else 0

    # High heat = more patience on thesis break
    ev_threshold = -8 if heat < 10 else -12
    risk_threshold = 10 if heat < 10 else 14

    should_sell = total_neg_ev <= ev_threshold or total_risk >= risk_threshold
    reason = f"thesis_break_ev{total_neg_ev:+.0f}_risk{total_risk:+.0f}_heat{heat}" if should_sell else ""

    return {
        "should_sell": should_sell,
        "reason": reason,
        "neg_ev": total_neg_ev,
        "risk": total_risk,
        "heat": heat,
    }


def get_portfolio_instructions(current_positions: dict, max_pos: int = 10) -> dict:
    """Generate buy/sell/hold instructions based on market analysis.

    Radar decides WHAT to buy/sell based on signals.
    Executor decides HOW MUCH based on its own cash situation.

    Args:
        current_positions: {ticker: {quantity, avg_entry_price, peak_price, current_price}}
        max_pos: max positions allowed

    Returns:
        {
            "sells": [{ticker, reason, urgency, pnl_pct}],
            "buys": [{ticker, rank, reason, ev_score, verdict_score, thesis}],
            "holds": [{ticker, rank, note}],
        }
    """
    # === CIRCUIT BREAKER: check for AI bubble burst ===
    try:
        from src.bubble_detector import check_bubble_burst
    except ImportError:
        check_bubble_burst = lambda **kw: {"level": "NORMAL", "action": "none"}
    bubble = check_bubble_burst(days=1)

    if bubble["action"] == "liquidate":
        # MELTDOWN: sell everything, buy nothing
        sells = []
        for ticker, pos in current_positions.items():
            entry = pos.get("avg_entry_price", 0)
            price = pos.get("current_price", 0)
            pnl_pct = (price - entry) / entry * 100 if entry > 0 else 0
            sells.append({
                "ticker": ticker,
                "reason": f"MELTDOWN_LIQUIDATE_{bubble['stats'].get('pct_declining',0):.0f}pct_declining",
                "urgency": "critical",
                "pnl_pct": round(pnl_pct, 1),
            })

        try:
            from src.activity_log import log as alog
            alog("recommender", "meltdown_liquidate",
                 positions=len(sells), stats=str(bubble["stats"]))
        except Exception:
            pass

        return {
            "sells": sells,
            "buys": [],
            "holds": [],
            "rankings": [],
            "bubble": bubble,
        }

    min_event = 5
    rankings = get_rankings(min_event=min_event)

    # Load trading exclusions (mega-caps tracked for signals but not bought)
    excluded = set()
    try:
        import yaml
        u = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "universe.yaml")))
        excluded = set(u.get("excluded_from_trading", {}).get("tickers", []))
    except Exception:
        pass

    top_n = [r["ticker"] for r in rankings[:max_pos]]

    sells = []
    buys = []
    holds = []

    # --- SELL DECISIONS ---
    for ticker, pos in current_positions.items():
        entry = pos.get("avg_entry_price", 0)
        peak = pos.get("peak_price", entry)
        price = pos.get("current_price", 0)

        if not price or not entry:
            continue

        pnl_pct = (price - entry) / entry * 100
        drop_from_peak = (price - peak) / peak * 100 if peak > 0 else 0

        sell_reason = None
        urgency = "normal"

        # 1. Hard stop (sector-aware)
        try:
            from src.sector_strategy import get_stop_levels, get_sector_for_ticker
        except ImportError:
            get_stop_levels = lambda t: (-20, -25)
            get_sector_for_ticker = lambda t: "unknown"
        ticker_sector = get_sector_for_ticker(ticker)
        hard_stop, trail_stop = get_stop_levels(ticker_sector)

        if pnl_pct <= hard_stop:
            sell_reason = f"hard_stop_{pnl_pct:.0f}%_({ticker_sector}:{hard_stop:.0f}%)"
            urgency = "urgent"

        # 2. Trailing stop (sector-aware)
        elif drop_from_peak <= trail_stop:
            sell_reason = f"trailing_stop_peak${peak:.0f}_drop{drop_from_peak:.0f}%_({ticker_sector}:{trail_stop:.0f}%)"
            urgency = "urgent"

        # 3. Thesis break (negative events, heat-aware)
        elif not sell_reason:
            sig = check_sell_signals(ticker)
            if sig["should_sell"]:
                sell_reason = sig["reason"]
                urgency = "normal"

        # 4. Universe pruner signals — deteriorating fundamentals
        if not sell_reason:
            try:
                from src.universe_pruner import check_d_tier, check_negative_sentiment, check_no_events, check_delisted
                with db.get_conn() as pconn:
                    pruner_triggers = []
                    t1 = check_d_tier(ticker, pconn)
                    if t1:
                        pruner_triggers.append(t1)
                    t2 = check_negative_sentiment(ticker, pconn)
                    if t2:
                        pruner_triggers.append(t2)
                    t3 = check_no_events(ticker, pconn)
                    if t3:
                        pruner_triggers.append(t3)
                    # 2+ triggers = sell
                    if len(pruner_triggers) >= 2:
                        reasons = "+".join(t["trigger"] for t in pruner_triggers)
                        sell_reason = f"pruner_{reasons}"
                        urgency = "normal"
                    # Critical single trigger (delisted) = immediate sell
                    elif pruner_triggers and any(t["severity"] == "critical" for t in pruner_triggers):
                        sell_reason = f"pruner_{pruner_triggers[0]['trigger']}"
                        urgency = "urgent"
            except Exception:
                pass

        # 5. Smart rotation: only sell weakest held if a stronger candidate needs the slot
        #    Old logic: "not in top N → sell" (too aggressive, sold winners early)
        #    New logic: only rotate if there's a BETTER candidate waiting AND this is the weakest position
        # (rotation is handled below after evaluating all positions)

        if sell_reason:
            sells.append({
                "ticker": ticker,
                "reason": sell_reason,
                "urgency": urgency,
                "pnl_pct": round(pnl_pct, 1),
            })
        else:
            # Find rank
            rank = next((i+1 for i, r in enumerate(rankings) if r["ticker"] == ticker), 99)
            holds.append({
                "ticker": ticker,
                "rank": rank,
                "pnl_pct": round(pnl_pct, 1),
                "note": f"rank#{rank} pnl={pnl_pct:+.1f}%",
            })

    # --- BUY DECISIONS (ranked, executor decides how many it can afford) ---
    held_tickers = set(current_positions.keys()) - set(s["ticker"] for s in sells)

    # --- SMART ROTATION ---
    # Only rotate if: (1) all slots full, (2) a candidate ranks higher than weakest held
    available_slots = max_pos - len(held_tickers)
    if available_slots <= 0 and rankings:
        # Score each held position by its current verdict_score
        held_scores = {}
        ranking_map = {r["ticker"]: r for r in rankings}
        for t in held_tickers:
            if t in ranking_map:
                held_scores[t] = ranking_map[t].get("verdict_score", 0)
            else:
                held_scores[t] = -999  # Not ranked at all = weakest

        # Find candidates that rank higher than weakest held
        weakest_held = sorted(held_scores.items(), key=lambda x: x[1])
        candidates_not_held = [r for r in rankings if r["ticker"] not in held_tickers
                               and r.get("ev_score", 0) >= min_event]

        for candidate in candidates_not_held:
            if not weakest_held:
                break
            weakest_ticker, weakest_score = weakest_held[0]
            cand_score = candidate.get("verdict_score", 0)

            # Only rotate if candidate is meaningfully better (>50% higher score)
            if cand_score > weakest_score * 1.5 and cand_score > 0:
                sells.append({
                    "ticker": weakest_ticker,
                    "reason": f"rotation_for_{candidate['ticker']}_score_{cand_score:.1f}>{weakest_score:.1f}",
                    "urgency": "low",
                    "pnl_pct": 0,
                })
                held_tickers.discard(weakest_ticker)
                weakest_held.pop(0)
                available_slots += 1
            else:
                break  # No more candidates better than weakest held

    for i, r in enumerate(rankings):
        if r["ticker"] in held_tickers:
            continue
        if r["ticker"] in excluded:
            continue
        if r["ev_score"] < min_event:
            continue
        buys.append({
            "ticker": r["ticker"],
            "rank": i + 1,
            "reason": f"rank#{i+1}_ev{r['ev_score']:+.0f}",
            "ev_score": r["ev_score"],
            "verdict_score": r["verdict_score"],
            "thesis": r.get("top_summary", ""),
        })

    # Log
    try:
        from src.activity_log import log as alog
        alog("recommender", "instructions_generated",
             sells=len(sells), buys=len(buys), holds=len(holds),
             sell_tickers=",".join(s["ticker"] for s in sells),
             buy_tickers=",".join(b["ticker"] for b in buys))
    except Exception:
        pass

    return {
        "sells": sells,
        "buys": buys,
        "holds": holds,
        "rankings": [{"ticker": r["ticker"], "ev_score": r["ev_score"],
                      "verdict_score": r["verdict_score"]} for r in rankings[:max_pos]],
        "bubble": bubble,
    }


if __name__ == "__main__":
    picks = get_top_picks(10)
    print("Top 10 Picks:")
    for i, p in enumerate(picks, 1):
        print(f"  {i:2d}. {p['ticker']:6s} [{p['signal_type']:7s}] "
              f"ev={p['ev_score']:+5.0f} mom={p['momentum']:3.0f} "
              f"heat={p['heat']:.1f} ${p['price']:.2f}")
