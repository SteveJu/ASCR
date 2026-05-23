"""Backtest trading simulator.

Replays events chronologically, simulates radar ranking + paper trader execution.
Uses the same logic as live system but on historical data.
"""
import json
from datetime import datetime, timedelta
from collections import defaultdict
from src import db
from src.config import all_tickers


INITIAL_CAPITAL = 10000
MAX_POSITIONS = 10
POSITION_PCT = 0.10
HARD_STOP = -0.20
TRAILING_STOP = -0.25
MIN_EVENT_SCORE = 5


class Position:
    def __init__(self, ticker, entry_date, entry_price, shares, value):
        self.ticker = ticker
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.shares = shares
        self.value = value
        self.peak_price = entry_price


class Simulator:
    def __init__(self):
        self.cash = INITIAL_CAPITAL
        self.positions = {}  # ticker -> Position
        self.trade_log = []
        self.daily_snapshots = []
        self.conn = db.get_conn()

    def _get_price(self, ticker, date):
        row = self.conn.execute(
            "SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (ticker, date)).fetchone()
        return float(row["close"]) if row else None

    def _get_events_up_to(self, date) -> dict:
        """Get event scores per ticker up to a given date (30-day window)."""
        window_start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT ticker, evidence_delta, verdict, conviction "
            "FROM events WHERE date BETWEEN ? AND ? AND source = 'sec_8k_backtest'",
            (window_start, date)).fetchall()

        scores = defaultdict(lambda: {"ev_sum": 0, "count": 0, "verdict_sum": 0, "conv_sum": 0})
        verdict_map = {"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "AVOID": -1, "SELL": -2}
        conv_map = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}

        for r in rows:
            t = r["ticker"]
            scores[t]["ev_sum"] += r["evidence_delta"] or 0
            scores[t]["count"] += 1
            scores[t]["verdict_sum"] += verdict_map.get(r["verdict"], 0)
            scores[t]["conv_sum"] += conv_map.get(r["conviction"], 0.3)

        rankings = {}
        for t, s in scores.items():
            if s["count"] == 0:
                continue
            ev_score = (s["ev_sum"] / s["count"]) * min(s["count"], 8)
            verdict_score = (s["verdict_sum"] / s["count"]) * (s["conv_sum"] / s["count"])
            rankings[t] = {"ev_score": ev_score, "verdict_score": verdict_score}

        return rankings

    def _portfolio_value(self, date):
        total = self.cash
        for pos in self.positions.values():
            price = self._get_price(pos.ticker, date)
            if price:
                total += pos.shares * price
        return total

    def run(self):
        """Run full backtest simulation."""
        # Get all trading days
        days = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT date FROM prices WHERE ticker='SPY' ORDER BY date").fetchall()]

        if not days:
            print("ERROR: No price data. Run fetch_prices first.")
            return

        print(f"Simulating {len(days)} trading days: {days[0]} → {days[-1]}")
        print(f"Initial capital: ${INITIAL_CAPITAL:,.0f}")

        qqq_start = self._get_price("QQQ", days[0])
        spy_start = self._get_price("SPY", days[0])

        for i, date in enumerate(days):
            # 1. Check stop losses
            sells = []
            for ticker, pos in list(self.positions.items()):
                price = self._get_price(ticker, date)
                if not price:
                    continue

                # Update peak
                pos.peak_price = max(pos.peak_price, price)

                # Hard stop
                pnl_pct = (price - pos.entry_price) / pos.entry_price
                if pnl_pct <= HARD_STOP:
                    sells.append((ticker, "hard_stop", price))
                    continue

                # Trailing stop
                trail_pct = (price - pos.peak_price) / pos.peak_price
                if trail_pct <= TRAILING_STOP:
                    sells.append((ticker, "trailing_stop", price))

            # 2. Execute sells
            for ticker, reason, price in sells:
                pos = self.positions.pop(ticker)
                value = pos.shares * price
                self.cash += value
                pnl = value - pos.value
                self.trade_log.append({
                    "date": date, "ticker": ticker, "action": "SELL",
                    "price": price, "shares": pos.shares, "value": value,
                    "reason": reason, "pnl": pnl,
                    "entry_price": pos.entry_price, "entry_date": pos.entry_date,
                })

            # 3. Weekly rebalance (every 5 trading days)
            if i % 5 == 0:
                rankings = self._get_events_up_to(date)
                top_tickers = sorted(
                    [(t, s) for t, s in rankings.items() if s["ev_score"] >= MIN_EVENT_SCORE],
                    key=lambda x: (-x[1]["verdict_score"], -x[1]["ev_score"])
                )[:MAX_POSITIONS]
                top_set = {t for t, _ in top_tickers}

                # Smart rotation: only sell weakest held if a stronger candidate needs the slot
                available_slots = MAX_POSITIONS - len(self.positions)
                if available_slots <= 0 and top_tickers:
                    # Score each held position
                    ranking_map = {t: s for t, s in top_tickers}
                    held_scores = []
                    for t in self.positions:
                        score = ranking_map.get(t, {}).get("verdict_score", -999)
                        held_scores.append((t, score))
                    held_scores.sort(key=lambda x: x[1])  # weakest first

                    # Find candidates not held
                    candidates = [(t, s) for t, s in top_tickers if t not in self.positions]

                    for cand_ticker, cand_score_dict in candidates:
                        if not held_scores:
                            break
                        weakest_t, weakest_s = held_scores[0]
                        cand_s = cand_score_dict.get("verdict_score", 0)

                        # Only rotate if candidate is meaningfully better (>50% higher)
                        if cand_s > weakest_s * 1.5 and cand_s > 0:
                            pos = self.positions.pop(weakest_t)
                            price = self._get_price(weakest_t, date)
                            if price:
                                value = pos.shares * price
                                self.cash += value
                                self.trade_log.append({
                                    "date": date, "ticker": weakest_t, "action": "SELL",
                                    "price": price, "shares": pos.shares, "value": value,
                                    "reason": f"rotation_for_{cand_ticker}",
                                    "pnl": value - pos.value,
                                    "entry_price": pos.entry_price, "entry_date": pos.entry_date,
                                })
                            held_scores.pop(0)
                        else:
                            break

                # Buy top picks with available slots
                available_slots = MAX_POSITIONS - len(self.positions)
                portfolio_val = self._portfolio_value(date)
                per_position = portfolio_val * POSITION_PCT

                for ticker, score in top_tickers:
                    if available_slots <= 0 or self.cash < per_position * 0.5:
                        break
                    if ticker in self.positions:
                        continue

                    price = self._get_price(ticker, date)
                    if not price or price <= 0:
                        continue

                    buy_amount = min(per_position, self.cash)
                    shares = buy_amount / price
                    self.cash -= buy_amount
                    self.positions[ticker] = Position(ticker, date, price, shares, buy_amount)
                    available_slots -= 1

                    self.trade_log.append({
                        "date": date, "ticker": ticker, "action": "BUY",
                        "price": price, "shares": shares, "value": buy_amount,
                        "reason": f"ev={score['ev_score']:.1f} v={score['verdict_score']:.1f}",
                    })

            # 4. Daily snapshot
            port_val = self._portfolio_value(date)
            qqq_now = self._get_price("QQQ", date)
            spy_now = self._get_price("SPY", date)

            self.daily_snapshots.append({
                "date": date,
                "portfolio_value": port_val,
                "cash": self.cash,
                "positions": len(self.positions),
                "cumulative_return": (port_val / INITIAL_CAPITAL - 1) * 100,
                "qqq_return": ((qqq_now / qqq_start - 1) * 100) if qqq_start and qqq_now else 0,
                "spy_return": ((spy_now / spy_start - 1) * 100) if spy_start and spy_now else 0,
            })

        self._save_results()
        self._print_summary()

    def _save_results(self):
        """Persist results to DB."""
        conn = db.get_conn()
        for t in self.trade_log:
            conn.execute(
                "INSERT INTO backtest_trades (date, ticker, action, price, shares, value, reason, portfolio_value, cash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t["date"], t["ticker"], t["action"], t["price"], t["shares"], t["value"],
                 t.get("reason", ""), t.get("portfolio_value", 0), t.get("cash", 0)))

        for s in self.daily_snapshots:
            conn.execute(
                "INSERT OR REPLACE INTO backtest_daily "
                "(date, portfolio_value, cash, positions_count, cumulative_return, qqq_cumulative, spy_cumulative) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (s["date"], s["portfolio_value"], s["cash"], s["positions"],
                 s["cumulative_return"], s["qqq_return"], s["spy_return"]))

        conn.commit()
        conn.close()

    def _print_summary(self):
        """Print backtest summary."""
        if not self.daily_snapshots:
            print("No data")
            return

        first = self.daily_snapshots[0]
        last = self.daily_snapshots[-1]

        # Calculate metrics
        total_return = last["cumulative_return"]
        qqq_return = last["qqq_return"]
        spy_return = last["spy_return"]
        alpha_qqq = total_return - qqq_return
        alpha_spy = total_return - spy_return

        # Max drawdown
        peak = INITIAL_CAPITAL
        max_dd = 0
        for s in self.daily_snapshots:
            peak = max(peak, s["portfolio_value"])
            dd = (s["portfolio_value"] - peak) / peak * 100
            max_dd = min(max_dd, dd)

        # Win rate
        completed = [t for t in self.trade_log if t["action"] == "SELL"]
        wins = sum(1 for t in completed if t.get("pnl", 0) > 0)
        win_rate = wins / len(completed) * 100 if completed else 0

        # Total trades
        buys = sum(1 for t in self.trade_log if t["action"] == "BUY")
        sells = len(completed)

        total_pnl = sum(t.get("pnl", 0) for t in completed)
        avg_pnl = total_pnl / len(completed) if completed else 0

        # Sharpe (simplified annualized)
        import statistics
        daily_returns = []
        for i in range(1, len(self.daily_snapshots)):
            prev = self.daily_snapshots[i-1]["portfolio_value"]
            curr = self.daily_snapshots[i]["portfolio_value"]
            daily_returns.append((curr - prev) / prev)

        if daily_returns:
            avg_daily = statistics.mean(daily_returns)
            std_daily = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0.01
            sharpe = (avg_daily / std_daily) * (252 ** 0.5) if std_daily > 0 else 0
        else:
            sharpe = 0

        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        print(f"Period:           {first['date']} → {last['date']}")
        print(f"Trading days:     {len(self.daily_snapshots)}")
        print(f"")
        print(f"📊 Returns:")
        print(f"  Portfolio:      {total_return:+.1f}%  (${INITIAL_CAPITAL:,} → ${last['portfolio_value']:,.0f})")
        print(f"  QQQ:            {qqq_return:+.1f}%")
        print(f"  SPY:            {spy_return:+.1f}%")
        print(f"  Alpha vs QQQ:   {alpha_qqq:+.1f}%")
        print(f"  Alpha vs SPY:   {alpha_spy:+.1f}%")
        print(f"")
        print(f"📉 Risk:")
        print(f"  Max Drawdown:   {max_dd:.1f}%")
        print(f"  Sharpe Ratio:   {sharpe:.2f}")
        print(f"")
        print(f"📈 Trades:")
        print(f"  Total buys:     {buys}")
        print(f"  Total sells:    {sells}")
        print(f"  Win rate:       {win_rate:.0f}%")
        print(f"  Avg P&L/trade:  ${avg_pnl:,.0f}")
        print(f"  Total realized: ${total_pnl:,.0f}")
        print(f"")
        print(f"💼 Current:")
        print(f"  Positions:      {len(self.positions)}")
        print(f"  Cash:           ${self.cash:,.0f}")
        for t, p in sorted(self.positions.items()):
            curr_price = self._get_price(t, last["date"])
            pnl_pct = ((curr_price - p.entry_price) / p.entry_price * 100) if curr_price else 0
            print(f"    {t}: ${p.entry_price:.2f} → ${curr_price:.2f} ({pnl_pct:+.1f}%)")
        print("=" * 60)


if __name__ == "__main__":
    sim = Simulator()
    sim.run()
