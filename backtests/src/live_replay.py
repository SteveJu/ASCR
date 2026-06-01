"""Live-style Radar + Paper replay from a blank state.

This simulator is stricter than ``simulator_v2``:
- events are ingested chronologically into an initially empty memory;
- events are only tradable after an execution lag;
- Radar generates instructions from current memory and current positions;
- Paper applies execution rejections before orders are recorded.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import statistics

import yaml

from src import db
from src.config import ROOT


INITIAL_CAPITAL = 10_000.0
MAX_POSITIONS = 10
POSITION_PCT = 0.10
MIN_EVENT_SCORE = 5.0
EVENT_LOOKBACK_DAYS = 30
EXECUTION_LAG_TRADING_DAYS = 1

SELL_COOLDOWN_DAYS = 3
MAX_TRADES_PER_DAY = 4
MAX_DAILY_TURNOVER_PCT = 30.0
PDT_MAX_DAY_TRADES = 3
PDT_WINDOW_TRADING_DAYS = 5
MIN_TRADE_VALUE = 50.0

LIVE_UNIVERSE_FILE = ROOT.parent / "ASCR" / "config" / "universe.yaml"

SOURCE_PROFILES = {
    "sec_only": {
        "sec",
        "sec_filing",
        "sec_8k",
        "sec_8k_backtest",
    },
    "sec_form4_13f": {
        "sec",
        "sec_filing",
        "sec_8k",
        "sec_8k_backtest",
        "form4",
        "sec_form4",
        "insider_backtest",
        "sec_13f",
        "13f",
        "13f_backtest",
        "13f_institutional",
        "institutional_13f",
    },
    "news_exploratory": {
        "sec",
        "sec_filing",
        "sec_8k",
        "sec_8k_backtest",
        "form4",
        "sec_form4",
        "insider_backtest",
        "sec_13f",
        "13f",
        "13f_backtest",
        "13f_institutional",
        "institutional_13f",
        "news",
        "google_news",
        "historical_news",
        "google_news_probe",
    },
}

# Mirrors live ASCR sector_strategy.py stop levels.
SECTOR_STOPS = {
    "memory_storage": (-20.0, -25.0),
    "optical": (-20.0, -25.0),
    "compute": (-18.0, -22.0),
    "power_cooling": (-22.0, -28.0),
    "energy_grid": (-22.0, -28.0),
    "semicap": (-20.0, -25.0),
    "networking": (-20.0, -25.0),
    "data_center": (-18.0, -22.0),
    "eda_ip": (-18.0, -22.0),
    "new_additions": (-15.0, -20.0),
    "memory": (-20.0, -25.0),
    "unknown": (-20.0, -25.0),
}


@dataclass
class Position:
    ticker: str
    entry_date: str
    entry_price: float
    shares: float
    cost_basis: float
    peak_price: float
    sector: str


@dataclass
class ReplayEvent:
    date: str
    available_date: str
    ticker: str
    source: str
    headline: str
    event_type: str
    evidence_delta: float
    verdict: str
    conviction: str
    confidence: float
    hash: str


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_universe() -> dict:
    """Prefer live radar config for trading exclusions, fallback to backtest."""
    live = _load_yaml(LIVE_UNIVERSE_FILE)
    local = _load_yaml(ROOT / "config" / "universe.yaml")
    return live or local


def _sector_map(universe: dict) -> dict[str, str]:
    mapping = {}
    for sector, data in universe.get("sectors", {}).items():
        if not isinstance(data, dict):
            continue
        for ticker in data.get("tickers", []):
            mapping[str(ticker).upper()] = sector
    return mapping


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _resolve_event_sources(profile: str) -> set[str] | None:
    manual = os.environ.get("LIVE_REPLAY_EVENT_SOURCES", "").strip()
    if manual:
        return {s.strip() for s in manual.split(",") if s.strip()}
    if profile in ("all", "all_available", ""):
        return None
    if profile not in SOURCE_PROFILES:
        valid = ", ".join(["all_available"] + sorted(SOURCE_PROFILES))
        raise ValueError(f"Unknown LIVE_REPLAY_PROFILE={profile!r}. Valid profiles: {valid}")
    return SOURCE_PROFILES[profile]


class LiveReplay:
    """Replay live Radar+Paper mechanics without prior memory."""

    def __init__(self, run_id: str | None = None, profile: str | None = None):
        self.conn = db.get_conn()
        self.profile = profile or os.environ.get("LIVE_REPLAY_PROFILE", "all_available")
        self.allowed_sources = _resolve_event_sources(self.profile)
        run_prefix = self.profile if self.profile else "all_available"
        self.run_id = run_id or datetime.now().strftime(f"live_{run_prefix}_%Y%m%d_%H%M%S")
        self.universe = _load_universe()
        self.ticker_sector = _sector_map(self.universe)
        self.excluded = set(
            self.universe.get("excluded_from_trading", {}).get("tickers", [])
        )

        self.cash = INITIAL_CAPITAL
        self.positions: dict[str, Position] = {}
        self.event_memory: list[ReplayEvent] = []
        self.discovered: set[str] = set()
        self.sell_dates: dict[str, str] = {}
        self.daily_trade_count: defaultdict[str, int] = defaultdict(int)
        self.daily_turnover: defaultdict[str, float] = defaultdict(float)
        self.trade_log: list[dict] = []
        self.blocked_log: list[dict] = []
        self.daily_snapshots: list[dict] = []
        self.day_trade_history: deque[tuple[str, str]] = deque()

        self._ensure_tables()

    def _ensure_tables(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_replay_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                shares REAL NOT NULL,
                value REAL NOT NULL,
                reason TEXT,
                portfolio_value REAL,
                cash REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS live_replay_blocked (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                rule TEXT,
                value REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS live_replay_daily (
                run_id TEXT NOT NULL,
                date TEXT NOT NULL,
                portfolio_value REAL,
                cash REAL,
                positions_count INTEGER,
                event_memory_count INTEGER,
                discovered_count INTEGER,
                daily_return REAL,
                cumulative_return REAL,
                qqq_cumulative REAL,
                spy_cumulative REAL,
                PRIMARY KEY (run_id, date)
            );
            """
        )
        self.conn.commit()

    def _trading_days(self) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT date FROM prices WHERE ticker='SPY' ORDER BY date"
            ).fetchall()
        ]

    def _get_price(self, ticker: str, date: str) -> float | None:
        row = self.conn.execute(
            "SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (ticker, date),
        ).fetchone()
        return float(row["close"]) if row else None

    def _next_trading_day(self, day_index: dict[str, int], days: list[str], date: str) -> str | None:
        """Return date after EXECUTION_LAG_TRADING_DAYS trading days."""
        if date in day_index:
            idx = day_index[date]
        else:
            idx = None
            for i, day in enumerate(days):
                if day >= date:
                    idx = i
                    break
            if idx is None:
                return None
        available_idx = idx + EXECUTION_LAG_TRADING_DAYS
        if available_idx >= len(days):
            return None
        return days[available_idx]

    def _load_events(self, days: list[str]) -> dict[str, list[ReplayEvent]]:
        day_index = {day: i for i, day in enumerate(days)}
        rows = self.conn.execute(
            """
            SELECT date, ticker, source, headline, event_type, evidence_delta,
                   verdict, conviction, confidence, hash
            FROM events
            ORDER BY date, id
            """
        ).fetchall()

        by_available: defaultdict[str, list[ReplayEvent]] = defaultdict(list)
        for r in rows:
            source = r["source"] or ""
            if self.allowed_sources is not None and source not in self.allowed_sources:
                continue
            available = self._next_trading_day(day_index, days, r["date"])
            if available is None:
                continue
            ticker = (r["ticker"] or "").upper()
            if not ticker:
                continue
            ev_hash = r["hash"] or hashlib.md5(
                f"{r['date']}|{ticker}|{r['headline']}".encode()
            ).hexdigest()
            by_available[available].append(
                ReplayEvent(
                    date=r["date"],
                    available_date=available,
                    ticker=ticker,
                    source=source,
                    headline=r["headline"] or "",
                    event_type=r["event_type"] or "other",
                    evidence_delta=float(r["evidence_delta"] or 0),
                    verdict=(r["verdict"] or "HOLD").upper(),
                    conviction=(r["conviction"] or "LOW").upper(),
                    confidence=float(r["confidence"] or 0.5),
                    hash=ev_hash,
                )
            )
        return by_available

    def _sector(self, ticker: str) -> str:
        return self.ticker_sector.get(ticker.upper(), "unknown")

    def _stops(self, ticker: str) -> tuple[float, float]:
        return SECTOR_STOPS.get(self._sector(ticker), SECTOR_STOPS["unknown"])

    def _portfolio_value(self, date: str) -> float:
        total = self.cash
        for pos in self.positions.values():
            price = self._get_price(pos.ticker, date)
            if price:
                total += pos.shares * price
        return total

    def _recent_events(self, date: str) -> list[ReplayEvent]:
        cutoff = _parse_date(date) - timedelta(days=EVENT_LOOKBACK_DAYS)
        return [e for e in self.event_memory if _parse_date(e.available_date) >= cutoff]

    def _rankings(self, date: str) -> list[dict]:
        verdict_map = {"STRONG_BUY": 3, "BUY": 2, "HOLD": 0, "AVOID": -2, "SELL": -3}
        conviction_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        scores = defaultdict(
            lambda: {
                "ev_sum": 0.0,
                "count": 0,
                "verdict_sum": 0.0,
                "conviction_sum": 0.0,
                "sources": set(),
                "top_headline": "",
            }
        )
        for event in self._recent_events(date):
            ticker = event.ticker
            if ticker not in self.discovered:
                continue
            if self._get_price(ticker, date) is None:
                continue
            s = scores[ticker]
            s["ev_sum"] += event.evidence_delta
            s["count"] += 1
            s["verdict_sum"] += verdict_map.get(event.verdict, 0)
            s["conviction_sum"] += conviction_map.get(event.conviction, 1)
            s["sources"].add(event.source)
            if not s["top_headline"] or event.evidence_delta > s.get("top_ev", -999):
                s["top_headline"] = event.headline
                s["top_ev"] = event.evidence_delta

        rankings = []
        for ticker, s in scores.items():
            count = s["count"]
            if count <= 0:
                continue
            ev_score = round((s["ev_sum"] / count) * min(count, 8), 1)
            if ev_score < MIN_EVENT_SCORE:
                continue
            avg_verdict = s["verdict_sum"] / count
            avg_conviction = s["conviction_sum"] / count
            rankings.append(
                {
                    "ticker": ticker,
                    "ev_score": ev_score,
                    "verdict_score": round(avg_verdict * avg_conviction, 1),
                    "ev_count": count,
                    "sources": ",".join(sorted(s["sources"])),
                    "top_headline": s["top_headline"],
                }
            )

        rankings.sort(
            key=lambda r: (r["verdict_score"], r["ev_score"], r["ev_count"], r["ticker"]),
            reverse=True,
        )
        return rankings

    def _check_bubble(self, date: str) -> dict:
        """Replay live bubble_detector's one-day synchronized crash check."""
        changes = []
        for ticker in self.ticker_sector:
            price_today = self._get_price(ticker, date)
            prev_row = self.conn.execute(
                """
                SELECT close FROM prices
                WHERE ticker=? AND date<?
                ORDER BY date DESC LIMIT 1
                """,
                (ticker, date),
            ).fetchone()
            if not price_today or not prev_row or not prev_row["close"]:
                continue
            change = (price_today - float(prev_row["close"])) / float(prev_row["close"])
            changes.append(change)
        if not changes:
            return {"level": None, "action": "none"}
        pct_declining = sum(1 for c in changes if c < 0) / len(changes)
        pct_crash_10 = sum(1 for c in changes if c <= -0.10) / len(changes)
        if pct_declining >= 0.95 and pct_crash_10 >= 0.80:
            return {"level": "MELTDOWN", "action": "liquidate"}
        return {"level": None, "action": "none"}

    def _sell_signals(self, date: str, rankings: list[dict]) -> tuple[list[dict], list[dict]]:
        ranking_map = {r["ticker"]: r for r in rankings}
        sells = []
        holds = []

        bubble = self._check_bubble(date)
        if bubble["action"] == "liquidate":
            for ticker, pos in self.positions.items():
                price = self._get_price(ticker, date) or pos.entry_price
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                sells.append(
                    {
                        "ticker": ticker,
                        "reason": "MELTDOWN_LIQUIDATE",
                        "urgency": "critical",
                        "pnl_pct": pnl_pct,
                    }
                )
            return sells, holds

        for ticker, pos in self.positions.items():
            price = self._get_price(ticker, date)
            if not price:
                continue
            pos.peak_price = max(pos.peak_price, price)
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            drop_from_peak = (price - pos.peak_price) / pos.peak_price * 100
            hard_stop, trailing_stop = self._stops(ticker)

            sell_reason = None
            urgency = "normal"
            if pnl_pct <= hard_stop:
                sell_reason = f"hard_stop_{pnl_pct:.0f}%_({pos.sector}:{hard_stop:.0f}%)"
                urgency = "urgent"
            elif drop_from_peak <= trailing_stop:
                sell_reason = (
                    f"trailing_stop_peak${pos.peak_price:.0f}_"
                    f"drop{drop_from_peak:.0f}%_({pos.sector}:{trailing_stop:.0f}%)"
                )
                urgency = "urgent"
            else:
                neg_ev = sum(
                    e.evidence_delta
                    for e in self._recent_events(date)
                    if e.ticker == ticker and e.evidence_delta < 0
                )
                if neg_ev <= -8:
                    sell_reason = f"thesis_break_ev{neg_ev:+.0f}"

            if sell_reason:
                sells.append(
                    {
                        "ticker": ticker,
                        "reason": sell_reason,
                        "urgency": urgency,
                        "pnl_pct": pnl_pct,
                    }
                )
            else:
                rank = next(
                    (idx + 1 for idx, r in enumerate(rankings) if r["ticker"] == ticker),
                    99,
                )
                holds.append(
                    {
                        "ticker": ticker,
                        "rank": rank,
                        "note": f"rank#{rank} pnl={pnl_pct:+.1f}%",
                        "verdict_score": ranking_map.get(ticker, {}).get("verdict_score", -999),
                    }
                )
        return sells, holds

    def _instructions(self, date: str) -> dict:
        rankings = self._rankings(date)
        sells, holds = self._sell_signals(date, rankings)
        selling = {s["ticker"] for s in sells}
        held = set(self.positions) - selling
        available_slots = MAX_POSITIONS - len(held)

        if available_slots <= 0 and rankings:
            ranking_map = {r["ticker"]: r for r in rankings}
            held_scores = sorted(
                (
                    (ticker, ranking_map.get(ticker, {}).get("verdict_score", -999))
                    for ticker in sorted(held)
                ),
                key=lambda x: (x[1], x[0]),
            )
            candidates = [r for r in rankings if r["ticker"] not in held]
            for candidate in candidates:
                if not held_scores:
                    break
                weakest, weakest_score = held_scores[0]
                cand_score = candidate["verdict_score"]
                if cand_score > weakest_score * 1.5 and cand_score > 0:
                    sells.append(
                        {
                            "ticker": weakest,
                            "reason": f"rotation_for_{candidate['ticker']}_score_{cand_score:.1f}>{weakest_score:.1f}",
                            "urgency": "low",
                            "pnl_pct": 0,
                        }
                    )
                    held.discard(weakest)
                    held_scores.pop(0)
                    available_slots += 1
                else:
                    break

        buys = []
        for idx, r in enumerate(rankings):
            ticker = r["ticker"]
            if ticker in held or ticker in self.excluded:
                continue
            buys.append(
                {
                    "ticker": ticker,
                    "rank": idx + 1,
                    "reason": f"rank#{idx+1}_ev{r['ev_score']:+.0f}",
                    "ev_score": r["ev_score"],
                    "verdict_score": r["verdict_score"],
                }
            )

        return {"sells": sells, "buys": buys, "holds": holds, "rankings": rankings}

    def _recent_day_trade_count(self, date: str, trading_days_so_far: list[str]) -> int:
        window_days = set(trading_days_so_far[-PDT_WINDOW_TRADING_DAYS:])
        return sum(1 for d, _ in self.day_trade_history if d in window_days and d <= date)

    def _would_day_trade(self, ticker: str, action: str, date: str) -> bool:
        opposite = "BUY" if action == "SELL" else "SELL"
        return any(
            t["date"] == date and t["ticker"] == ticker and t["action"] == opposite
            for t in self.trade_log
        )

    def _block(self, date: str, ticker: str, action: str, reason: str, rule: str, value: float):
        self.blocked_log.append(
            {
                "date": date,
                "ticker": ticker,
                "action": action,
                "reason": reason,
                "rule": rule,
                "value": value,
            }
        )

    def _validate_trade(
        self,
        date: str,
        trading_days_so_far: list[str],
        ticker: str,
        action: str,
        trade_value: float,
        portfolio_value: float,
        reason: str,
        urgency: str = "normal",
    ) -> bool:
        if self.daily_trade_count[date] >= MAX_TRADES_PER_DAY:
            self._block(date, ticker, action, reason, "daily_trade_limit", trade_value)
            return False

        projected_turnover = self.daily_turnover[date] + trade_value
        if portfolio_value > 0 and projected_turnover / portfolio_value * 100 > MAX_DAILY_TURNOVER_PCT:
            self._block(date, ticker, action, reason, "daily_turnover_cap", trade_value)
            return False

        if action == "BUY":
            last_sell = self.sell_dates.get(ticker)
            if last_sell:
                days_since = (_parse_date(date) - _parse_date(last_sell)).days
                if days_since < SELL_COOLDOWN_DAYS:
                    self._block(date, ticker, action, reason, "sell_cooldown", trade_value)
                    return False
            if trade_value < MIN_TRADE_VALUE:
                self._block(date, ticker, action, reason, "minimum_trade", trade_value)
                return False

        if portfolio_value < 25_000 and self._would_day_trade(ticker, action, date):
            used = self._recent_day_trade_count(date, trading_days_so_far)
            if urgency == "critical":
                pass
            elif urgency == "urgent" and used < PDT_MAX_DAY_TRADES:
                pass
            else:
                self._block(date, ticker, action, reason, "pdt_day_trade", trade_value)
                return False

        return True

    def _record_trade(
        self,
        date: str,
        ticker: str,
        action: str,
        price: float,
        shares: float,
        value: float,
        reason: str,
    ):
        self.daily_trade_count[date] += 1
        self.daily_turnover[date] += value
        self.trade_log.append(
            {
                "date": date,
                "ticker": ticker,
                "action": action,
                "price": price,
                "shares": shares,
                "value": value,
                "reason": reason,
                "portfolio_value": self._portfolio_value(date),
                "cash": self.cash,
            }
        )
        if self._would_day_trade(ticker, action, date):
            self.day_trade_history.append((date, ticker))

    def run(self):
        days = self._trading_days()
        if not days:
            print("ERROR: No price data.")
            return
        events_by_day = self._load_events(days)
        qqq_start = self._get_price("QQQ", days[0])
        spy_start = self._get_price("SPY", days[0])

        print(f"Live replay run_id: {self.run_id}")
        print(f"Source profile: {self.profile}")
        if self.allowed_sources is None:
            print("Event sources: all available")
        else:
            print(f"Event sources: {', '.join(sorted(self.allowed_sources))}")
        print(f"Simulating {len(days)} days: {days[0]} -> {days[-1]}")
        print("Initial state: no positions, no event memory, no sell history")
        print(f"Excluded from buys: {', '.join(sorted(self.excluded)) or '(none)'}")

        previous_value = INITIAL_CAPITAL
        trading_days_so_far = []
        for date in days:
            trading_days_so_far.append(date)
            self.daily_trade_count[date] = 0
            self.daily_turnover[date] = 0.0

            for event in events_by_day.get(date, []):
                self.event_memory.append(event)
                self.discovered.add(event.ticker)

            instructions = self._instructions(date)
            portfolio_value = self._portfolio_value(date)

            for sell in instructions["sells"]:
                ticker = sell["ticker"]
                pos = self.positions.get(ticker)
                if not pos:
                    continue
                price = self._get_price(ticker, date)
                if not price:
                    continue
                value = pos.shares * price
                if not self._validate_trade(
                    date,
                    trading_days_so_far,
                    ticker,
                    "SELL",
                    value,
                    portfolio_value,
                    sell["reason"],
                    sell.get("urgency", "normal"),
                ):
                    continue
                self.cash += value
                self.sell_dates[ticker] = date
                self.positions.pop(ticker)
                self._record_trade(date, ticker, "SELL", price, pos.shares, value, sell["reason"])
                portfolio_value = self._portfolio_value(date)

            slots = MAX_POSITIONS - len(self.positions)
            target_value = self._portfolio_value(date) * POSITION_PCT
            for buy in instructions["buys"]:
                if slots <= 0:
                    break
                ticker = buy["ticker"]
                if ticker in self.positions:
                    continue
                price = self._get_price(ticker, date)
                if not price:
                    continue
                amount = min(target_value, self.cash)
                if not self._validate_trade(
                    date,
                    trading_days_so_far,
                    ticker,
                    "BUY",
                    amount,
                    self._portfolio_value(date),
                    buy["reason"],
                    "normal",
                ):
                    continue
                shares = amount / price
                self.cash -= amount
                sector = self._sector(ticker)
                self.positions[ticker] = Position(
                    ticker=ticker,
                    entry_date=date,
                    entry_price=price,
                    shares=shares,
                    cost_basis=amount,
                    peak_price=price,
                    sector=sector,
                )
                self._record_trade(date, ticker, "BUY", price, shares, amount, buy["reason"])
                slots -= 1

            port_val = self._portfolio_value(date)
            qqq_now = self._get_price("QQQ", date)
            spy_now = self._get_price("SPY", date)
            daily_return = (port_val - previous_value) / previous_value * 100 if previous_value else 0
            previous_value = port_val
            self.daily_snapshots.append(
                {
                    "date": date,
                    "portfolio_value": port_val,
                    "cash": self.cash,
                    "positions": len(self.positions),
                    "event_memory_count": len(self.event_memory),
                    "discovered_count": len(self.discovered),
                    "daily_return": daily_return,
                    "cumulative_return": (port_val / INITIAL_CAPITAL - 1) * 100,
                    "qqq_return": ((qqq_now / qqq_start - 1) * 100) if qqq_start and qqq_now else 0,
                    "spy_return": ((spy_now / spy_start - 1) * 100) if spy_start and spy_now else 0,
                }
            )

        self._save_results()
        self._print_summary()

    def _save_results(self):
        self.conn.execute("DELETE FROM live_replay_trades WHERE run_id=?", (self.run_id,))
        self.conn.execute("DELETE FROM live_replay_blocked WHERE run_id=?", (self.run_id,))
        self.conn.execute("DELETE FROM live_replay_daily WHERE run_id=?", (self.run_id,))

        for t in self.trade_log:
            self.conn.execute(
                """
                INSERT INTO live_replay_trades
                (run_id, date, ticker, action, price, shares, value, reason, portfolio_value, cash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    t["date"],
                    t["ticker"],
                    t["action"],
                    t["price"],
                    t["shares"],
                    t["value"],
                    t["reason"],
                    t.get("portfolio_value", 0),
                    t.get("cash", 0),
                ),
            )

        for b in self.blocked_log:
            self.conn.execute(
                """
                INSERT INTO live_replay_blocked
                (run_id, date, ticker, action, reason, rule, value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    b["date"],
                    b["ticker"],
                    b["action"],
                    b["reason"],
                    b["rule"],
                    b["value"],
                ),
            )

        for s in self.daily_snapshots:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO live_replay_daily
                (run_id, date, portfolio_value, cash, positions_count, event_memory_count,
                 discovered_count, daily_return, cumulative_return, qqq_cumulative, spy_cumulative)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    s["date"],
                    s["portfolio_value"],
                    s["cash"],
                    s["positions"],
                    s["event_memory_count"],
                    s["discovered_count"],
                    s["daily_return"],
                    s["cumulative_return"],
                    s["qqq_return"],
                    s["spy_return"],
                ),
            )
        self.conn.commit()

    def _print_summary(self):
        if not self.daily_snapshots:
            print("No data")
            return

        first = self.daily_snapshots[0]
        last = self.daily_snapshots[-1]
        daily_returns = [
            (self.daily_snapshots[i]["portfolio_value"] - self.daily_snapshots[i - 1]["portfolio_value"])
            / self.daily_snapshots[i - 1]["portfolio_value"]
            for i in range(1, len(self.daily_snapshots))
            if self.daily_snapshots[i - 1]["portfolio_value"] > 0
        ]
        sharpe = 0.0
        if len(daily_returns) > 1:
            std = statistics.stdev(daily_returns)
            sharpe = statistics.mean(daily_returns) / std * (252**0.5) if std > 0 else 0.0

        peak = INITIAL_CAPITAL
        max_dd = 0.0
        for s in self.daily_snapshots:
            peak = max(peak, s["portfolio_value"])
            max_dd = min(max_dd, (s["portfolio_value"] - peak) / peak * 100)

        sells = [t for t in self.trade_log if t["action"] == "SELL"]
        buys = [t for t in self.trade_log if t["action"] == "BUY"]
        realized = 0.0
        for sell in sells:
            # Use saved positions are gone; infer realized P&L from matching prior buy lots by ticker.
            # Summary realized is approximate; portfolio value is authoritative.
            pass

        print("\n" + "=" * 64)
        print("LIVE REPLAY RESULTS (Blank Memory + Radar/Paper Rules)")
        print("=" * 64)
        print(f"Run ID:           {self.run_id}")
        print(f"Profile:          {self.profile}")
        print(f"Period:           {first['date']} -> {last['date']}")
        print(f"Trading days:     {len(self.daily_snapshots)}")
        print(f"Event memory:     0 -> {last['event_memory_count']} available events")
        print(f"Discovered:       0 -> {last['discovered_count']} tickers")
        print()
        print(f"Portfolio:        {last['cumulative_return']:+.1f}%  (${INITIAL_CAPITAL:,.0f} -> ${last['portfolio_value']:,.0f})")
        print(f"QQQ:              {last['qqq_return']:+.1f}%")
        print(f"SPY:              {last['spy_return']:+.1f}%")
        print(f"Max Drawdown:     {max_dd:.1f}%")
        print(f"Sharpe Ratio:     {sharpe:.2f}")
        print()
        print(f"Buys/Sells:       {len(buys)} / {len(sells)}")
        print(f"Blocked orders:   {len(self.blocked_log)}")
        if self.blocked_log:
            by_rule = defaultdict(int)
            for b in self.blocked_log:
                by_rule[b["rule"]] += 1
            print("Blocked by rule:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_rule.items())))
        print()
        print(f"Positions:        {len(self.positions)}")
        print(f"Cash:             ${self.cash:,.0f}")
        for ticker, pos in sorted(self.positions.items()):
            price = self._get_price(ticker, last["date"]) or pos.entry_price
            pnl = (price - pos.entry_price) / pos.entry_price * 100
            print(f"  {ticker:6s} {pos.sector:15s} ${pos.entry_price:.2f} -> ${price:.2f} ({pnl:+.1f}%)")
        print("=" * 64)


def main():
    run_id = os.environ.get("LIVE_REPLAY_RUN_ID")
    sim = LiveReplay(run_id=run_id)
    sim.run()


if __name__ == "__main__":
    main()
