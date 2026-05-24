"""Strategy comparison backtester — momentum vs event-driven vs hybrid.

Uses bt_scores + bt_prices from ASCR backtest.sqlite.
Tests 3 strategies on 2025-H2 data:
  1. Pure Momentum: top-5 by momentum (current strategy)
  2. Event-Driven: top-5 by evidence score (what user wants)
  3. Hybrid: event signal priority + momentum tiebreak
"""
import os
import sqlite3
from datetime import datetime
from collections import defaultdict
from src.utils import get_logger

logger = get_logger("strategy_bt")

BT_DB = os.environ.get("ASCR_BACKTEST_DB_PATH", os.path.join(os.environ.get("ASCR_PROJECT_DIR", "../ASCR"), "data", "backtest.sqlite"))


def _get_data(start="2025-07-01", end="2025-12-31"):
    """Load scores + prices for backtest period."""
    conn = sqlite3.connect(BT_DB)
    conn.row_factory = sqlite3.Row

    scores = conn.execute("""
        SELECT eval_date, ticker, evidence, asymmetry, momentum, risk,
               opportunity, rating
        FROM bt_scores WHERE eval_date >= ? AND eval_date <= ?
        ORDER BY eval_date, momentum DESC
    """, (start, end)).fetchall()

    prices = conn.execute("""
        SELECT date, ticker, close FROM bt_prices
        WHERE date >= ? AND date <= ?
        ORDER BY date
    """, (start, end)).fetchall()

    conn.close()

    # Build price lookup
    price_map = {}  # (date, ticker) -> close
    price_dates = sorted(set(r["date"] for r in prices))
    for r in prices:
        price_map[(r["date"], r["ticker"])] = r["close"]

    # Build score lookup by date
    score_dates = sorted(set(r["eval_date"] for r in scores))
    score_map = {}  # date -> [scores sorted by some criteria]
    for r in scores:
        d = r["eval_date"]
        if d not in score_map:
            score_map[d] = []
        score_map[d].append(dict(r))

    return score_map, price_map, score_dates, price_dates


def _get_price(price_map, price_dates, ticker, target_date):
    """Get price on or before target_date."""
    # Find closest date <= target_date
    for d in reversed(price_dates):
        if d <= target_date:
            p = price_map.get((d, ticker))
            if p:
                return p, d
    return None, None


def _run_strategy(name, score_map, price_map, score_dates, price_dates,
                  rank_fn, max_pos=5, initial=10000,
                  hard_stop=-0.20, trail_act=0.20, trail_drop=-0.25):
    """Run a generic strategy with given ranking function."""
    cash = initial
    positions = {}  # ticker -> {shares, entry_price, peak_price, entry_date}
    trades = []
    equity_curve = []

    for i, score_date in enumerate(score_dates):
        day_scores = score_map.get(score_date, [])
        if not day_scores:
            continue

        # Get prices for this date
        def get_px(ticker):
            p, _ = _get_price(price_map, price_dates, ticker, score_date)
            return p or 0

        # === SELL PASS ===
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            price = get_px(ticker)
            if not price:
                continue

            pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
            pos["peak_price"] = max(pos["peak_price"], price)
            drop_from_peak = (price - pos["peak_price"]) / pos["peak_price"]

            sell = False
            reason = ""
            if pnl_pct <= hard_stop:
                sell = True
                reason = f"hard_stop_{pnl_pct:.0%}"
            elif pnl_pct > trail_act and drop_from_peak <= trail_drop:
                sell = True
                reason = f"trailing_stop_{drop_from_peak:.0%}"

            if sell:
                sell_val = pos["shares"] * price
                cash += sell_val
                realized = (price - pos["entry_price"]) * pos["shares"]
                trades.append({
                    "date": score_date, "ticker": ticker, "side": "SELL",
                    "price": price, "pnl_pct": pnl_pct, "reason": reason,
                    "hold_days": (datetime.strptime(score_date, "%Y-%m-%d") -
                                 datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days,
                })
                del positions[ticker]

        # === BUY PASS ===
        slots = max_pos - len(positions)
        if slots > 0:
            ranked = rank_fn(day_scores)
            pos_val = sum(pos["shares"] * get_px(t) for t, pos in positions.items())
            total_eq = cash + pos_val
            target = total_eq * (1.0 / max_pos)

            for s in ranked:
                if slots <= 0:
                    break
                ticker = s["ticker"]
                if ticker in positions:
                    continue

                price = get_px(ticker)
                if not price:
                    continue

                amount = min(target, cash * 0.95)
                if amount < 100:
                    break

                shares = amount / price
                cash -= amount
                positions[ticker] = {
                    "shares": shares, "entry_price": price,
                    "peak_price": price, "entry_date": score_date,
                }
                trades.append({
                    "date": score_date, "ticker": ticker, "side": "BUY",
                    "price": price, "momentum": s.get("momentum", 0),
                    "evidence": s.get("evidence", 0),
                })
                slots -= 1

        # Record equity
        pos_val = sum(pos["shares"] * get_px(t) for t, pos in positions.items())
        total_eq = cash + pos_val
        equity_curve.append({"date": score_date, "equity": total_eq})

    # Final mark-to-market
    final_eq = equity_curve[-1]["equity"] if equity_curve else initial
    ret = (final_eq / initial - 1) * 100

    # Open positions at end
    open_pos = {}
    for t, pos in positions.items():
        px = _get_price(price_map, price_dates, t, score_dates[-1])[0] or pos["entry_price"]
        pnl = (px - pos["entry_price"]) / pos["entry_price"] * 100
        open_pos[t] = {"entry": pos["entry_price"], "final": px, "pnl_pct": pnl}

    return {
        "name": name, "return_pct": ret, "final_equity": final_eq,
        "num_trades": len(trades), "trades": trades,
        "equity_curve": equity_curve, "open_positions": open_pos,
        "buys": len([t for t in trades if t["side"] == "BUY"]),
        "sells": len([t for t in trades if t["side"] == "SELL"]),
    }


def run_comparison(start="2025-07-01", end="2025-12-31"):
    """Run all 3 strategies and compare."""
    score_map, price_map, score_dates, price_dates = _get_data(start, end)
    logger.info(f"Backtest {start} to {end}: {len(score_dates)} score dates, "
                f"{len(price_dates)} price dates")

    # Strategy 1: Pure Momentum (current)
    def rank_momentum(scores):
        return sorted(scores, key=lambda s: s["momentum"], reverse=True)

    # Strategy 2: Pure Evidence (event-driven)
    def rank_evidence(scores):
        return sorted(scores, key=lambda s: s["evidence"], reverse=True)

    # Strategy 3: Hybrid — evidence >= 50 first (sorted by evidence), then momentum fill
    def rank_hybrid(scores):
        high_ev = sorted([s for s in scores if s["evidence"] >= 50],
                        key=lambda s: s["evidence"], reverse=True)
        # Fill remaining from momentum, excluding already-selected
        high_ev_tickers = {s["ticker"] for s in high_ev}
        momentum_fill = sorted([s for s in scores if s["ticker"] not in high_ev_tickers],
                              key=lambda s: s["momentum"], reverse=True)
        return high_ev + momentum_fill

    # Strategy 4: Evidence-weighted (evidence * momentum combined)
    def rank_ev_momentum(scores):
        # Evidence is the primary signal, momentum as confirmation
        for s in scores:
            s["_combo"] = s["evidence"] * 0.6 + s["momentum"] * 0.4
        return sorted(scores, key=lambda s: s["_combo"], reverse=True)

    # Strategy 5: Evidence-first, momentum tiebreak (>= 30 evidence threshold)
    def rank_ev_first_30(scores):
        high_ev = sorted([s for s in scores if s["evidence"] >= 30],
                        key=lambda s: (s["evidence"], s["momentum"]), reverse=True)
        high_ev_tickers = {s["ticker"] for s in high_ev}
        momentum_fill = sorted([s for s in scores if s["ticker"] not in high_ev_tickers],
                              key=lambda s: s["momentum"], reverse=True)
        return high_ev + momentum_fill

    results = []
    for name, fn in [
        ("1. Pure Momentum", rank_momentum),
        ("2. Pure Evidence", rank_evidence),
        ("3. Hybrid (ev≥50 first)", rank_hybrid),
        ("4. Evidence×0.6+Mom×0.4", rank_ev_momentum),
        ("5. Evidence≥30 first", rank_ev_first_30),
    ]:
        r = _run_strategy(name, score_map, price_map, score_dates, price_dates, fn)
        results.append(r)
        logger.info(f"  {name}: {r['return_pct']:+.1f}%, {r['num_trades']} trades")

    # QQQ benchmark
    qqq_start = _get_price(price_map, price_dates, "QQQ", score_dates[0])[0]
    qqq_end = _get_price(price_map, price_dates, "QQQ", score_dates[-1])[0]
    qqq_ret = (qqq_end / qqq_start - 1) * 100 if qqq_start and qqq_end else 0

    return results, qqq_ret


def print_comparison(results, qqq_ret):
    """Print formatted comparison."""
    print(f"\n{'='*70}")
    print(f"STRATEGY COMPARISON BACKTEST — 2025-H2")
    print(f"{'='*70}")
    print(f"\nQQQ Benchmark: {qqq_ret:+.1f}%\n")
    print(f"{'Strategy':<30s} {'Return':>8s} {'Alpha':>8s} {'Buys':>5s} {'Sells':>6s} {'Trades':>7s}")
    print("-" * 70)

    for r in sorted(results, key=lambda x: x["return_pct"], reverse=True):
        alpha = r["return_pct"] - qqq_ret
        print(f"{r['name']:<30s} {r['return_pct']:>+7.1f}% {alpha:>+7.1f}% "
              f"{r['buys']:>5d} {r['sells']:>6d} {r['num_trades']:>7d}")

    # Best strategy detail
    best = max(results, key=lambda x: x["return_pct"])
    print(f"\n{'='*70}")
    print(f"BEST: {best['name']} — {best['return_pct']:+.1f}%")
    print(f"{'='*70}")

    if best["open_positions"]:
        print("\nOpen positions at end:")
        for t, p in sorted(best["open_positions"].items(), key=lambda x: x[1]["pnl_pct"], reverse=True):
            print(f"  {t:6s}: entry ${p['entry']:.2f} → ${p['final']:.2f} ({p['pnl_pct']:+.1f}%)")

    buys = [t for t in best["trades"] if t["side"] == "BUY"]
    if buys:
        print("\nBuy history:")
        for b in buys:
            print(f"  {b['date']} BUY {b['ticker']:6s} @ ${b['price']:.2f} "
                  f"(ev={b.get('evidence',0):.0f}, mom={b.get('momentum',0):.0f})")

    sells = [t for t in best["trades"] if t["side"] == "SELL"]
    if sells:
        print("\nSell history:")
        for s in sells:
            print(f"  {s['date']} SELL {s['ticker']:6s} @ ${s['price']:.2f} "
                  f"({s['pnl_pct']:+.0%}) — {s['reason']} (held {s.get('hold_days',0)}d)")
