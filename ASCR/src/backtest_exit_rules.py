"""Backtest exit rules — evaluate effectiveness of each exit strategy.

Uses historical price data from DB. No broker connection.
Tests: profit-taking, stop-loss, trailing-stop, time-stop.
Compares: risk-adjusted return, missed upside, avoided drawdown.
"""
import json
from datetime import datetime, timedelta
from src import db
from src.utils import get_logger

logger = get_logger("backtest")


def _simulate_position(prices: list, entry_price: float, entry_idx: int,
                        exit_rule: dict) -> dict:
    """Simulate a single position with given exit rule.

    Args:
        prices: list of {date, close} sorted ascending by date
        entry_price: buy price
        entry_idx: index in prices where entry happens
        exit_rule: dict with rule parameters

    Returns:
        {exit_price, exit_date, exit_idx, pnl_pct, days_held, exit_reason,
         max_gain, max_drawdown, missed_upside_20d, avoided_drawdown_20d}
    """
    rule_type = exit_rule.get("type", "hold")
    max_price = entry_price
    exit_price = None
    exit_idx = None
    exit_reason = "held_to_end"

    for i in range(entry_idx + 1, len(prices)):
        p = prices[i]["close"]
        pnl_pct = (p - entry_price) / entry_price * 100
        max_price = max(max_price, p)
        drawdown_from_peak = (p - max_price) / max_price * 100

        days_held = i - entry_idx

        # Stop loss
        if rule_type in ("stop_loss", "all"):
            stop_pct = exit_rule.get("stop_loss_pct", -25)
            if pnl_pct <= stop_pct:
                exit_price = p
                exit_idx = i
                exit_reason = "stop_loss"
                break

        # Profit taking at +25%
        if rule_type in ("profit_25", "all"):
            if pnl_pct >= 25:
                exit_price = p
                exit_idx = i
                exit_reason = "profit_25"
                break

        # Trailing stop (activate after +30%, trigger at trailing %)
        if rule_type in ("trailing", "all"):
            trailing_pct = exit_rule.get("trailing_pct", 20)
            activation = exit_rule.get("trailing_activation", 30)
            if (max_price - entry_price) / entry_price * 100 >= activation:
                if abs(drawdown_from_peak) >= trailing_pct:
                    exit_price = p
                    exit_idx = i
                    exit_reason = "trailing_stop"
                    break

        # Time stop
        if rule_type in ("time_stop", "all"):
            time_days = exit_rule.get("time_days", 60)
            if days_held >= time_days and pnl_pct < 15:
                exit_price = p
                exit_idx = i
                exit_reason = "time_stop"
                break

    # If no exit triggered
    if exit_price is None:
        exit_idx = len(prices) - 1
        exit_price = prices[exit_idx]["close"]

    pnl_pct = (exit_price - entry_price) / entry_price * 100
    days_held = exit_idx - entry_idx

    # Calculate max gain and max drawdown during hold
    hold_prices = [prices[i]["close"] for i in range(entry_idx, exit_idx + 1)]
    max_gain = (max(hold_prices) - entry_price) / entry_price * 100 if hold_prices else 0
    max_drawdown = (min(hold_prices) - entry_price) / entry_price * 100 if hold_prices else 0

    # Post-exit analysis: what happened in next 20 days?
    missed_upside = 0
    avoided_drawdown = 0
    if exit_idx + 20 < len(prices):
        post_prices = [prices[i]["close"] for i in range(exit_idx, min(exit_idx + 21, len(prices)))]
        if post_prices:
            post_max = max(post_prices)
            post_min = min(post_prices)
            missed_upside = (post_max - exit_price) / exit_price * 100
            avoided_drawdown = (post_min - exit_price) / exit_price * 100

    return {
        "exit_price": exit_price,
        "exit_date": prices[exit_idx]["date"],
        "pnl_pct": pnl_pct,
        "days_held": days_held,
        "exit_reason": exit_reason,
        "max_gain": max_gain,
        "max_drawdown": max_drawdown,
        "missed_upside_20d": missed_upside,
        "avoided_drawdown_20d": avoided_drawdown,
    }


def backtest_rule(rule_name: str, rule_params: dict, tickers: list = None,
                   lookback_days: int = 252) -> dict:
    """Backtest a single exit rule across all tickers.

    Simulates buying at random entry points and applying the exit rule.
    """
    if tickers is None:
        from src import config
        tickers = config.all_tickers()

    all_results = []

    for ticker in tickers:
        prices_raw = db.get_prices(ticker, days=lookback_days)
        if len(prices_raw) < 60:
            continue

        prices = sorted(prices_raw, key=lambda x: x["date"])

        # Simulate entries every 20 trading days
        for entry_idx in range(0, len(prices) - 60, 20):
            entry_price = prices[entry_idx]["close"]
            if entry_price <= 0:
                continue

            result = _simulate_position(prices, entry_price, entry_idx, rule_params)
            result["ticker"] = ticker
            result["entry_price"] = entry_price
            result["entry_date"] = prices[entry_idx]["date"]
            all_results.append(result)

    if not all_results:
        return {"rule": rule_name, "error": "no data"}

    # Aggregate stats
    wins = [r for r in all_results if r["pnl_pct"] > 0]
    losses = [r for r in all_results if r["pnl_pct"] <= 0]

    total_return = sum(r["pnl_pct"] for r in all_results) / len(all_results)
    max_dd = min(r["max_drawdown"] for r in all_results)
    win_rate = len(wins) / len(all_results) * 100
    avg_win = sum(r["pnl_pct"] for r in wins) / max(len(wins), 1)
    avg_loss = sum(r["pnl_pct"] for r in losses) / max(len(losses), 1)
    gross_wins = sum(r["pnl_pct"] for r in wins)
    gross_losses = abs(sum(r["pnl_pct"] for r in losses))
    profit_factor = gross_wins / max(gross_losses, 0.01)
    avg_days = sum(r["days_held"] for r in all_results) / len(all_results)
    avg_missed = sum(r["missed_upside_20d"] for r in all_results) / len(all_results)
    avg_avoided = sum(r["avoided_drawdown_20d"] for r in all_results) / len(all_results)

    return {
        "rule": rule_name,
        "params": rule_params,
        "trades": len(all_results),
        "total_return_avg": round(total_return, 2),
        "max_drawdown": round(max_dd, 2),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_holding_days": round(avg_days, 1),
        "avg_missed_upside_20d": round(avg_missed, 2),
        "avg_avoided_drawdown_20d": round(avg_avoided, 2),
    }


def run_all_backtests(tickers: list = None) -> list:
    """Run backtests for all exit rule variants."""
    rules = [
        ("hold_forever", {"type": "hold"}),
        ("stop_loss_25", {"type": "stop_loss", "stop_loss_pct": -25}),
        ("stop_loss_15", {"type": "stop_loss", "stop_loss_pct": -15}),
        ("profit_25", {"type": "profit_25"}),
        ("trailing_20_after_30", {"type": "trailing", "trailing_pct": 20, "trailing_activation": 30}),
        ("trailing_15_after_30", {"type": "trailing", "trailing_pct": 15, "trailing_activation": 30}),
        ("time_stop_60d", {"type": "time_stop", "time_days": 60}),
        ("combined", {"type": "all", "stop_loss_pct": -25, "trailing_pct": 20, "trailing_activation": 30, "time_days": 60}),
    ]

    results = []
    for name, params in rules:
        logger.info(f"Backtesting: {name}")
        result = backtest_rule(name, params, tickers)
        results.append(result)

    return results


def format_backtest_report(results: list) -> str:
    """Format backtest results as markdown."""
    lines = ["# 📊 Exit Rules Backtest Report\n"]
    lines.append("| Rule | Trades | Avg Return | Max DD | Win Rate | Avg Win | Avg Loss | PF | Avg Days | Missed ↑ | Avoided ↓ |")
    lines.append("|------|--------|-----------|--------|----------|---------|----------|-----|----------|----------|-----------|")

    for r in results:
        if "error" in r:
            continue
        lines.append(
            f"| {r['rule']} | {r['trades']} | {r['total_return_avg']:+.1f}% | {r['max_drawdown']:.1f}% | "
            f"{r['win_rate']:.0f}% | {r['avg_win']:+.1f}% | {r['avg_loss']:+.1f}% | {r['profit_factor']:.2f} | "
            f"{r['avg_holding_days']:.0f}d | {r['avg_missed_upside_20d']:+.1f}% | {r['avg_avoided_drawdown_20d']:+.1f}% |"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    results = run_all_backtests()
    print(format_backtest_report(results))
