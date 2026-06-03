"""ASCR public CLI entry point."""
import sys
import os
import argparse
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db, config
from src.price_fetcher import fetch_prices, get_ticker_info
from src.sec_fetcher import fetch_all as fetch_sec
from src.news_fetcher import fetch_news
from src.event_deduper import deduplicate_events, filter_actionable
from src.event_cooldown import should_apply_event, set_cooldown
from src.scoring import compute_scores
from src.score_delta import compute_deltas, get_inflections, should_upgrade_tracking
from src.market_regime import detect_regime
from src.shadow_tracker import record_b_tier_shadows, update_shadow_returns, get_shadow_report
from src.position_tracker import get_position_status
from src.exit_rules import evaluate_exits, generate_trade_card
from src.report_generator import generate_daily_report
from src.telegram_notifier import send_daily_summary, send_alert
from src.llm_high_value_router import get_router as get_llm_router
from src.ranking_quality import generate_ranking_report
from src.utils import get_logger

logger = get_logger("ASCR")


def cmd_init_db(args):
    db.init_db()
    print("Database initialized at:", config.db_path())


def cmd_run_daily(args):
    """Run daily pipeline using Analysis + Discovery engines."""

    # Weekend guard
    if datetime.now().weekday() >= 5:
        logger.info("Weekend — skipping daily run")
        return

    morning = getattr(args, 'morning', False) or datetime.now().hour < 12

    logger.info("=" * 60)
    logger.info(f"ASCR — {'AM' if morning else 'PM'} Run")
    logger.info("=" * 60)

    # === Analysis Engine (analyze existing universe) ===
    from src.analysis_engine import run_analysis
    result = run_analysis(morning=morning)
    logger.info(f"Analysis done: {result}")

    # === Discovery Engine (find new stocks) — only PM ===
    if not morning:
        try:
            from src.discovery_engine import run_discovery
            discovery = run_discovery()
            logger.info(f"Discovery done: {discovery}")
        except Exception as e:
            logger.warning(f"Discovery engine: {e}")

    logger.info("Daily run complete")


def cmd_score(args):
    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = config.all_tickers()
    benchmarks = config.all_tickers(include_benchmarks=True)
    fetch_prices(tickers, period="3mo")
    results = compute_scores(tickers)
    print(f"\n{'Rating':6s} {'Priority':8s} | {'Ticker':6s} | {'Opp':>5s} | {'E':>3s} {'A':>3s} {'M':>3s} {'R':>3s} | Reason")
    print("-" * 90)
    for r in results:
        print(f"{r['rating']:6s} [{r['tracking_priority']:6s}] | {r['ticker']:6s} | {r['opportunity']:5.1f} | {r['evidence']:3.0f} {r['asymmetry']:3.0f} {r['momentum']:3.0f} {r['risk']:3.0f} | {r['reason']}")


def cmd_report(args):
    date = args.date if args.date != "today" else datetime.now().strftime("%Y-%m-%d")
    filepath = os.path.join(config.REPORTS_DIR, "daily", f"{date}.md")
    if os.path.exists(filepath):
        with open(filepath) as f:
            print(f.read())
    else:
        print(f"No report for {date}. Run 'run-daily' first.")


def cmd_add_position(args):
    ticker = args.ticker.upper()
    shares = args.shares if args.shares else args.size / args.entry_price
    pos_id = db.add_position(
        ticker, args.entry_price, shares, args.date or datetime.now().strftime("%Y-%m-%d"),
        thesis=args.thesis or "", expected_holding=args.horizon or "1-3 months",
        max_loss_pct=args.max_loss, trailing_stop_pct=args.trailing, notes=args.notes or ""
    )
    pos = db.get_position(pos_id)
    prices = db.get_prices(ticker, days=5)
    current = prices[0]["close"] if prices else None
    card = generate_trade_card(pos, current)
    print(card)
    send_alert("NEW POSITION", ticker, card)


def cmd_update_position(args):
    ticker = args.ticker.upper()
    positions = get_position_status()
    pos_list = [p for p in positions if p["ticker"] == ticker]
    if not pos_list:
        print(f"No open position for {ticker}")
        return
    for pos in pos_list:
        print(f"\n${pos['ticker']} — {pos.get('position_status', 'Hold Strong')}")
        print(f"  Entry: ${pos['entry_price']:.2f} | Current: ${pos['current_price']:.2f} | P&L: {pos['pnl_pct']:+.1f}%")
        print(f"  Peak: ${pos['max_price_since_entry']:.2f} | Drawdown: {pos['drawdown_from_high']:.1f}%")
    exits = evaluate_exits(pos_list)
    if exits:
        for sig in exits:
            print(f"\n⚠️ Exit Signals:")
            for s in sig["signals"]:
                print(f"  [{s['severity']}] {s['type']}: {s['message']}")
                print(f"    → {s['action']}")
    else:
        print("\n✅ No exit signals.")


def cmd_list_alerts(args):
    alerts = db.get_recent_exit_alerts(days=args.days or 7)
    if not alerts:
        print("No recent alerts.")
        return
    for a in alerts:
        print(f"[{a['severity']:8s}] {a['date']} ${a['ticker']}: {a['alert_type']}")
        print(f"  {a['reason']}")
        print(f"  → {a['action_suggestion']}\n")


def cmd_explain(args):
    ticker = args.ticker.upper()
    scores = db.get_score_history(ticker, days=5)
    if not scores:
        print(f"No scores for {ticker}. Run scoring first.")
        return
    latest = scores[0]
    print(f"\n📊 ${ticker} — Rating: {latest['rating']} | Opp: {latest['opportunity_score']:.1f}")
    print(f"  Evidence:  {latest['evidence_score']:.0f}/100")
    print(f"  Asymmetry: {latest['asymmetry_score']:.0f}/100")
    print(f"  PriceConf: {latest['momentum_score']:.0f}/100")
    print(f"  Risk:      {latest['risk_score']:.0f}/100")
    print(f"  Priority:  {latest.get('tracking_priority', 'N/A')}")
    print(f"  Reason:    {latest.get('reason', 'N/A')}")
    print(f"  Next:      {latest.get('next_trigger', 'N/A')}")
    if latest.get("details_json"):
        details = json.loads(latest["details_json"])
        print("\n  --- Details ---")
        for dim in ["evidence", "asymmetry", "momentum", "risk", "valuation", "business_quality"]:
            d = details.get(dim, {})
            if d:
                print(f"  {dim.replace('_', ' ').title()}:")
                for k, v in d.items():
                    if k != "note":
                        print(f"    {k}: {v}")


def cmd_backtest(args):
    from src.backtest_exit_rules import run_all_backtests, format_backtest_report
    results = run_all_backtests()
    print(format_backtest_report(results))


def cmd_feedback(args):
    from src.performance_feedback_loop import generate_feedback_report
    print(generate_feedback_report())


def cmd_regime(args):
    regime = detect_regime()
    print(f"\n📊 Market Regime: {regime['regime'].upper()}")
    print(f"  Reason: {regime['reason']}")
    d = regime.get("details", {})
    for idx in ("spy", "qqq"):
        if idx in d:
            s = d[idx]
            print(f"  {idx.upper()}: ${s['price']:.2f} | SMA50: ${s['sma50']:.2f} ({'✅' if s['above_50'] else '❌'}) | SMA200: ${s['sma200']:.2f} ({'✅' if s['above_200'] else '❌'})")


def cmd_shadows(args):
    report = get_shadow_report()
    if "message" in report:
        print(report["message"])
        return
    print(f"\n👻 Shadow Tracking Summary")
    print(f"  Tracked: {report['total_tracked']}")
    print(f"  Avg 20d return: {report['avg_return_20d']:+.1f}%")
    print(f"  Positive 20d: {report['positive_20d_pct']:.0f}%")
    if report.get("avg_return_60d"):
        print(f"  Avg 60d return: {report['avg_return_60d']:+.1f}%")
    if report.get("upgrades"):
        print(f"  Upgrades B→A/S: {report['upgrades']} ({', '.join(report['upgrade_tickers'])})")
    if report.get("big_winners"):
        print(f"\n  🏆 Big winners (>30% in 60d):")
        for w in report["big_winners"]:
            print(f"    {w['ticker']}: +{w['return_60d']:.0f}%")


def cmd_backtest_historical(args):
    from src.historical_backtest import init_backtest_db, download_historical_prices, run_backtest, run_all_periods, format_backtest_report
    init_backtest_db()
    if args.period == "all":
        results = run_all_periods()
        for period, summary in results.items():
            print(format_backtest_report(summary, period))
            print("\n" + "=" * 60 + "\n")
    elif args.period == "2024H2":
        download_historical_prices(config.all_tickers(), start="2024-01-01")
        s = run_backtest("2024H2_to_2025H1", "2024-07-01", "2024-12-31")
        print(format_backtest_report(s, "2024H2_to_2025H1"))
    elif args.period == "2025H2":
        download_historical_prices(config.all_tickers(), start="2024-01-01")
        s = run_backtest("2025H2_to_2026H1", "2025-07-01", "2025-12-31")
        print(format_backtest_report(s, "2025H2_to_2026H1"))


def cmd_ranking(args):
    from src.ranking_quality import generate_ranking_report
    lookback = args.lookback if hasattr(args, 'lookback') else 90
    report = generate_ranking_report(lookback_days=lookback)
    print(report)


def cmd_frontier(args):
    from src.frontier_radar import build_payload, render_report
    payload = build_payload(limit=args.limit)
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_report(payload, limit=args.limit))


def main():
    parser = argparse.ArgumentParser(prog="ASCR", description="AI Supply Chain Stock Discovery System")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init-db", help="Initialize database")
    sub.add_parser("run-daily", help="Run full daily pipeline")

    p = sub.add_parser("score", help="Score ticker(s)")
    p.add_argument("--ticker", "-t")

    p = sub.add_parser("report", help="View daily report")
    p.add_argument("--date", "-d", default="today")

    p = sub.add_parser("add-position", help="Add a position")
    p.add_argument("--ticker", "-t", required=True)
    p.add_argument("--entry-price", type=float, required=True)
    p.add_argument("--size", type=float, default=0)
    p.add_argument("--shares", type=float, default=0)
    p.add_argument("--date", default=None)
    p.add_argument("--thesis", default="")
    p.add_argument("--horizon", default="1-3 months")
    p.add_argument("--max-loss", type=float, default=25)
    p.add_argument("--trailing", type=float, default=15)
    p.add_argument("--notes", default="")

    p = sub.add_parser("update-position", help="Check position status")
    p.add_argument("--ticker", "-t", required=True)

    p = sub.add_parser("list-alerts", help="List recent exit alerts")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("explain", help="Explain scoring for a ticker")
    p.add_argument("--ticker", "-t", required=True)

    sub.add_parser("backtest", help="Backtest exit rules")
    sub.add_parser("feedback", help="Performance feedback report")
    sub.add_parser("regime", help="Show market regime")
    sub.add_parser("shadows", help="Shadow tracking report")

    p = sub.add_parser("backtest-historical", help="Historical backtest (2024H2/2025H2)")
    p.add_argument("--period", choices=["all", "2024H2", "2025H2"], default="all")

    p = sub.add_parser("ranking", help="Ranking quality report")
    p.add_argument("--lookback", type=int, default=90)

    p = sub.add_parser("frontier", help="Frontier radar report")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    commands = {
        "init-db": cmd_init_db, "run-daily": cmd_run_daily, "score": cmd_score,
        "report": cmd_report, "add-position": cmd_add_position,
        "update-position": cmd_update_position, "list-alerts": cmd_list_alerts,
        "explain": cmd_explain, "backtest": cmd_backtest, "feedback": cmd_feedback,
        "regime": cmd_regime, "shadows": cmd_shadows, "backtest-historical": cmd_backtest_historical,
        "ranking": cmd_ranking, "frontier": cmd_frontier,
    }
    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
