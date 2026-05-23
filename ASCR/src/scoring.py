"""Scoring engine V2 — Evidence, Asymmetry, Momentum, Risk + rating + tracking_priority."""
import json
from datetime import datetime
from src import config, db
from src.price_fetcher import get_ticker_info
from src.rating import compute_rating, compute_tracking_priority
from src.scoring_feedback import feedback_adjustment
from src.utils import get_logger

logger = get_logger("scoring")


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _parse_event_date(event: dict):
    raw = event.get("date") or event.get("event_date") or event.get("created_at")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw)
        except Exception:
            return None
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
    return None


def _event_alpha_score(events: list, event_cfg: dict = None, as_of: datetime = None) -> tuple:
    """Convert recent structured events into score adjustments.

    The old engine mostly scored static fundamentals and generic price state.
    This layer is where ASCR's actual edge should live: time-decayed, source-
    weighted, novelty-aware public events with priced-in penalties.
    """
    event_cfg = event_cfg or {}
    as_of = as_of or datetime.now()
    half_life = max(float(event_cfg.get("half_life_days", 14)), 1.0)
    min_conf = float(event_cfg.get("min_confidence", 0.25))
    source_weights = event_cfg.get("source_weights", {})
    event_type_weights = event_cfg.get("event_type_weights", {})
    verdict_scores = event_cfg.get("verdict_scores", {})
    conviction_weights = event_cfg.get("conviction_weights", {})

    evidence_adj = 0.0
    asymmetry_adj = 0.0
    risk_adj = 0.0
    used = 0
    skipped = 0
    seen = set()
    top_events = []

    for event in events or []:
        key = event.get("hash") or event.get("url") or event.get("headline") or event.get("title")
        duplicate_penalty = 1.0
        if key:
            if key in seen:
                duplicate_penalty = 0.35
            seen.add(key)

        conf = event.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.60
        except (TypeError, ValueError):
            conf = 0.60
        if conf < min_conf:
            skipped += 1
            continue

        dt = _parse_event_date(event)
        age_days = max((as_of - dt).days, 0) if dt else 0
        decay = 0.5 ** (age_days / half_life)

        source = str(event.get("source") or "other").lower()
        event_type = str(event.get("event_type") or "other").lower()
        source_weight = float(source_weights.get(source, source_weights.get("other", 0.75)))
        type_weight = float(event_type_weights.get(event_type, event_type_weights.get("other", 0.70)))

        priced_in = event.get("priced_in_pct")
        try:
            priced_in = float(priced_in) if priced_in is not None else 25.0
        except (TypeError, ValueError):
            priced_in = 25.0
        priced_in_multiplier = max(0.25, 1.0 - priced_in / 100.0)

        verdict = str(event.get("verdict") or "").upper()
        conviction = str(event.get("conviction") or "").upper()
        verdict_delta = float(verdict_scores.get(verdict, 0))
        conviction_weight = float(conviction_weights.get(conviction, 0.65 if verdict_delta else 1.0))

        try:
            ev_delta = float(event.get("evidence_delta") or 0)
        except (TypeError, ValueError):
            ev_delta = 0.0
        try:
            as_delta = float(event.get("asymmetry_delta") or 0)
        except (TypeError, ValueError):
            as_delta = 0.0
        try:
            ri_delta = float(event.get("risk_delta") or 0)
        except (TypeError, ValueError):
            ri_delta = 0.0

        multiplier = decay * source_weight * type_weight * conf * duplicate_penalty
        positive_multiplier = multiplier * priced_in_multiplier

        ev_component = ev_delta * (positive_multiplier if ev_delta > 0 else multiplier)
        ev_component += verdict_delta * conviction_weight * positive_multiplier
        as_component = as_delta * (positive_multiplier if as_delta > 0 else multiplier)
        ri_component = ri_delta * multiplier

        evidence_adj += ev_component
        asymmetry_adj += as_component
        risk_adj += ri_component
        used += 1

        headline = event.get("headline") or event.get("title") or event.get("summary") or event_type
        top_events.append({
            "headline": str(headline)[:120],
            "type": event_type,
            "source": source,
            "age_days": age_days,
            "evidence_adj": round(ev_component, 2),
            "asymmetry_adj": round(as_component, 2),
            "risk_adj": round(ri_component, 2),
            "priced_in_pct": priced_in,
        })

    max_ev = float(event_cfg.get("max_evidence_adjustment", 45))
    max_as = float(event_cfg.get("max_asymmetry_adjustment", 35))
    max_ri = float(event_cfg.get("max_risk_adjustment", 40))
    adjustments = {
        "evidence": max(-max_ev, min(max_ev, evidence_adj)),
        "asymmetry": max(-max_as, min(max_as, asymmetry_adj)),
        "risk": max(-max_ri, min(max_ri, risk_adj)),
    }
    details = {
        "used_events": used,
        "skipped_low_confidence": skipped,
        "raw_adjustments": {
            "evidence": round(evidence_adj, 2),
            "asymmetry": round(asymmetry_adj, 2),
            "risk": round(risk_adj, 2),
        },
        "adjustments": {k: round(v, 2) for k, v in adjustments.items()},
        "top_events": sorted(top_events, key=lambda e: abs(e["evidence_adj"]) + abs(e["risk_adj"]), reverse=True)[:5],
    }
    return adjustments, details


def _momentum_score(ticker: str, prices: list, info: dict) -> tuple:
    """Calculate momentum score from price/volume data."""
    score = 0
    details = {}

    if len(prices) < 10:
        return 0, {"error": "insufficient price data"}

    prices_asc = sorted(prices, key=lambda x: x["date"])
    closes = [p["close"] for p in prices_asc if p["close"]]
    volumes = [p["volume"] for p in prices_asc if p["volume"]]

    if not closes or not volumes:
        return 0, {"error": "no close/volume data"}

    current = closes[-1]

    # 1. Price trend (20-day SMA)
    if len(closes) >= 20:
        sma20 = sum(closes[-20:]) / 20
        if current > sma20:
            trend_pct = (current - sma20) / sma20 * 100
            score += min(25, trend_pct * 2.5)
            details["above_sma20"] = f"+{trend_pct:.1f}%"
        else:
            details["above_sma20"] = f"{(current - sma20) / sma20 * 100:.1f}%"

    # 2. Near 52-week high
    high_52w = info.get("fifty_two_week_high")
    if high_52w and high_52w > 0:
        pct_of_high = current / high_52w
        if pct_of_high >= 0.95:
            score += 25
            details["near_52w_high"] = f"{pct_of_high*100:.0f}% ✅"
        elif pct_of_high >= 0.85:
            score += 15
            details["near_52w_high"] = f"{pct_of_high*100:.0f}%"
        else:
            details["near_52w_high"] = f"{pct_of_high*100:.0f}%"

    # 3. Volume surge
    if len(volumes) >= 20:
        avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) > 20 else sum(volumes[:-1]) / max(len(volumes)-1, 1)
        if avg_vol > 0:
            vol_ratio = volumes[-1] / avg_vol
            if vol_ratio >= 3:
                score += 25
                details["volume_surge"] = f"{vol_ratio:.1f}x 🔥"
            elif vol_ratio >= 2:
                score += 15
                details["volume_surge"] = f"{vol_ratio:.1f}x"
            elif vol_ratio >= 1.5:
                score += 5
                details["volume_surge"] = f"{vol_ratio:.1f}x"
            else:
                details["volume_surge"] = f"{vol_ratio:.1f}x"

    # 4. 20-day return
    if len(closes) >= 20:
        ret_20d = (closes[-1] - closes[-20]) / closes[-20] * 100
        if ret_20d > 10:
            score += 25
        elif ret_20d > 5:
            score += 15
        elif ret_20d > 0:
            score += 5
        details["return_20d"] = f"{ret_20d:+.1f}%"

    return min(100, score), details


def _asymmetry_score(ticker: str, info: dict) -> tuple:
    score = 0
    details = {}

    mcap = info.get("market_cap")
    if mcap:
        if mcap < 2e9:
            score += 30
            details["market_cap"] = f"${mcap/1e9:.1f}B (micro)"
        elif mcap < 10e9:
            score += 20
            details["market_cap"] = f"${mcap/1e9:.1f}B (small)"
        elif mcap < 50e9:
            score += 10
            details["market_cap"] = f"${mcap/1e9:.1f}B (mid)"
        else:
            details["market_cap"] = f"${mcap/1e9:.1f}B (large)"

    analyst_count = info.get("analyst_count")
    if analyst_count is not None:
        if analyst_count < 5:
            score += 20
            details["analyst_coverage"] = f"{analyst_count} (low)"
        elif analyst_count < 10:
            score += 10
            details["analyst_coverage"] = f"{analyst_count} (moderate)"
        else:
            details["analyst_coverage"] = f"{analyst_count}"

    fwd_pe = info.get("forward_pe")
    trail_pe = info.get("pe_ratio")
    if fwd_pe and trail_pe and trail_pe > 0:
        pe_compression = (trail_pe - fwd_pe) / trail_pe * 100
        if pe_compression > 30:
            score += 20
            details["pe_compression"] = f"{pe_compression:.0f}% (strong growth implied)"
        elif pe_compression > 15:
            score += 10
            details["pe_compression"] = f"{pe_compression:.0f}%"

    rev_growth = info.get("revenue_growth")
    if rev_growth is not None:
        if rev_growth > 0.5:
            score += 20
            details["revenue_growth"] = f"{rev_growth*100:.0f}% (hyper)"
        elif rev_growth > 0.2:
            score += 10
            details["revenue_growth"] = f"{rev_growth*100:.0f}%"
        else:
            details["revenue_growth"] = f"{rev_growth*100:.0f}%"

    low_52w = info.get("fifty_two_week_low")
    high_52w = info.get("fifty_two_week_high")
    if low_52w and high_52w and high_52w > low_52w:
        range_pct = (high_52w - low_52w) / low_52w * 100
        if range_pct > 100:
            score += 10
            details["52w_range"] = f"{range_pct:.0f}% (wide)"

    return min(100, score), details


def _risk_score(ticker: str, info: dict) -> tuple:
    score = 0
    details = {}

    dte = info.get("debt_to_equity")
    if dte is not None:
        if dte > 200:
            score += 25
            details["debt_to_equity"] = f"{dte:.0f}% (very high)"
        elif dte > 100:
            score += 15
            details["debt_to_equity"] = f"{dte:.0f}% (high)"
        elif dte > 50:
            score += 5
            details["debt_to_equity"] = f"{dte:.0f}%"
        else:
            details["debt_to_equity"] = f"{dte:.0f}% (healthy)"

    gm = info.get("gross_margin")
    if gm is not None:
        if gm < 0:
            score += 25
            details["gross_margin"] = f"{gm*100:.1f}% (negative!)"
        elif gm < 0.2:
            score += 10
            details["gross_margin"] = f"{gm*100:.1f}% (thin)"
        else:
            details["gross_margin"] = f"{gm*100:.1f}%"

    pm = info.get("profit_margin")
    if pm is not None and pm < 0:
        score += 15
        details["profit_margin"] = f"{pm*100:.1f}% (unprofitable)"

    rev_growth = info.get("revenue_growth")
    if rev_growth is not None and rev_growth < 0:
        score += 20
        details["revenue_declining"] = f"{rev_growth*100:.1f}%"

    sr = info.get("short_ratio")
    if sr is not None and sr > 5:
        score += 15
        details["short_ratio"] = f"{sr:.1f} days (high)"

    return min(100, score), details


def _evidence_score_basic(ticker: str, info: dict) -> tuple:
    score = 0
    details = {}

    rev_growth = info.get("revenue_growth")
    if rev_growth is not None:
        if rev_growth > 0.5:
            score += 30
            details["revenue_growth"] = f"{rev_growth*100:.0f}% (strong acceleration)"
        elif rev_growth > 0.2:
            score += 20
            details["revenue_growth"] = f"{rev_growth*100:.0f}%"
        elif rev_growth > 0.1:
            score += 10
            details["revenue_growth"] = f"{rev_growth*100:.0f}%"

    gm = info.get("gross_margin")
    if gm is not None and gm > 0.4:
        score += 15
        details["gross_margin_healthy"] = f"{gm*100:.1f}%"

    pm = info.get("profit_margin")
    if pm is not None and pm > 0.1:
        score += 15
        details["profitable"] = f"{pm*100:.1f}%"

    fwd_pe = info.get("forward_pe")
    trail_pe = info.get("pe_ratio")
    if fwd_pe and trail_pe and fwd_pe < trail_pe * 0.85:
        score += 20
        details["guidance_implied_raise"] = f"Fwd PE {fwd_pe:.1f} vs Trailing {trail_pe:.1f}"

    details["note"] = "Basic scoring. LLM text analysis not yet applied."
    return min(100, score), details


def compute_scores(tickers: list = None):
    """Compute all scores and save to DB."""
    if tickers is None:
        tickers = config.all_tickers()

    cfg = config.scoring()
    weights = cfg.get("opportunity_weights", {})
    today = datetime.now().strftime("%Y-%m-%d")

    # Regime-aware weight override
    try:
        from src.market_regime import detect_regime
        regime = detect_regime()
        regime_key = regime.get("regime", "neutral")
        regime_weights = cfg.get("regime_weights", {}).get(regime_key)
        if regime_weights:
            weights = regime_weights
            logger.info(f"Using {regime_key} regime weights: {weights}")
    except Exception:
        pass  # fall back to default weights

    # Check which tickers have open positions
    open_positions = db.get_open_positions()
    held_tickers = {p["ticker"] for p in open_positions}

    logger.info(f"Scoring {len(tickers)} tickers ({len(held_tickers)} with positions)...")
    results = []

    for ticker in tickers:
        try:
            prices = db.get_prices(ticker, days=60)
            info = get_ticker_info(ticker)

            evidence, ev_details = _evidence_score_basic(ticker, info)
            asymmetry, as_details = _asymmetry_score(ticker, info)
            momentum, mo_details = _momentum_score(ticker, prices, info)
            risk, ri_details = _risk_score(ticker, info)
            base_scores = {
                "evidence": evidence,
                "asymmetry": asymmetry,
                "momentum": momentum,
                "risk": risk,
            }

            event_cfg = cfg.get("event_alpha", {})
            recent_events = db.get_events(ticker, days=int(event_cfg.get("lookback_days", 45)))
            event_details = {"enabled": False}
            if event_cfg.get("enabled", True):
                event_adj, event_details = _event_alpha_score(recent_events, event_cfg)
                event_details["enabled"] = True
                evidence = _clamp(evidence + event_adj["evidence"])
                asymmetry = _clamp(asymmetry + event_adj["asymmetry"])
                risk = _clamp(risk + event_adj["risk"])

            feedback_details = {"enabled": False}
            feedback_cfg = cfg.get("feedback_alpha", {})
            if feedback_cfg.get("enabled", True):
                provisional_opp = (
                    evidence * weights.get("evidence", 0.35) +
                    asymmetry * weights.get("asymmetry", 0.35) +
                    momentum * weights.get("momentum", 0.15) +
                    risk * weights.get("risk", -0.15)
                )
                provisional_rating = compute_rating(provisional_opp, evidence, asymmetry, risk, momentum)
                feedback_adj, feedback_details = feedback_adjustment(
                    ticker, provisional_rating, recent_events, feedback_cfg
                )
                evidence = _clamp(evidence + feedback_adj["evidence"])
                asymmetry = _clamp(asymmetry + feedback_adj["asymmetry"])
                risk = _clamp(risk + feedback_adj["risk"])

            # Opportunity Score
            opp = (
                evidence * weights.get("evidence", 0.35) +
                asymmetry * weights.get("asymmetry", 0.35) +
                momentum * weights.get("momentum", 0.15) +
                risk * weights.get("risk", -0.15)
            )

            # Rating (V2 — multi-dimensional check)
            rating = compute_rating(opp, evidence, asymmetry, risk, momentum)

            # Tracking priority
            has_position = ticker in held_tickers
            tracking_priority = compute_tracking_priority(rating, has_position, recent_events[:10])

            # Build reason and next_trigger
            reason = _build_reason(rating, evidence, asymmetry, momentum, risk, info)
            next_trigger = _build_next_trigger(ticker, rating, info)

            details = {
                "info": {k: v for k, v in info.items() if v is not None},
                "base_scores": base_scores,
                "evidence": ev_details,
                "asymmetry": as_details,
                "momentum": mo_details,
                "risk": ri_details,
                "event_alpha": event_details,
                "feedback_alpha": feedback_details,
            }

            db.upsert_score(ticker, today, evidence, asymmetry, momentum, risk, opp, rating,
                           tracking_priority, reason, next_trigger, json.dumps(details))

            # Also update ticker table
            db.upsert_ticker(ticker, info.get("name", ""), info.get("industry", ""))

            results.append({
                "ticker": ticker,
                "name": info.get("name", ticker),
                "evidence": evidence,
                "asymmetry": asymmetry,
                "momentum": momentum,
                "risk": risk,
                "opportunity": round(opp, 1),
                "rating": rating,
                "tracking_priority": tracking_priority,
                "reason": reason,
                "next_trigger": next_trigger,
            })

            logger.info(f"  {ticker}: E={evidence} A={asymmetry} M={momentum} R={risk} → Opp={opp:.1f} ({rating}) [{tracking_priority}]")

        except Exception as e:
            logger.error(f"Error scoring {ticker}: {e}")

    results.sort(key=lambda x: x["opportunity"], reverse=True)
    return results


def _build_reason(rating, evidence, asymmetry, momentum, risk, info) -> str:
    """Build human-readable reason for the rating."""
    parts = []
    if evidence >= 70:
        parts.append("strong evidence")
    elif evidence >= 50:
        parts.append("moderate evidence")
    if asymmetry >= 60:
        parts.append("high asymmetry")
    if momentum >= 60:
        parts.append("strong momentum")
    if risk >= 30:
        parts.append(f"elevated risk ({risk})")

    mcap = info.get("market_cap")
    if mcap and mcap < 10e9:
        parts.append(f"small cap (${mcap/1e9:.1f}B)")

    rev_growth = info.get("revenue_growth")
    if rev_growth and rev_growth > 0.3:
        parts.append(f"revenue growing {rev_growth*100:.0f}%")

    return "; ".join(parts) if parts else "insufficient data"


def _build_next_trigger(ticker, rating, info) -> str:
    """Suggest what to watch for next."""
    if rating in ("S", "A"):
        return "Monitor for pullback entry or earnings confirmation"
    elif rating == "B":
        return "Wait for next earnings or major contract announcement"
    elif rating == "C":
        return "Need evidence of real revenue or margin improvement"
    else:
        return "Skip unless major positive catalyst"


if __name__ == "__main__":
    results = compute_scores()
    print("\n=== SCORES ===")
    for r in results:
        print(f"{r['rating']} [{r['tracking_priority']:6s}] | {r['ticker']:6s} | Opp={r['opportunity']:5.1f} | E={r['evidence']:3.0f} A={r['asymmetry']:3.0f} M={r['momentum']:3.0f} R={r['risk']:3.0f} | {r['reason']}")
