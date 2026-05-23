"""Scoring engine V2 — Evidence, Asymmetry, Momentum, Risk + rating + tracking_priority."""
import json
from datetime import datetime
from src import config, db
from src.price_fetcher import get_ticker_info
from src.rating import compute_rating, compute_tracking_priority
from src.utils import get_logger

logger = get_logger("scoring")


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
            recent_events = db.get_events(ticker, days=7)
            tracking_priority = compute_tracking_priority(rating, has_position, recent_events)

            # Build reason and next_trigger
            reason = _build_reason(rating, evidence, asymmetry, momentum, risk, info)
            next_trigger = _build_next_trigger(ticker, rating, info)

            details = {
                "info": {k: v for k, v in info.items() if v is not None},
                "evidence": ev_details,
                "asymmetry": as_details,
                "momentum": mo_details,
                "risk": ri_details,
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
