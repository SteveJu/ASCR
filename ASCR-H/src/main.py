"""ASCR-H — CLI with full Strategy Validation."""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, config
from src.quote_provider import get_live_quote
from src.utils import get_logger

logger = get_logger("ASCR-H")


def _position_status_rows(positions, quote_func=get_live_quote):
    rows = []
    total_value = 0
    total_prev_value = 0
    for position in positions:
        quote = quote_func(position["ticker"])
        qty = position["quantity"]
        current_value = position.get("current_value", 0) or 0
        price = quote.get("price") or (current_value / qty if qty else 0)
        value = qty * price if price > 0 else current_value
        cost = position["cost_basis"]
        pnl = (value - cost) / cost * 100 if cost > 0 else 0
        prev_close = quote.get("previous_close")
        if prev_close and prev_close > 0 and price > 0:
            day_pct = (price - prev_close) / prev_close * 100
            day = f"{day_pct:+.1f}%"
            total_prev_value += qty * prev_close
        else:
            day = "n/a"
            total_prev_value += value
        rows.append(
            f"\n{position['ticker']:8s} {qty:8.1f} ${position['avg_entry_price']:7.2f} "
            f"${value:9,.0f} {pnl:+7.1f}% {day:>8s}"
        )
        total_value += value
    return rows, total_value, total_prev_value


def cmd_init(args):
    cfg = config.load()
    db.init_db()
    cash = args.cash or cfg["initial_cash"]
    db.init_account(cash)
    from src.decision_logger import init_decision_tables
    init_decision_tables()
    print(f"\nAccount initialized with ${cash:,.0f}")


def cmd_run_daily(args):
    """Daily momentum sprint: check exits, fill slots, log decisions, evaluate, report."""
    from src.event_trader import run_daily as event_run, format_daily_telegram
    from src.outcome_evaluator import evaluate_outcomes
    from src.decision_quality import score_all_pending

    logger.info("=" * 50)
    logger.info("Event Trader — Daily Run")
    logger.info("=" * 50)

    # Run event pipeline (results push to ASCR Bot bot via ASCR)
    try:
        sys.path.insert(0, os.environ.get("ASCR_PROJECT_DIR", "../ASCR"))
        from src.event_pipeline import run_pipeline
        events = run_pipeline()
        logger.info(f"Event pipeline: {len(events)} actionable events")
    except Exception as e:
        logger.warning(f"Event pipeline: {e}")

    result = event_run()
    if "error" in result:
        print(f"\nError: {result['error']}")
        return

    total_eq = result['total_equity']
    ret_pct = result['return_pct']
    print("\n💰 Total: ${:,.0f} ({:+.1f}%)".format(total_eq, ret_pct))
    cash_val = result['cash']
    n_pos = result['num_positions']
    print("Cash: ${:,.0f} | Positions: {}".format(cash_val, n_pos))
    for a in result.get("actions", []):
        if a["type"] == "BUY":
            print(f"\n  🟢 BUY {a['ticker']} {a['shares']:.1f}sh @ ${a['price']:.2f}")
        else:
            print(f"\n  🔴 SELL {a['ticker']} @ ${a['price']:.2f} ({a['pnl_pct']:+.1f}%)")
    for t, p in result.get("positions", {}).items():
        print(f"\n  {'🟢' if p['pnl_pct']>=0 else '🔴'} {t}: {p['pnl_pct']:+.1f}%")

    # Evaluate + score pending outcomes
    try:
        evaluate_outcomes()
        score_all_pending()
    except Exception as e:
        logger.warning(f"Outcome eval: {e}")

    # Push to Telegram
    try:
        from src.telegram_notifier import _send
        msg = format_daily_telegram(result)
        _send(msg)
    except Exception as e:
        logger.warning(f"Telegram daily: {e}")

    # Regime monitor — check kill signals
    try:
        from src.regime_monitor import compute_regime_signals, format_regime_report
        from src.telegram_notifier import send_regime_alert
        regime = compute_regime_signals()
        if regime["recommendation"] != "continue":
            send_regime_alert(format_regime_report(regime))
            logger.warning(f"Regime: {regime['recommendation']} — {regime['recommendation_message']}")
    except Exception as e:
        logger.warning(f"Regime monitor: {e}")

    # Push strategy health if enough data
    try:
        from src.strategy_health import compute_health
        from src.degradation_detector import detect_degradation
        from src.telegram_notifier import send_strategy_health, send_validation_alert
        health = compute_health('live_paper')
        if 'error' not in health:
            send_strategy_health(health)
            deg = detect_degradation('live_paper')
            if deg['alerts']:
                send_validation_alert(deg['alerts'])
    except Exception as e:
        logger.warning(f"Telegram health: {e}")


def cmd_run_live(args):
    """Run live paper tracking only (no execution, just decisions + evaluation)."""
    from src.live_paper_tracker import log_daily_decisions
    from src.outcome_evaluator import evaluate_outcomes
    from src.decision_quality import score_all_pending

    logger.info("Running live paper tracker...")
    n = log_daily_decisions()
    print(f"\nLogged {n} decisions")

    evaluated = evaluate_outcomes()
    print(f"\nEvaluated {evaluated} outcomes")

    scored = score_all_pending()
    print(f"\nScored {scored} decisions")


def cmd_run_backtest(args):
    """Momentum sprint backtest with decision logging and full evaluation."""
    from src.decision_logger import init_decision_tables
    from src.momentum_backtester import run_momentum_backtest, format_momentum_report
    from src.historical_backtester import evaluate_historical_outcomes
    from src.decision_quality import score_all_pending

    init_decision_tables()

    if args.clean:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM decision_quality_scores WHERE decision_id IN "
                        "(SELECT decision_id FROM decisions WHERE mode='historical_backtest')")
            conn.execute("DELETE FROM decision_outcomes WHERE decision_id IN "
                        "(SELECT decision_id FROM decisions WHERE mode='historical_backtest')")
            conn.execute("DELETE FROM decisions WHERE mode='historical_backtest'")
            conn.execute("DELETE FROM strategy_health WHERE mode='historical_backtest'")
        print("Cleared previous historical data\n")

    result = run_momentum_backtest(args.start, args.end)
    print(format_momentum_report(result))

    print("\nEvaluating outcomes...")
    evaluated = evaluate_historical_outcomes()
    print(f"\n  {evaluated} outcomes evaluated")

    print("Scoring decisions...")
    scored = score_all_pending()
    print(f"\n  {scored} decisions scored")

    from src.validation_report import generate_validation_report
    print(generate_validation_report("historical_backtest"))


def cmd_evaluate_outcomes(args):
    """Evaluate pending outcomes for all modes."""
    from src.outcome_evaluator import evaluate_outcomes
    n = evaluate_outcomes()
    print(f"\nEvaluated {n} outcomes")


def cmd_compute_dqs(args):
    """Score all pending decision outcomes."""
    from src.decision_quality import score_all_pending
    n = score_all_pending()
    print(f"\nScored {n} decision quality scores")


def cmd_strategy_health(args):
    """Compute and display strategy health."""
    from src.strategy_health import compute_health
    from src.degradation_detector import detect_degradation

    mode = args.mode
    health = compute_health(mode)
    if "error" in health:
        print(f"\n⚠️ {health['error']}")
        return

    w = health["warning_level"]
    emoji = {"healthy": "🟢", "monitoring": "🟡", "unstable": "🟠", "broken": "🔴"}.get(w, "⚪")
    print(f"\n\n{emoji} Strategy Health ({mode}): DQS {health['overall_dqs']:.1f}/100 ({w.upper()})")
    print(f"\n\n  {'Component':<25s} {'Score':>6s} {'Weight':>7s}")
    print(f"\n  {'-'*42}")
    for label, key, weight in [
        ("Buy DQS", "buy_dqs", "25%"), ("Sell DQS", "sell_dqs", "20%"),
        ("Trim DQS", "trim_dqs", "10%"), ("Hold DQS", "hold_dqs", "15%"),
        ("NoBuy DQS", "no_buy_dqs", "15%"), ("Ranking Quality", "rating_quality_score", "10%"),
        ("Stability", "stability_score", "5%"),
    ]:
        v = health.get(key, 0) or 0
        print(f"\n  {label:<25s} {v:6.1f} {weight:>7s}")
    print(f"\n  {'─'*42}")
    print(f"\n  {'OVERALL':<25s} {health['overall_dqs']:6.1f}")

    print(f"\n\n  FP: {health['false_positive_rate']:.1f}% | FN: {health['false_negative_rate']:.1f}% | "
          f"Exit: {health['exit_quality_score']:.1f}% | Sample: {health['sample_size']}")

    deg = detect_degradation(mode)
    if deg["alerts"]:
        print(f"\n\n  ⚠️ Alerts ({len(deg['alerts'])}):")
        for a in deg["alerts"]:
            e = "🚨" if a["level"] == "critical" else "⚠️"
            print(f"\n    {e} {a['message']}")


def cmd_validation_report(args):
    """Generate full validation report."""
    from src.outcome_evaluator import evaluate_outcomes
    from src.decision_quality import score_all_pending

    evaluate_outcomes()
    score_all_pending()

    from src.validation_report import generate_validation_report
    report = generate_validation_report(args.mode)
    print(report)

    # Push health to Telegram
    try:
        from src.strategy_health import compute_health
        from src.degradation_detector import detect_degradation
        from src.telegram_notifier import send_strategy_health, send_validation_alert
        health = compute_health(args.mode)
        if 'error' not in health:
            send_strategy_health(health)
            deg = detect_degradation(args.mode)
            if deg['alerts']:
                send_validation_alert(deg['alerts'])
    except Exception as e:
        logger.warning(f'Telegram validation: {e}')


def cmd_missed_opportunities(args):
    """Show missed opportunities."""
    from src.strategy_health import compute_health
    mode = args.mode
    health = compute_health(mode)
    missed = health.get("missed_opportunities", [])
    if not missed:
        print("No missed opportunities detected.")
        return

    print(f"\n\n🎯 Missed Opportunities ({mode}, {len(missed)} total):\n")
    for m in missed:
        print(f"\n  {m['ticker']:6s} [{m.get('rating','')}] "
              f"max gain +{m.get('max_gain',0):.0f}%, fwd {m.get('forward_return',0):+.1f}%")


def cmd_worst_decisions(args):
    """Show worst decisions by DQS."""
    mode = args.mode
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT d.ticker, d.decision_type, d.decision_date, d.rating, d.reason,
                   q.quality_score, q.explanation,
                   o.forward_return, o.alpha_return, o.max_drawdown
            FROM decision_quality_scores q
            JOIN decisions d ON q.decision_id = d.decision_id
            JOIN decision_outcomes o ON o.decision_id = q.decision_id AND o.horizon_days = q.horizon_days
            WHERE d.mode = ? AND q.horizon_days = 20
            ORDER BY q.quality_score ASC LIMIT ?
        """, (mode, args.limit or 20)).fetchall()

    if not rows:
        print(f"\nNo scored decisions for {mode}")
        return

    print(f"\n\n💀 Worst Decisions ({mode}):\n")
    print(f"\n{'Date':12s} {'Type':8s} {'Ticker':6s} {'Rating':6s} {'DQS':>5s} "
          f"{'Return':>8s} {'Alpha':>8s} {'Reason'}")
    print("-" * 90)
    for r in rows:
        r = dict(r)
        print(f"\n{r['decision_date']:12s} {r['decision_type']:8s} {r['ticker']:6s} "
              f"{r.get('rating',''):6s} {r['quality_score']:5.0f} "
              f"{r.get('forward_return',0):+7.1f}% {r.get('alpha_return',0):+7.1f}% "
              f"{r.get('reason','')}")


def cmd_status(args):
    from src.portfolio import update_portfolio
    account = db.get_account()
    if not account:
        print("No account. Run 'init' first.")
        return
    status = update_portfolio()
    positions = db.get_all_positions()
    rows, live_pos_value, prev_pos_value = _position_status_rows(positions)
    positions_value = live_pos_value if positions else status["positions_value"]
    total_equity = status["cash"] + positions_value
    prev_total = status["cash"] + prev_pos_value
    day_pct = (total_equity - prev_total) / prev_total * 100 if prev_total > 0 else 0
    day_dollar = total_equity - prev_total
    print(f"\n\n💰 Cash: ${status['cash']:,.0f}")
    print(f"\n📊 Positions: ${positions_value:,.0f} ({status['num_positions']} open)")
    print(f"\n💎 Total: ${total_equity:,.0f}")
    if positions:
        print(f"\nDay: {day_pct:+.1f}% (${day_dollar:+,.0f})")
    if positions:
        print(f"\n\n{'Ticker':8s} {'Qty':>8s} {'Entry':>8s} {'Value':>10s} {'P&L':>8s} {'Day':>8s}")
        print("-" * 59)
        for row in rows:
            print(row)


def cmd_report(args):
    from src.performance import compute_stats
    stats = compute_stats()
    if "error" in stats:
        print(stats["error"]); return
    print(f"\n\n📈 Performance: ${stats['total_equity']:,.0f} ({stats['total_return_pct']:+.2f}%)")
    print(f"\n  Win Rate: {stats['win_rate']:.0f}% | PF: {stats['profit_factor']:.2f}")


def cmd_orders(args):
    orders = db.get_orders(days=30)
    if not orders:
        print("No orders."); return
    for o in orders[:30]:
        e = "🟢" if o["side"] == "BUY" else "🔴"
        print(f"\n{o['date']} {e}{o['side']:4s} {o['ticker']:6s} {o['quantity']:6.1f} "
              f"${o['price']:7.2f} {o.get('reason','')}")


def cmd_decisions(args):
    mode = args.mode
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT d.*, q.quality_score, o.forward_return, o.alpha_return
            FROM decisions d
            LEFT JOIN decision_quality_scores q ON d.decision_id = q.decision_id AND q.horizon_days = 20
            LEFT JOIN decision_outcomes o ON d.decision_id = o.decision_id AND o.horizon_days = 20
            WHERE d.mode = ? ORDER BY d.decision_date DESC LIMIT ?
        """, (mode, args.limit or 30)).fetchall()
    if not rows:
        print(f"\nNo decisions for {mode}"); return
    print(f"\n\n{'Date':12s} {'Type':8s} {'Ticker':6s} {'Rating':6s} {'DQS':>5s} {'Return':>8s} {'Reason'}")
    print("-" * 80)
    for r in rows:
        r = dict(r)
        dqs = f"{r['quality_score']:.0f}" if r.get("quality_score") is not None else "  —"
        fwd = f"{r['forward_return']:+.1f}%" if r.get("forward_return") is not None else "  —  "
        print(f"\n{r['decision_date']:12s} {r['decision_type']:8s} {r['ticker']:6s} "
              f"{r.get('rating',''):6s} {dqs:>5s} {fwd:>8s} {r.get('reason','')}")


def cmd_weekly_report(args):
    """Generate and send weekly performance report."""
    from src.weekly_report import generate_weekly_report
    report = generate_weekly_report()
    # Print plain text version
    import re
    print(re.sub(r"<[^>]+>", "", report))
    # Send to Telegram
    try:
        from src.telegram_notifier import _send
        _send(report)
        print("\n✅ Sent to Telegram")
    except Exception as e:
        logger.warning(f"Telegram weekly: {e}")


def cmd_regime_check(args):
    """Check AI trade regime — kill signal detection."""
    from src.regime_monitor import compute_regime_signals, format_regime_report
    result = compute_regime_signals()
    report = format_regime_report(result)
    # Strip HTML for CLI
    import re
    cli_report = re.sub(r"<[^>]+>", "", report)
    print(cli_report)

    # Push to Telegram
    try:
        from src.telegram_notifier import send_regime_alert
        send_regime_alert(report)
    except Exception as e:
        logger.warning(f"Telegram regime: {e}")


def cmd_position_audit(args):
    """Reconcile positions/account cash against paper_orders."""
    from src.position_audit import audit_positions, format_audit_report
    print(format_audit_report(audit_positions()))


def main():
    parser = argparse.ArgumentParser(prog="ASCR-H")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init")
    p.add_argument("--cash", type=float, default=None)

    sub.add_parser("run-daily", help="Full daily pipeline")
    sub.add_parser("run-live", help="Log live decisions + evaluate + score")

    p = sub.add_parser("run-backtest", help="Historical backtest")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--clean", action="store_true")

    sub.add_parser("evaluate-outcomes", help="Evaluate pending outcomes")
    sub.add_parser("compute-dqs", help="Score pending decision quality")

    p = sub.add_parser("strategy-health", help="Strategy health check")
    p.add_argument("--mode", choices=["live_paper", "historical_backtest"], default="historical_backtest")

    p = sub.add_parser("validation-report", help="Full validation report")
    p.add_argument("--mode", choices=["live_paper", "historical_backtest"], default="historical_backtest")

    p = sub.add_parser("missed-opportunities")
    p.add_argument("--mode", choices=["live_paper", "historical_backtest"], default="historical_backtest")

    p = sub.add_parser("worst-decisions")
    p.add_argument("--mode", choices=["live_paper", "historical_backtest"], default="historical_backtest")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("status")
    sub.add_parser("report")
    sub.add_parser("orders")

    sub.add_parser("weekly-report", help="Generate and send weekly report")
    sub.add_parser("regime-check", help="AI trade regime monitor — kill signal detection")
    sub.add_parser("position-audit", help="Reconcile positions against order ledger")

    p = sub.add_parser("decisions")
    p.add_argument("--mode", choices=["live_paper", "historical_backtest"], default="historical_backtest")
    p.add_argument("--limit", type=int, default=30)

    args = parser.parse_args()
    commands = {
        "init": cmd_init,
        "weekly-report": cmd_weekly_report,
        "regime-check": cmd_regime_check, "run-daily": cmd_run_daily, "run-live": cmd_run_live,
        "run-backtest": cmd_run_backtest, "evaluate-outcomes": cmd_evaluate_outcomes,
        "compute-dqs": cmd_compute_dqs, "strategy-health": cmd_strategy_health,
        "validation-report": cmd_validation_report,
        "missed-opportunities": cmd_missed_opportunities,
        "worst-decisions": cmd_worst_decisions,
        "status": cmd_status, "report": cmd_report, "orders": cmd_orders,
        "decisions": cmd_decisions,
        "position-audit": cmd_position_audit,
    }
    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
