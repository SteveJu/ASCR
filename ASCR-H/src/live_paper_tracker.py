"""Live paper tracker — reads ascr scores daily and logs live decisions.

Runs as part of the daily ascr_h pipeline.
Every scored ticker gets a decision: BUY, HOLD, SELL, TRIM, or NO_BUY.
"""
from datetime import datetime
from src import db, config
from src.decision_logger import log_decision, get_benchmark_price
from src.broker_simulator import get_current_price
from src.utils import get_logger

logger = get_logger("live_tracker")


def log_daily_decisions():
    """Log all decisions from today's scoring cycle."""
    cfg = config.load()
    scores = db.read_radar_scores()
    if not scores:
        logger.warning("No scores available")
        return 0

    positions = db.get_all_positions("open")
    held_tickers = {p["ticker"] for p in positions}
    pos_map = {p["ticker"]: p for p in positions}

    decisions_logged = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for s in scores:
        ticker = s["ticker"]
        rating = s.get("rating", "D")
        sizing_pct = cfg["sizing"].get(rating, 0)

        price = get_current_price(ticker)
        if price <= 0:
            continue

        score_dict = {
            "rating": rating,
            "evidence": s.get("evidence_score", 0),
            "asymmetry": s.get("asymmetry_score", 0),
            "momentum": s.get("momentum_score", 0),
            "risk": s.get("risk_score", 0),
            "opportunity": s.get("opportunity_score", 0),
            "tracking_priority": s.get("tracking_priority", ""),
        }

        if ticker in held_tickers:
            # Held position — HOLD or exit decision
            pos = pos_map[ticker]
            pnl_pct = (price - pos["avg_entry_price"]) / pos["avg_entry_price"] * 100
            days_held = 0
            try:
                days_held = (datetime.strptime(today, "%Y-%m-%d") -
                            datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days
            except (ValueError, TypeError):
                pass

            # Check exit conditions
            if pnl_pct <= cfg["exits"]["hard_stop_loss_pct"]:
                log_decision(ticker, "SELL", price, score_dict,
                            reason=f"hard_stop_loss_{pnl_pct:.0f}pct", mode="live_paper")
            elif days_held > cfg["exits"]["time_stop_days"] and rating not in ("S", "A"):
                log_decision(ticker, "SELL", price, score_dict,
                            reason=f"time_stop_{days_held}d_rating_{rating}", mode="live_paper")
            elif pnl_pct >= 50:
                log_decision(ticker, "TRIM", price, score_dict,
                            reason=f"profit_50pct_{pnl_pct:.0f}pct", mode="live_paper")
            elif pnl_pct >= 25:
                log_decision(ticker, "TRIM", price, score_dict,
                            reason=f"profit_25pct_{pnl_pct:.0f}pct", mode="live_paper")
            else:
                log_decision(ticker, "HOLD", price, score_dict,
                            reason=f"holding_{days_held}d_pnl_{pnl_pct:+.1f}pct", mode="live_paper")
        else:
            # Not held — BUY or NO_BUY
            if sizing_pct > 0:
                account = db.get_account()
                if account and account["cash"] > 500:
                    log_decision(ticker, "BUY", price, score_dict,
                                reason=f"rating_{rating}_opp_{s.get('opportunity_score',0):.0f}",
                                mode="live_paper")
                else:
                    log_decision(ticker, "NO_BUY", price, score_dict,
                                reason=f"rating_{rating}_insufficient_cash", mode="live_paper")
            else:
                log_decision(ticker, "NO_BUY", price, score_dict,
                            reason=f"rating_{rating}_no_sizing", mode="live_paper")

        decisions_logged += 1

    logger.info(f"Logged {decisions_logged} live decisions for {today}")
    return decisions_logged
