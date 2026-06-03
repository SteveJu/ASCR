"""Stock Recommender — core ranking and recommendation engine.

This is ASCR's primary output: a ranked list of stocks to buy.
Paper-trader and any other consumer reads this.

Ranking logic:
  1. Verdict score (LLM verdict × conviction) — primary
  2. Event signal strength (avg_delta × capped count)
  3. Evidence score as final tie-breaker
  4. Must have event evidence; momentum alone never qualifies
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

DEFAULT_PORTFOLIO_POLICY = {
    "min_hold_days_before_thesis_sell": 10,
    "trailing_activation_pct": 30,
    "default_trailing_stop_pct": -30,
    "trailing_min_hold_days": 10,
    "trailing_winner_widen_pct": 5,
    "rotation": {
        "enabled": True,
        "min_hold_days": 90,
        "protect_profitable_days": 30,
        "candidate_min_event_score": 12,
        "candidate_min_verdict_score": 6,
        "min_score_ratio": 2.0,
        "min_score_gap": 4.0,
    },
    "add_capital": {
        "enabled": True,
        "min_event_score": 8,
        "min_verdict_score": 6,
        "max_suggestions": 3,
        "suggested_position_pct": 0.10,
    },
}


def _deep_merge(defaults: dict, overrides: dict) -> dict:
    merged = dict(defaults)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _portfolio_policy() -> dict:
    try:
        return _deep_merge(
            DEFAULT_PORTFOLIO_POLICY,
            config.scoring().get("portfolio_policy", {}),
        )
    except Exception:
        return DEFAULT_PORTFOLIO_POLICY


def _days_held(pos: dict) -> int:
    entry_date = pos.get("entry_date") or pos.get("date")
    if not entry_date:
        return 999
    try:
        entry_dt = datetime.strptime(str(entry_date)[:10], "%Y-%m-%d")
        return max(0, (datetime.now() - entry_dt).days)
    except ValueError:
        return 999


def _pnl_pct(pos: dict) -> float:
    entry = pos.get("avg_entry_price", 0) or pos.get("entry_price", 0) or 0
    price = pos.get("current_price", 0) or 0
    return (price - entry) / entry * 100 if entry > 0 and price > 0 else 0.0


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
      ticker, ev_score, verdict_score, ev_count, event_types, heat,
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
        price_confirmation = sc.get("momentum", 0) or 0

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
                # Schema keeps momentum_score as a price-confirmation diagnostic.
                # It is not used for live recommendation ranking.
                "momentum": price_confirmation,
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


def get_portfolio_instructions(
    current_positions: dict,
    max_pos: int = 10,
    cash_available: float | None = None,
    portfolio_value: float | None = None,
    target_position_pct: float = 0.10,
) -> dict:
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
            "add_capital_suggestions": [],
            "rankings": [],
            "bubble": bubble,
        }

    min_event = 5
    rankings = get_rankings(min_event=min_event)
    policy = _portfolio_policy()

    # Load trading exclusions (mega-caps tracked for signals but not bought)
    excluded = set()
    try:
        import yaml
        u = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "universe.yaml")))
        excluded = set(u.get("excluded_from_trading", {}).get("tickers", []))
    except Exception:
        pass

    sells = []
    buys = []
    holds = []
    add_capital_suggestions = []

    # --- SELL DECISIONS ---
    for ticker, pos in current_positions.items():
        entry = pos.get("avg_entry_price", 0)
        peak = pos.get("peak_price", entry)
        price = pos.get("current_price", 0)

        if not price or not entry:
            continue

        pnl_pct = (price - entry) / entry * 100
        drop_from_peak = (price - peak) / peak * 100 if peak > 0 else 0
        peak_pnl_pct = (peak - entry) / entry * 100 if entry > 0 else 0
        days_held = _days_held(pos)

        sell_reason = None
        urgency = "normal"

        # 1. Hard stop (sector-aware)
        try:
            from src.sector_strategy import get_sector_strategy, get_sector_for_ticker
        except ImportError:
            get_sector_strategy = lambda t: {"stop_loss_pct": -20, "trailing_stop_pct": -30}
            get_sector_for_ticker = lambda t: "unknown"
        ticker_sector = get_sector_for_ticker(ticker)
        sector_policy = get_sector_strategy(ticker_sector)
        hard_stop = float(sector_policy.get("stop_loss_pct", -20))
        if ticker_sector == "unknown":
            trail_stop = float(policy.get("default_trailing_stop_pct", -30))
        else:
            trail_stop = float(sector_policy.get("trailing_stop_pct", -25))
        trail_activation = float(
            sector_policy.get("trailing_activation_pct", policy.get("trailing_activation_pct", 30))
        )
        trailing_min_hold = int(policy.get("trailing_min_hold_days", 10))
        if peak_pnl_pct >= 50:
            trail_stop -= float(policy.get("trailing_winner_widen_pct", 5))

        if pnl_pct <= hard_stop:
            sell_reason = f"hard_stop_{pnl_pct:.0f}%_({ticker_sector}:{hard_stop:.0f}%)"
            urgency = "urgent"

        # 2. Trailing stop (sector-aware)
        elif (
            days_held >= trailing_min_hold
            and peak_pnl_pct >= trail_activation
            and drop_from_peak <= trail_stop
        ):
            sell_reason = (
                f"trailing_stop_peak${peak:.0f}_drop{drop_from_peak:.0f}%_"
                f"peak_gain{peak_pnl_pct:.0f}%_held{days_held}d_"
                f"({ticker_sector}:{trail_stop:.0f}%)"
            )
            urgency = "urgent"

        # 3. Thesis break (negative events, heat-aware)
        elif not sell_reason:
            sig = check_sell_signals(ticker)
            if sig["should_sell"]:
                severe_thesis_break = sig.get("neg_ev", 0) <= -20 or sig.get("risk", 0) >= 25
                min_thesis_hold = int(policy.get("min_hold_days_before_thesis_sell", 10))
                if severe_thesis_break or days_held >= min_thesis_hold:
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
                    # 2+ triggers = sell after the thesis has had a short grace period.
                    if (
                        len(pruner_triggers) >= 2
                        and days_held >= int(policy.get("min_hold_days_before_thesis_sell", 10))
                    ):
                        reasons = "+".join(t["trigger"] for t in pruner_triggers)
                        sell_reason = f"pruner_{reasons}"
                        urgency = "normal"
                    # Critical single trigger (delisted) = immediate sell
                    elif pruner_triggers and any(t["severity"] == "critical" for t in pruner_triggers):
                        sell_reason = f"pruner_{pruner_triggers[0]['trigger']}"
                        urgency = "urgent"
            except Exception:
                pass

        # 5. Patient rotation: only sell weakest held if a stronger candidate needs the slot
        #    Earlier rank-only rotation was too aggressive in strong tape.
        #    Current logic waits for an aged weak thesis and a much stronger fresh event.
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
                "note": f"rank#{rank} pnl={pnl_pct:+.1f}% held={days_held}d",
            })

    # --- BUY DECISIONS (ranked, executor decides how many it can afford) ---
    held_tickers = set(current_positions.keys()) - set(s["ticker"] for s in sells)

    # --- PATIENT ROTATION ---
    # Only rotate if: (1) all slots full, (2) the replacement is a much stronger
    # fresh event, and (3) the weakest held position has had enough time to work.
    available_slots = max_pos - len(held_tickers)
    rotation_policy = policy.get("rotation", {})
    if available_slots <= 0 and rankings and rotation_policy.get("enabled", True):
        held_meta = {}
        ranking_map = {r["ticker"]: r for r in rankings}
        for idx, r in enumerate(rankings):
            r["rank"] = idx + 1
        for t in held_tickers:
            r = ranking_map.get(t)
            pos = current_positions.get(t, {})
            held_meta[t] = {
                "score": r.get("verdict_score", 0) if r else -999,
                "ev_score": r.get("ev_score", 0) if r else 0,
                "rank": r.get("rank", 99) if r else 99,
                "pnl_pct": _pnl_pct(pos),
                "days_held": _days_held(pos),
            }

        min_rotation_days = int(rotation_policy.get("min_hold_days", 90))
        protect_profitable_days = int(rotation_policy.get("protect_profitable_days", 30))
        rotatable = []
        for ticker, meta in held_meta.items():
            if meta["days_held"] < min_rotation_days:
                continue
            if meta["pnl_pct"] > 0 and meta["days_held"] < protect_profitable_days:
                continue
            if meta["pnl_pct"] > 0 and meta["score"] > 0:
                continue
            rotatable.append((ticker, meta))

        weakest_held = sorted(rotatable, key=lambda x: (x[1]["score"], x[1]["pnl_pct"]))
        candidates_not_held = [r for r in rankings if r["ticker"] not in held_tickers
                               and r.get("ev_score", 0) >= min_event]

        for candidate in candidates_not_held:
            if not weakest_held:
                break
            weakest_ticker, weakest = weakest_held[0]
            weakest_score = weakest["score"]
            cand_score = candidate.get("verdict_score", 0)
            cand_ev = candidate.get("ev_score", 0)
            score_gap = cand_score - max(weakest_score, 0)
            ratio_ok = weakest_score <= 0 or cand_score >= weakest_score * float(rotation_policy.get("min_score_ratio", 2.0))
            gap_ok = score_gap >= float(rotation_policy.get("min_score_gap", 4.0))
            candidate_ok = (
                cand_ev >= float(rotation_policy.get("candidate_min_event_score", 12))
                and cand_score >= float(rotation_policy.get("candidate_min_verdict_score", 6))
            )

            if candidate_ok and ratio_ok and gap_ok:
                sells.append({
                    "ticker": weakest_ticker,
                    "reason": (
                        f"rotation_for_{candidate['ticker']}_score_{cand_score:.1f}>"
                        f"{weakest_score:.1f}_ev{cand_ev:.0f}_held{weakest['days_held']}d"
                    ),
                    "urgency": "low",
                    "pnl_pct": round(weakest["pnl_pct"], 1),
                })
                held_tickers.discard(weakest_ticker)
                holds = [h for h in holds if h["ticker"] != weakest_ticker]
                weakest_held.pop(0)
                available_slots += 1
            else:
                break  # No more candidates better than weakest held

    buy_slots = max(0, max_pos - len(held_tickers))
    for i, r in enumerate(rankings):
        if len(buys) >= buy_slots:
            break
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

    add_policy = policy.get("add_capital", {})
    if add_policy.get("enabled", True):
        if portfolio_value is None:
            portfolio_value = 0.0
            for pos in current_positions.values():
                qty = pos.get("quantity", 0) or pos.get("shares", 0) or 0
                price = pos.get("current_price", 0) or pos.get("price", 0) or 0
                portfolio_value += qty * price
        if cash_available is None:
            cash_available = 0.0
        suggested_position_pct = float(add_policy.get("suggested_position_pct", target_position_pct))
        target_add_cash = max(portfolio_value * suggested_position_pct - cash_available, 0)
        buy_tickers = {b["ticker"] for b in buys}
        min_add_ev = float(add_policy.get("min_event_score", 8))
        min_add_verdict = float(add_policy.get("min_verdict_score", 6))
        max_suggestions = int(add_policy.get("max_suggestions", 3))
        for i, r in enumerate(rankings):
            if len(add_capital_suggestions) >= max_suggestions:
                break
            ticker = r["ticker"]
            if ticker in held_tickers or ticker in buy_tickers or ticker in excluded:
                continue
            if r.get("ev_score", 0) < min_add_ev or r.get("verdict_score", 0) < min_add_verdict:
                continue
            add_capital_suggestions.append({
                "ticker": ticker,
                "rank": i + 1,
                "reason": (
                    f"add_capital_candidate_rank#{i+1}_ev{r['ev_score']:+.0f}_"
                    f"verdict{r['verdict_score']:.1f}"
                ),
                "ev_score": r["ev_score"],
                "verdict_score": r["verdict_score"],
                "suggested_cash": round(target_add_cash, 2),
                "thesis": r.get("top_summary", ""),
            })

    # Log
    try:
        from src.activity_log import log as alog
        alog("recommender", "instructions_generated",
             sells=len(sells), buys=len(buys), holds=len(holds),
             add_capital=len(add_capital_suggestions),
             sell_tickers=",".join(s["ticker"] for s in sells),
             buy_tickers=",".join(b["ticker"] for b in buys))
    except Exception:
        pass

    return {
        "sells": sells,
        "buys": buys, 
        "holds": holds,
        "add_capital_suggestions": add_capital_suggestions,
        "rankings": [{"ticker": r["ticker"], "ev_score": r["ev_score"], 
                      "verdict_score": r["verdict_score"]} for r in rankings[:max_pos]],
        "bubble": bubble,
    }


if __name__ == "__main__":
    picks = get_top_picks(10)
    print("Top 10 Picks:")
    for i, p in enumerate(picks, 1):
        print(f"  {i:2d}. {p['ticker']:6s} [{p['signal_type']:7s}] "
              f"verdict={p['verdict_score']:+4.1f} ev={p['ev_score']:+5.0f} "
              f"heat={p['heat']:.1f} ${p['price']:.2f}")
