"""Trading Rules — enforces real-world constraints on paper trading.

Rules:
1. Market hours: only trade Mon-Fri 9:30 AM - 4:00 PM ET
2. PDT (Pattern Day Trader): <$25K account can't day trade (buy+sell same day)
3. T+2 settlement: sell proceeds available after 2 business days
4. No duplicate buys on same ticker same day
5. Position limits: enforced in executor, not here
"""
import sqlite3
from datetime import datetime, timedelta, time as dtime
from src.utils import get_logger

logger = get_logger("trading_rules")

# US market holidays 2026 (NYSE)
US_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}


def is_trading_day(dt: datetime = None) -> tuple[bool, str]:
    """Check if today is a trading day (weekday + not holiday). No time check."""
    if dt is None:
        dt = datetime.now()
    if dt.weekday() >= 5:
        day = "Saturday" if dt.weekday() == 5 else "Sunday"
        return False, f"weekend ({day})"
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in US_HOLIDAYS_2026:
        return False, f"market holiday ({date_str})"
    return True, "trading day"


def is_market_open(dt: datetime = None) -> tuple[bool, str]:
    """Check if market is open at given time. Returns (is_open, reason)."""
    if dt is None:
        dt = datetime.now()

    # Weekend
    if dt.weekday() >= 5:
        day = "Saturday" if dt.weekday() == 5 else "Sunday"
        return False, f"weekend ({day})"

    # Holiday
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in US_HOLIDAYS_2026:
        return False, f"market holiday ({date_str})"

    # Market hours: 9:30 AM - 4:00 PM ET
    market_open = dtime(9, 30)
    market_close = dtime(16, 0)
    t = dt.time()
    if t < market_open:
        return False, f"pre-market ({t.strftime('%H:%M')}, opens 9:30)"
    if t >= market_close:
        return False, f"after-hours ({t.strftime('%H:%M')}, closed at 16:00)"

    return True, "market open"


def next_market_open(dt: datetime = None) -> datetime:
    """Return next market open datetime."""
    if dt is None:
        dt = datetime.now()
    candidate = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    if dt.time() >= dtime(9, 30):
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.strftime("%Y-%m-%d") in US_HOLIDAYS_2026:
        candidate += timedelta(days=1)
    return candidate


# PDT: 3 day trades allowed per rolling 5 business days (<$25K accounts)
PDT_MAX_DAY_TRADES = 3
PDT_WINDOW_DAYS = 5


def get_pdt_budget(db_path: str, today: str = None) -> dict:
    """Check how many day trades have been used and remain.

    Returns {used, remaining, trades: [{date, ticker}]}
    """
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    # Look back 5 business days
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all orders in the window
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=8)).strftime("%Y-%m-%d")
    orders = conn.execute(
        "SELECT date, ticker, side FROM paper_orders WHERE date >= ? ORDER BY date",
        (cutoff,)
    ).fetchall()
    conn.close()

    # Count day trades: same ticker bought AND sold on same day
    day_trades = []
    buys_by_day = {}
    sells_by_day = {}

    for o in orders:
        key = (o["date"], o["ticker"])
        if o["side"] == "BUY":
            buys_by_day[key] = True
        elif o["side"] == "SELL":
            sells_by_day[key] = True

    for key in buys_by_day:
        if key in sells_by_day:
            day_trades.append({"date": key[0], "ticker": key[1]})

    # Only count last 5 business days
    trading_days = sorted(set(o["date"] for o in orders))[-PDT_WINDOW_DAYS:]
    recent_day_trades = [dt for dt in day_trades if dt["date"] in trading_days]

    used = len(recent_day_trades)
    remaining = PDT_MAX_DAY_TRADES - used

    return {
        "used": used,
        "remaining": max(0, remaining),
        "trades": recent_day_trades,
        "window": trading_days,
    }


def check_pdt_rule(account_equity: float, ticker: str, side: str,
                   db_path: str, today: str = None,
                   urgency: str = "normal") -> tuple[bool, str]:
    """PDT rule: 3 day trades per 5 business days for accounts <$25K.

    urgency levels:
      "normal" — don't use day trade budget
      "urgent" — use budget if available (hard stop, trailing stop)
      "critical" — always allow (MELTDOWN, delisted)

    Returns (allowed, reason).
    """
    if account_equity >= 25000:
        return True, "account >= $25K, PDT exempt"

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    would_be_day_trade = False

    if side == "SELL":
        bought_today = conn.execute(
            "SELECT COUNT(*) as c FROM paper_orders WHERE ticker=? AND date=? AND side='BUY'",
            (ticker, today)
        ).fetchone()["c"]
        would_be_day_trade = bought_today > 0

    elif side == "BUY":
        sold_today = conn.execute(
            "SELECT COUNT(*) as c FROM paper_orders WHERE ticker=? AND date=? AND side='SELL'",
            (ticker, today)
        ).fetchone()["c"]
        would_be_day_trade = sold_today > 0

    conn.close()

    if not would_be_day_trade:
        return True, "not a day trade"

    # It IS a day trade — check budget
    budget = get_pdt_budget(db_path, today)

    if urgency == "critical":
        # Always allow critical trades (MELTDOWN, delisted)
        return True, f"PDT day trade ALLOWED (critical urgency, {budget['remaining']}/{PDT_MAX_DAY_TRADES} remaining)"

    if urgency == "urgent" and budget["remaining"] > 0:
        # Allow urgent trades if budget available
        return True, f"PDT day trade ALLOWED (urgent, {budget['remaining']-1}/{PDT_MAX_DAY_TRADES} remaining after)"

    if budget["remaining"] <= 0:
        return False, f"PDT: {budget['used']}/{PDT_MAX_DAY_TRADES} day trades used in 5d window. Wait or set urgency=critical"

    # Normal urgency — don't waste day trade budget
    return False, f"PDT: would use day trade ({budget['remaining']}/{PDT_MAX_DAY_TRADES} remaining). Set urgency=urgent to override"


def check_settlement(ticker: str, side: str, db_path: str,
                     today: str = None) -> tuple[bool, str]:
    """T+1 settlement (SEC rule since May 2024): sell proceeds available next business day.

    For sells: always allowed.
    For buys with unsettled cash: check if proceeds from recent sells have settled.
    """
    if side == "SELL":
        return True, "sells always allowed"

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    # T+1: proceeds from sells settle 1 business day later
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get recent sells
    recent_sells = conn.execute("""
        SELECT date, ticker, quantity * price as proceeds
        FROM paper_orders WHERE side='SELL'
        AND date >= date(?, '-3 days')
        ORDER BY date DESC
    """, (today,)).fetchall()
    conn.close()

    unsettled = 0
    for sell in recent_sells:
        sell_date = datetime.strptime(sell["date"], "%Y-%m-%d")
        # Count business days since sell
        bdays = 0
        check = sell_date
        while check.strftime("%Y-%m-%d") < today:
            check += timedelta(days=1)
            if check.weekday() < 5 and check.strftime("%Y-%m-%d") not in US_HOLIDAYS_2026:
                bdays += 1
        if bdays < 1:  # T+1
            unsettled += sell["proceeds"]

    if unsettled > 0:
        return True, f"${unsettled:.0f} unsettled (T+1), usable but flagged"

    return True, "all cash settled"


def check_duplicate_buy(ticker: str, db_path: str,
                        today: str = None) -> tuple[bool, str]:
    """No duplicate buy on same ticker same day."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE ticker=? AND date=? AND side='BUY'",
        (ticker, today)
    ).fetchone()[0]
    conn.close()

    if count > 0:
        return False, f"already bought {ticker} today"
    return True, "no duplicate"


def validate_trade(ticker: str, side: str, account_equity: float,
                   db_path: str, dt: datetime = None,
                   scheduled_run: bool = False) -> tuple[bool, list[str]]:
    """Run all trading rules. Returns (allowed, list of violations/warnings).

    scheduled_run=True: daily pipeline run at close — only checks trading day,
    not intraday hours. Simulates MOC (Market-on-Close) orders.
    """
    if dt is None:
        dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    violations = []
    warnings = []

    # 1. Market hours / trading day
    if scheduled_run:
        is_day, day_reason = is_trading_day(dt)
        if not is_day:
            violations.append(f"Not a trading day: {day_reason}")
        else:
            warnings.append("Scheduled run: using closing price (MOC)")
    else:
        mkt_open, mkt_reason = is_market_open(dt)
        if not mkt_open:
            violations.append(f"Market closed: {mkt_reason}")

    # 2. PDT
    pdt_ok, pdt_reason = check_pdt_rule(account_equity, ticker, side, db_path, today)
    if not pdt_ok:
        violations.append(pdt_reason)

    # 3. Settlement
    settle_ok, settle_reason = check_settlement(ticker, side, db_path, today)
    if not settle_ok:
        violations.append(settle_reason)
    elif "unsettled" in settle_reason:
        warnings.append(settle_reason)

    # 4. Duplicate buy
    if side == "BUY":
        dup_ok, dup_reason = check_duplicate_buy(ticker, db_path, today)
        if not dup_ok:
            violations.append(dup_reason)

    allowed = len(violations) == 0
    all_messages = violations + [f"⚠️ {w}" for w in warnings]

    if violations:
        logger.warning(f"BLOCKED {side} {ticker}: {violations}")
    if warnings:
        logger.info(f"WARNING {side} {ticker}: {warnings}")

    return allowed, all_messages


# ============================================================
# ROBINHOOD-SAFE RULES
# Avoid patterns that trigger platform scrutiny
# ============================================================

# Cooldown: don't rebuy a ticker within N days of selling it
SELL_COOLDOWN_DAYS = 3

# Max trades per day (buy + sell combined)
MAX_TRADES_PER_DAY = 4

# Max portfolio turnover per day (as % of portfolio value)
MAX_DAILY_TURNOVER_PCT = 30

# Wash sale window (IRS: 30 days before/after for tax loss)
WASH_SALE_DAYS = 30


def check_sell_cooldown(ticker: str, side: str, db_path: str,
                        today: str = None) -> tuple[bool, str]:
    """Don't rebuy a ticker within SELL_COOLDOWN_DAYS of selling it.
    Prevents rapid flip patterns that look like day trading."""
    if side != "BUY":
        return True, "not a buy"

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=SELL_COOLDOWN_DAYS)).strftime("%Y-%m-%d")
    recent_sell = conn.execute(
        "SELECT date FROM paper_orders WHERE ticker=? AND side='SELL' AND date >= ? ORDER BY date DESC LIMIT 1",
        (ticker, cutoff)
    ).fetchone()
    conn.close()

    if recent_sell:
        sell_date = recent_sell["date"]
        days_ago = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(sell_date, "%Y-%m-%d")).days
        return False, f"cooldown: sold {ticker} {days_ago}d ago, wait {SELL_COOLDOWN_DAYS - days_ago}d more"

    return True, "no recent sell"


def check_daily_trade_limit(db_path: str, today: str = None) -> tuple[bool, str]:
    """Limit total trades per day to avoid pattern detection."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE date=?", (today,)
    ).fetchone()[0]
    conn.close()

    if count >= MAX_TRADES_PER_DAY:
        return False, f"daily limit: {count}/{MAX_TRADES_PER_DAY} trades today"

    return True, f"{count}/{MAX_TRADES_PER_DAY} trades today"


def check_daily_turnover(side: str, trade_value: float, portfolio_value: float,
                         db_path: str, today: str = None) -> tuple[bool, str]:
    """Limit daily turnover to MAX_DAILY_TURNOVER_PCT of portfolio."""
    if portfolio_value <= 0:
        return True, "no portfolio"

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Sum all trade values today
    rows = conn.execute(
        "SELECT COALESCE(SUM(quantity * price), 0) as total FROM paper_orders WHERE date=?",
        (today,)
    ).fetchone()
    conn.close()

    today_turnover = float(rows["total"]) + trade_value
    turnover_pct = (today_turnover / portfolio_value) * 100

    if turnover_pct > MAX_DAILY_TURNOVER_PCT:
        return False, f"turnover {turnover_pct:.0f}% > {MAX_DAILY_TURNOVER_PCT}% limit (${today_turnover:,.0f}/${portfolio_value:,.0f})"

    return True, f"turnover {turnover_pct:.0f}%/{MAX_DAILY_TURNOVER_PCT}%"


def check_gap_risk(ticker: str, entry_price: float, current_price: float,
                   stop_pct: float = -20) -> tuple[str, str]:
    """Flag gap risk — if current price already below stop level,
    the stop was gapped through. Real execution would be at market open, not stop price.

    Returns (execution_type, detail):
      "normal" — price above stop, normal execution
      "gap_through" — price gapped through stop, execute at current (worse) price
    """
    stop_price = entry_price * (1 + stop_pct / 100)

    if current_price <= stop_price:
        actual_loss_pct = (current_price - entry_price) / entry_price * 100
        gap_slippage = actual_loss_pct - stop_pct
        return "gap_through", (
            f"gap through stop: target {stop_pct:.0f}% but actual {actual_loss_pct:.1f}% "
            f"(slippage {gap_slippage:.1f}%). Executing at market ${current_price:.2f}"
        )

    return "normal", "price above stop level"


def check_wash_sale(ticker: str, side: str, db_path: str,
                    today: str = None) -> tuple[bool, str]:
    """Warn about wash sale rule: if you sold at a loss within 30 days
    and rebuy, the loss is disallowed for tax purposes.
    This is a WARNING only, not a block — but good to track."""
    if side != "BUY":
        return True, "not a buy"

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=WASH_SALE_DAYS)).strftime("%Y-%m-%d")

    # Check if there was a sell at a loss within 30 days
    recent_sells = conn.execute("""
        SELECT o.date, o.price as sell_price, p.avg_entry_price
        FROM paper_orders o
        JOIN paper_positions p ON o.ticker = p.ticker
        WHERE o.ticker=? AND o.side='SELL' AND o.date >= ?
        AND o.price < p.avg_entry_price
        ORDER BY o.date DESC LIMIT 1
    """, (ticker, cutoff)).fetchone()
    conn.close()

    if recent_sells:
        return True, f"⚠️ WASH SALE: sold {ticker} at loss on {recent_sells['date']}, rebuy within 30d disallows the loss deduction"

    return True, "no wash sale concern"


def validate_trade_full(ticker: str, side: str, account_equity: float,
                        trade_value: float, portfolio_value: float,
                        db_path: str, dt: datetime = None,
                        scheduled_run: bool = False,
                        entry_price: float = 0,
                        current_price: float = 0) -> tuple[bool, list[str]]:
    """Extended validation with Robinhood-safe rules.

    Runs all base rules + cooldown + daily limits + turnover + gap risk + wash sale.
    """
    # Base rules
    allowed, messages = validate_trade(
        ticker, side, account_equity, db_path, dt, scheduled_run)

    if dt is None:
        dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")

    # Cooldown
    cd_ok, cd_reason = check_sell_cooldown(ticker, side, db_path, today)
    if not cd_ok:
        allowed = False
        messages.append(cd_reason)

    # Daily trade limit
    dl_ok, dl_reason = check_daily_trade_limit(db_path, today)
    if not dl_ok:
        allowed = False
        messages.append(dl_reason)

    # Daily turnover
    dt_ok, dt_reason = check_daily_turnover(side, trade_value, portfolio_value, db_path, today)
    if not dt_ok:
        allowed = False
        messages.append(dt_reason)

    # Gap risk (sells only)
    if side == "SELL" and entry_price > 0 and current_price > 0:
        gap_type, gap_detail = check_gap_risk(ticker, entry_price, current_price)
        if gap_type == "gap_through":
            messages.append(f"⚠️ {gap_detail}")

    # Wash sale warning (buys only)
    ws_ok, ws_reason = check_wash_sale(ticker, side, db_path, today)
    if "WASH SALE" in ws_reason:
        messages.append(ws_reason)

    return allowed, messages
