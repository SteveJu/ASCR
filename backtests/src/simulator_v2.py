"""Backtest V2 — sector-aware simulation with dynamic universe discovery.

Key differences from V1:
1. Universe starts EMPTY — tickers are "discovered" as events occur
2. Sector-aware stop losses (sentiment tighter, infrastructure looser)
3. Sell cooldown (3 days — can't rebuy immediately)
4. Daily trade limit (max 4)
5. Death signal detection from filing text
6. Smart rotation with 1.5x threshold
7. Sector concentration limit (max 3 per sector)
"""
import json
import statistics
from datetime import datetime, timedelta
from collections import defaultdict
from src import db
from src.config import all_tickers


INITIAL_CAPITAL = 10000
MAX_POSITIONS = 10
POSITION_PCT = 0.10
MIN_EVENT_SCORE = 5

# Sector-aware stop losses
SECTOR_STOPS = {
    "memory_storage": (-0.20, -0.25),
    "optical":        (-0.20, -0.25),
    "compute":        (-0.18, -0.22),
    "power_cooling":  (-0.22, -0.28),
    "energy_grid":    (-0.22, -0.28),
    "semicap":        (-0.20, -0.25),
    "networking":     (-0.20, -0.25),
    "data_center":    (-0.18, -0.22),
    "eda_ip":         (-0.18, -0.22),
    "new_additions":  (-0.15, -0.20),  # Sentiment — tighter
}

# Ticker → sector mapping (matches live system)
TICKER_SECTOR = {
    "MU": "memory_storage", "SNDK": "memory_storage", "STX": "memory_storage", "WDC": "memory_storage",
    "CIEN": "optical", "COHR": "optical", "GLW": "optical", "LITE": "optical",
    "ANET": "networking", "AVGO": "networking", "CSCO": "networking", "MRVL": "networking",
    "CEG": "energy_grid", "ETN": "power_cooling", "NEE": "energy_grid", "VRT": "power_cooling", "VST": "energy_grid",
    "DLR": "data_center", "EQIX": "data_center", "IREN": "new_additions", "NBIS": "new_additions",
    "AMAT": "semicap", "ASML": "semicap", "KLAC": "semicap", "LRCX": "semicap", "TER": "semicap",
    "ARM": "eda_ip", "CDNS": "eda_ip", "SNPS": "eda_ip",
    "AAOI": "optical", "CRWV": "new_additions", "MOD": "power_cooling", "PWR": "power_cooling",
    "NVDA": "compute", "AMD": "compute", "INTC": "compute", "TSM": "semicap",
    "SMCI": "compute", "CRDO": "networking", "MXL": "networking",
    "ACMR": "semicap", "UCTT": "semicap", "VICR": "power_cooling", "ON": "compute",
    "APLD": "new_additions", "KEEL": "new_additions", "SAP": "eda_ip",
    "DELL": "compute", "QCOM": "compute",
}

MAX_PER_SECTOR = 3
SELL_COOLDOWN_DAYS = 3
MAX_TRADES_PER_DAY = 4

# Death signal keywords by sector
DEATH_SIGNALS = {
    "memory_storage": ["oversupply", "price decline", "capacity expansion complete", "inventory build"],
    "optical": ["contract completed", "new supplier", "order pushout", "cancellation"],
    "semicap": ["capex cut", "order decline", "utilization drop"],
    "power_cooling": ["efficiency breakthrough", "cooling commoditization"],
    "new_additions": ["short report", "SEC investigation", "dilution", "guidance cut"],
}


class Position:
    def __init__(self, ticker, entry_date, entry_price, shares, value, sector):
        self.ticker = ticker
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.shares = shares
        self.value = value
        self.peak_price = entry_price
        self.sector = sector


class SimulatorV2:
    def __init__(self):
        self.cash = INITIAL_CAPITAL
        self.positions = {}
        self.trade_log = []
        self.daily_snapshots = []
        self.conn = db.get_conn()
        self.rebalance_interval = 5  # default weekly, set to 1 for daily
        self.discovered_universe = set()  # starts empty!
        self.sell_dates = {}  # ticker -> last sell date (for cooldown)
        self.daily_trade_count = defaultdict(int)  # date -> count

    def _get_price(self, ticker, date):
        row = self.conn.execute(
            "SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (ticker, date)).fetchone()
        return float(row["close"]) if row else None

    def _get_sector(self, ticker):
        return TICKER_SECTOR.get(ticker, "new_additions")

    def _get_stops(self, ticker):
        sector = self._get_sector(ticker)
        return SECTOR_STOPS.get(sector, (-0.20, -0.25))

    def _sector_count(self, sector):
        """How many positions in this sector."""
        return sum(1 for p in self.positions.values() if p.sector == sector)

    def _check_death_signal(self, ticker, headline):
        """Check if a headline contains death signals for this ticker's sector."""
        sector = self._get_sector(ticker)
        keywords = DEATH_SIGNALS.get(sector, [])
        hl = (headline or "").lower()
        return any(kw in hl for kw in keywords)

    def _get_events_up_to(self, date) -> dict:
        """Get event scores per ticker up to date (30-day window).
        Also "discovers" tickers as they appear in events.
        """
        window_start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT ticker, evidence_delta, verdict, conviction, headline, source "
            "FROM events WHERE date BETWEEN ? AND ?",
            (window_start, date)).fetchall()

        scores = defaultdict(lambda: {"ev_sum": 0, "count": 0, "verdict_sum": 0,
                                       "conv_sum": 0, "death": False})
        verdict_map = {"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "AVOID": -1, "SELL": -2}
        conv_map = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}

        for r in rows:
            t = r["ticker"]
            # Dynamic universe discovery — ticker appears in events = discovered
            self.discovered_universe.add(t)

            # Insider events get 0.5x weight (ambiguous signal)
            weight = 0.5 if r["source"] == "insider_backtest" else 1.0
            scores[t]["ev_sum"] += (r["evidence_delta"] or 0) * weight
            scores[t]["count"] += weight
            scores[t]["verdict_sum"] += verdict_map.get(r["verdict"], 0) * weight
            scores[t]["conv_sum"] += conv_map.get(r["conviction"], 0.3) * weight

            # Check for death signals
            if self._check_death_signal(t, r["headline"]):
                scores[t]["death"] = True

        rankings = {}
        for t, s in scores.items():
            if s["count"] == 0 or s["death"]:
                continue
            # Only rank tickers in discovered universe
            if t not in self.discovered_universe:
                continue
            # Verify price data exists
            if not self._get_price(t, date):
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

    def _can_trade(self, date):
        return self.daily_trade_count[date] < MAX_TRADES_PER_DAY

    def _check_cooldown(self, ticker, date):
        """Returns True if we CAN buy (cooldown expired or no recent sell)."""
        last_sell = self.sell_dates.get(ticker)
        if not last_sell:
            return True
        sell_dt = datetime.strptime(last_sell, "%Y-%m-%d")
        curr_dt = datetime.strptime(date, "%Y-%m-%d")
        return (curr_dt - sell_dt).days >= SELL_COOLDOWN_DAYS

    def run(self):
        """Run full V2 simulation."""
        days = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT date FROM prices WHERE ticker='SPY' ORDER BY date").fetchall()]

        if not days:
            print("ERROR: No price data.")
            return

        print(f"Simulating {len(days)} days: {days[0]} -> {days[-1]}")
        print(f"Capital: ${INITIAL_CAPITAL:,}")
        print(f"Universe starts EMPTY — tickers discovered from events\n")

        qqq_start = self._get_price("QQQ", days[0])
        spy_start = self._get_price("SPY", days[0])

        for i, date in enumerate(days):
            self.daily_trade_count[date] = 0

            # 1. Stop loss checks (sector-aware)
            sells = []
            for ticker, pos in list(self.positions.items()):
                price = self._get_price(ticker, date)
                if not price:
                    continue

                pos.peak_price = max(pos.peak_price, price)
                hard_stop, trail_stop = self._get_stops(ticker)

                # Hard stop
                pnl_pct = (price - pos.entry_price) / pos.entry_price
                if pnl_pct <= hard_stop:
                    sells.append((ticker, f"hard_stop_{pnl_pct:.0%}({self._get_sector(ticker)})", price))
                    continue

                # Trailing stop
                trail_pct = (price - pos.peak_price) / pos.peak_price
                if trail_pct <= trail_stop:
                    sells.append((ticker, f"trail_stop_{trail_pct:.0%}", price))

            # Execute sells (respect daily limit)
            for ticker, reason, price in sells:
                if not self._can_trade(date):
                    break
                pos = self.positions.pop(ticker)
                value = pos.shares * price
                self.cash += value
                pnl = value - pos.value
                self.sell_dates[ticker] = date
                self.daily_trade_count[date] += 1
                self.trade_log.append({
                    "date": date, "ticker": ticker, "action": "SELL",
                    "price": price, "shares": pos.shares, "value": value,
                    "reason": reason, "pnl": pnl,
                    "entry_price": pos.entry_price, "entry_date": pos.entry_date,
                    "sector": pos.sector,
                })

            # 2. Rebalance check (configurable interval, default 5 = weekly)
            if i % self.rebalance_interval == 0 and self._can_trade(date):
                rankings = self._get_events_up_to(date)
                top_tickers = sorted(
                    [(t, s) for t, s in rankings.items() if s["ev_score"] >= MIN_EVENT_SCORE],
                    key=lambda x: (-x[1]["verdict_score"], -x[1]["ev_score"])
                )[:MAX_POSITIONS * 2]  # Over-fetch for filtering

                # Smart rotation
                available_slots = MAX_POSITIONS - len(self.positions)
                if available_slots <= 0 and top_tickers:
                    ranking_map = {t: s for t, s in top_tickers}
                    held_scores = []
                    for t in self.positions:
                        score = ranking_map.get(t, {}).get("verdict_score", -999)
                        held_scores.append((t, score))
                    held_scores.sort(key=lambda x: x[1])

                    candidates = [(t, s) for t, s in top_tickers if t not in self.positions]

                    for cand_ticker, cand_score_dict in candidates:
                        if not held_scores or not self._can_trade(date):
                            break
                        weakest_t, weakest_s = held_scores[0]
                        cand_s = cand_score_dict.get("verdict_score", 0)

                        if cand_s > weakest_s * 1.5 and cand_s > 0:
                            # Check sector concentration for candidate
                            cand_sector = self._get_sector(cand_ticker)
                            if self._sector_count(cand_sector) >= MAX_PER_SECTOR:
                                continue
                            # Check cooldown for candidate
                            if not self._check_cooldown(cand_ticker, date):
                                continue

                            pos = self.positions.pop(weakest_t)
                            price = self._get_price(weakest_t, date)
                            if price:
                                value = pos.shares * price
                                self.cash += value
                                self.sell_dates[weakest_t] = date
                                self.daily_trade_count[date] += 1
                                self.trade_log.append({
                                    "date": date, "ticker": weakest_t, "action": "SELL",
                                    "price": price, "shares": pos.shares, "value": value,
                                    "reason": f"rotation_for_{cand_ticker}",
                                    "pnl": value - pos.value,
                                    "entry_price": pos.entry_price, "entry_date": pos.entry_date,
                                    "sector": pos.sector,
                                })
                            held_scores.pop(0)
                        else:
                            break

                # Buy top picks
                available_slots = MAX_POSITIONS - len(self.positions)
                portfolio_val = self._portfolio_value(date)
                per_position = portfolio_val * POSITION_PCT

                for ticker, score in top_tickers:
                    if available_slots <= 0 or self.cash < per_position * 0.5:
                        break
                    if not self._can_trade(date):
                        break
                    if ticker in self.positions:
                        continue
                    if not self._check_cooldown(ticker, date):
                        continue

                    # Sector concentration check
                    sector = self._get_sector(ticker)
                    if self._sector_count(sector) >= MAX_PER_SECTOR:
                        continue

                    price = self._get_price(ticker, date)
                    if not price or price <= 0:
                        continue

                    buy_amount = min(per_position, self.cash)
                    shares = buy_amount / price
                    self.cash -= buy_amount
                    self.positions[ticker] = Position(ticker, date, price, shares, buy_amount, sector)
                    available_slots -= 1
                    self.daily_trade_count[date] += 1

                    self.trade_log.append({
                        "date": date, "ticker": ticker, "action": "BUY",
                        "price": price, "shares": shares, "value": buy_amount,
                        "reason": f"ev={score['ev_score']:.1f}_v={score['verdict_score']:.1f}",
                        "sector": sector,
                    })

            # Daily snapshot
            port_val = self._portfolio_value(date)
            qqq_now = self._get_price("QQQ", date)
            spy_now = self._get_price("SPY", date)

            self.daily_snapshots.append({
                "date": date,
                "portfolio_value": port_val,
                "cash": self.cash,
                "positions": len(self.positions),
                "universe_size": len(self.discovered_universe),
                "cumulative_return": (port_val / INITIAL_CAPITAL - 1) * 100,
                "qqq_return": ((qqq_now / qqq_start - 1) * 100) if qqq_start and qqq_now else 0,
                "spy_return": ((spy_now / spy_start - 1) * 100) if spy_start and spy_now else 0,
            })

        self._save_results()
        self._print_summary()

    def _save_results(self):
        conn = db.get_conn()
        # Clear old V2 results
        conn.execute("DELETE FROM backtest_trades")
        conn.execute("DELETE FROM backtest_daily")

        for t in self.trade_log:
            conn.execute(
                "INSERT INTO backtest_trades (date, ticker, action, price, shares, value, reason, portfolio_value, cash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t["date"], t["ticker"], t["action"], t["price"], t["shares"], t["value"],
                 t.get("reason", ""), self._portfolio_value(t["date"]), self.cash))

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
        if not self.daily_snapshots:
            print("No data")
            return

        first = self.daily_snapshots[0]
        last = self.daily_snapshots[-1]

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

        buys = sum(1 for t in self.trade_log if t["action"] == "BUY")
        sells = len(completed)
        total_pnl = sum(t.get("pnl", 0) for t in completed)
        avg_pnl = total_pnl / len(completed) if completed else 0

        # Sharpe
        daily_returns = []
        for i in range(1, len(self.daily_snapshots)):
            prev = self.daily_snapshots[i-1]["portfolio_value"]
            curr = self.daily_snapshots[i]["portfolio_value"]
            if prev > 0:
                daily_returns.append((curr - prev) / prev)

        if len(daily_returns) > 1:
            avg_daily = statistics.mean(daily_returns)
            std_daily = statistics.stdev(daily_returns)
            sharpe = (avg_daily / std_daily) * (252 ** 0.5) if std_daily > 0 else 0
        else:
            sharpe = 0

        # Sector distribution of trades
        sector_trades = defaultdict(lambda: {"buys": 0, "wins": 0, "pnl": 0})
        for t in self.trade_log:
            s = t.get("sector", "unknown")
            if t["action"] == "BUY":
                sector_trades[s]["buys"] += 1
            elif t["action"] == "SELL":
                if t.get("pnl", 0) > 0:
                    sector_trades[s]["wins"] += 1
                sector_trades[s]["pnl"] += t.get("pnl", 0)

        # Cooldown & limit stats
        cooldown_blocks = 0  # can't track exactly but we can count zero-trade days

        print("\n" + "=" * 60)
        print("BACKTEST V2 RESULTS (Sector-Aware + Dynamic Universe)")
        print("=" * 60)
        print(f"Period:           {first['date']} -> {last['date']}")
        print(f"Trading days:     {len(self.daily_snapshots)}")
        print(f"Universe:         0 -> {len(self.discovered_universe)} tickers (discovered from events)")
        print()
        print(f"{'='*20} RETURNS {'='*20}")
        print(f"  Portfolio:      {total_return:+.1f}%  (${INITIAL_CAPITAL:,} -> ${last['portfolio_value']:,.0f})")
        print(f"  QQQ:            {qqq_return:+.1f}%")
        print(f"  SPY:            {spy_return:+.1f}%")
        print(f"  Alpha vs QQQ:   {alpha_qqq:+.1f}%")
        print(f"  Alpha vs SPY:   {alpha_spy:+.1f}%")
        print()
        print(f"{'='*20} RISK {'='*22}")
        print(f"  Max Drawdown:   {max_dd:.1f}%")
        print(f"  Sharpe Ratio:   {sharpe:.2f}")
        print()
        print(f"{'='*20} TRADES {'='*20}")
        print(f"  Total buys:     {buys}")
        print(f"  Total sells:    {sells}")
        print(f"  Win rate:       {win_rate:.0f}%")
        print(f"  Avg P&L/trade:  ${avg_pnl:,.0f}")
        print(f"  Total realized: ${total_pnl:,.0f}")
        print()
        print(f"{'='*20} SECTOR BREAKDOWN {'='*10}")
        for s, data in sorted(sector_trades.items(), key=lambda x: -x[1]["pnl"]):
            wr = (data["wins"] / max(data["buys"], 1)) * 100
            print(f"  {s:18s}  buys={data['buys']:2d}  wr={wr:.0f}%  pnl=${data['pnl']:+,.0f}")
        print()
        print(f"{'='*20} CURRENT {'='*20}")
        print(f"  Positions:      {len(self.positions)}")
        print(f"  Cash:           ${self.cash:,.0f}")
        for t, p in sorted(self.positions.items()):
            curr_price = self._get_price(t, last["date"])
            pnl_pct = ((curr_price - p.entry_price) / p.entry_price * 100) if curr_price else 0
            print(f"    {t:6s} ({p.sector:15s}): ${p.entry_price:.2f} -> ${curr_price:.2f} ({pnl_pct:+.1f}%)")
        print()

        # Compare with V1
        print(f"{'='*20} V1 COMPARISON {'='*14}")
        print(f"  V1 (fixed stops):       +170.3%, Sharpe 2.74, DD -19.9%, WR 62%")
        print(f"  V2 (sector-aware):      {total_return:+.1f}%, Sharpe {sharpe:.2f}, DD {max_dd:.1f}%, WR {win_rate:.0f}%")
        diff = total_return - 170.3
        print(f"  Delta:                  {diff:+.1f}%")
        print("=" * 60)


if __name__ == "__main__":
    sim = SimulatorV2()
    sim.run()
