"""Leveraged ETF Monitor.

Two signals:
1. New leveraged ETF launches targeting universe tickers → event signal
2. AUM tracking for existing leveraged ETFs → bubble signal

Data source: yfinance (free, no API key needed)
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

from src import config, db
from src.utils import get_logger

logger = get_logger("leveraged_etf")

# Known single-stock leveraged ETFs mapped to underlying
# Updated: 2026-05-13
KNOWN_ETFS = {
    "NVDA": [
        {"ticker": "NVDL", "name": "GraniteShares 2x Long NVDA", "leverage": 2.0, "direction": "bull"},
        {"ticker": "NVDU", "name": "Direxion Daily NVDA Bull 2X", "leverage": 2.0, "direction": "bull"},
        {"ticker": "NVDD", "name": "Direxion Daily NVDA Bear 1X", "leverage": -1.0, "direction": "bear"},
        {"ticker": "NVDX", "name": "T-Rex 2X Long NVIDIA", "leverage": 2.0, "direction": "bull"},
        {"ticker": "NVD", "name": "GraniteShares 2x Short NVDA", "leverage": -2.0, "direction": "bear"},
    ],
    "AMD": [
        {"ticker": "AMDL", "name": "GraniteShares 2x Long AMD", "leverage": 2.0, "direction": "bull"},
        {"ticker": "AMDU", "name": "Defiance 2X Long AMD", "leverage": 2.0, "direction": "bull"},
        {"ticker": "AMDD", "name": "Direxion Daily AMD Bear 1X", "leverage": -1.0, "direction": "bear"},
    ],
    "TSM": [
        {"ticker": "TSMX", "name": "Direxion Daily TSM Bull 2X", "leverage": 2.0, "direction": "bull"},
    ],
    "AVGO": [
        {"ticker": "AVGX", "name": "Defiance 2X Long AVGO", "leverage": 2.0, "direction": "bull"},
    ],
    "SMCI": [
        {"ticker": "SMCX", "name": "Defiance 2X Long SMCI", "leverage": 2.0, "direction": "bull"},
    ],
}

# ETF ticker patterns to scan for new launches (common prefixes/suffixes)
# Format: underlying_ticker → list of candidate ETF tickers to check
SCAN_SUFFIXES = ["L", "X", "U", "D", "S", "2L", "2S"]
SCAN_PREFIXES = ["2X", "3X"]

DATA_DIR = Path(__file__).parent.parent / "data"
ETF_STATE_FILE = DATA_DIR / "leveraged_etf_state.json"


def _load_state() -> dict:
    """Load persisted ETF tracking state."""
    if ETF_STATE_FILE.exists():
        return json.loads(ETF_STATE_FILE.read_text())
    return {"known_etfs": {}, "aum_history": {}, "last_scan": None}


def _save_state(state: dict):
    """Persist ETF tracking state."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ETF_STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _get_etf_info(ticker: str) -> dict | None:
    """Fetch ETF info from yfinance. Returns None if not found or not an ETF."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        aum = info.get("totalAssets", 0) or 0
        if aum <= 0:
            return None
        return {
            "ticker": ticker,
            "name": info.get("shortName", info.get("longName", "")),
            "aum": aum,
            "price": info.get("regularMarketPrice", info.get("previousClose", 0)),
            "inception": info.get("fundInceptionDate", ""),
        }
    except Exception:
        return None


def scan_new_etfs(universe: list = None) -> list:
    """Scan for newly launched leveraged ETFs targeting universe tickers.

    Returns list of {underlying, etf_ticker, name, aum, ...} for NEW finds.
    """
    if universe is None:
        universe = config.all_tickers()

    state = _load_state()
    known = set()
    for etfs in KNOWN_ETFS.values():
        for e in etfs:
            known.add(e["ticker"])
    for etfs in state.get("known_etfs", {}).values():
        for e in etfs:
            known.add(e["ticker"])

    new_finds = []

    # Only scan mid/small cap tickers that DON'T already have leveraged ETFs
    # (Big caps like NVDA/AMD already have them — not interesting)
    tickers_with_etfs = set(KNOWN_ETFS.keys()) | set(state.get("known_etfs", {}).keys())
    scan_targets = [t for t in universe if t not in tickers_with_etfs]

    for underlying in scan_targets:
        candidates = []
        for suffix in SCAN_SUFFIXES:
            candidates.append(underlying[:3] + suffix)  # e.g., MRV + L = MRVL (collision!)
            candidates.append(underlying + suffix)       # e.g., MRVL + X = MRVLX

        # Filter out the underlying itself and known tickers
        candidates = [c for c in set(candidates) if c != underlying and c not in known and c not in universe]

        for cand in candidates:
            info = _get_etf_info(cand)
            if info and info["aum"] > 1_000_000:  # >$1M AUM = real ETF
                name_lower = info["name"].lower()
                # Verify it's actually a leveraged/inverse ETF for this underlying
                underlying_lower = underlying.lower()
                # Strict check: ETF name must reference the underlying ticker or full company name
                is_match = underlying_lower in name_lower
                if not is_match:
                    # Also accept abbreviated ticker in name (e.g., "MRVL" in "2x Long MRVL")
                    is_match = underlying.upper() in info["name"].upper()
                if not is_match:
                    continue  # Skip — likely a different ETF that happens to have a similar ticker
                # Must also be a leveraged/inverse product
                if not any(w in name_lower for w in ["2x", "3x", "bull", "bear", "long", "short", "lever", "daily"]):
                    continue
                new_finds.append({
                    "underlying": underlying,
                    "etf_ticker": cand,
                    "name": info["name"],
                    "aum": info["aum"],
                    "price": info["price"],
                })
                logger.info(f"NEW leveraged ETF: {cand} for {underlying} (AUM: ${info['aum']/1e6:.0f}M)")

    # Persist new finds
    if new_finds:
        for f in new_finds:
            u = f["underlying"]
            if u not in state.get("known_etfs", {}):
                state.setdefault("known_etfs", {})[u] = []
            state["known_etfs"][u].append({
                "ticker": f["etf_ticker"],
                "name": f["name"],
                "discovered": datetime.now().isoformat(),
            })

    state["last_scan"] = datetime.now().isoformat()
    _save_state(state)

    return new_finds


def track_aum() -> dict:
    """Track AUM changes for all known leveraged ETFs.

    Returns {underlying: {total_aum, aum_change_30d_pct, bull_aum, bear_aum, etfs: [...]}}
    """
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    results = {}

    all_etfs = dict(KNOWN_ETFS)
    for u, etfs in state.get("known_etfs", {}).items():
        if u not in all_etfs:
            all_etfs[u] = []
        existing_tickers = {e["ticker"] for e in all_etfs[u]}
        for e in etfs:
            if e["ticker"] not in existing_tickers:
                all_etfs[u].append({"ticker": e["ticker"], "name": e.get("name", ""),
                                     "leverage": 2.0, "direction": "bull"})

    aum_history = state.get("aum_history", {})

    for underlying, etfs in all_etfs.items():
        total_aum = 0
        bull_aum = 0
        bear_aum = 0
        etf_details = []

        for etf in etfs:
            info = _get_etf_info(etf["ticker"])
            if not info:
                continue

            aum = info["aum"]
            total_aum += aum
            direction = etf.get("direction", "bull")
            if direction == "bull":
                bull_aum += aum
            else:
                bear_aum += aum

            etf_details.append({
                "ticker": etf["ticker"],
                "name": info["name"],
                "aum": aum,
                "direction": direction,
            })

            # Record AUM history
            key = etf["ticker"]
            if key not in aum_history:
                aum_history[key] = []
            aum_history[key].append({"date": today, "aum": aum})
            # Keep 90 days
            aum_history[key] = aum_history[key][-90:]

        if total_aum > 0:
            # Calculate 30d AUM change
            aum_30d_ago = 0
            for etf in etfs:
                hist = aum_history.get(etf["ticker"], [])
                old = [h for h in hist if h["date"] <= (datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d")]
                if old:
                    aum_30d_ago += old[-1]["aum"]

            aum_change_pct = ((total_aum - aum_30d_ago) / aum_30d_ago * 100) if aum_30d_ago > 0 else 0

            results[underlying] = {
                "total_aum": total_aum,
                "bull_aum": bull_aum,
                "bear_aum": bear_aum,
                "bull_bear_ratio": bull_aum / bear_aum if bear_aum > 0 else float("inf"),
                "aum_change_30d_pct": round(aum_change_pct, 1),
                "etf_count": len(etf_details),
                "etfs": etf_details,
            }

    state["aum_history"] = aum_history
    _save_state(state)

    return results


def get_bubble_signals() -> list:
    """Analyze leveraged ETF data for bubble signals.

    Returns list of {ticker, signal, severity, detail} warnings.
    """
    aum_data = track_aum()
    signals = []

    for underlying, data in aum_data.items():
        # Signal 1: AUM growth > 50% in 30d → overheating
        if data["aum_change_30d_pct"] > 50:
            signals.append({
                "ticker": underlying,
                "signal": "leveraged_aum_surge",
                "severity": "WARNING",
                "detail": f"{underlying} leveraged ETF AUM +{data['aum_change_30d_pct']:.0f}% in 30d (${data['total_aum']/1e6:.0f}M total)",
            })

        # Signal 2: AUM growth > 100% in 30d → danger
        if data["aum_change_30d_pct"] > 100:
            signals[-1]["severity"] = "DANGER"

        # Signal 3: Bull/Bear ratio extremely skewed → crowded long
        if data["bull_bear_ratio"] > 20 and data["total_aum"] > 500_000_000:
            signals.append({
                "ticker": underlying,
                "signal": "leveraged_crowded_long",
                "severity": "WARNING",
                "detail": f"{underlying} bull/bear ETF ratio: {data['bull_bear_ratio']:.0f}:1 (${data['bull_aum']/1e6:.0f}M bull vs ${data['bear_aum']/1e6:.0f}M bear)",
            })

        # Signal 4: New leveraged ETF on mid-cap → AI trade expanding to second tier
        # (This is tracked via scan_new_etfs, not here)

    if signals:
        logger.info(f"Leveraged ETF bubble signals: {len(signals)}")

    return signals


def generate_events(universe: list = None) -> list:
    """Main entry: scan for new ETFs + bubble signals → event dicts for event_pipeline.

    Called during pipeline runs (weekly frequency recommended).
    """
    if universe is None:
        universe = config.all_tickers()

    events = []

    # 1. New ETF launches
    new_etfs = scan_new_etfs(universe)
    for ne in new_etfs:
        events.append({
            "ticker": ne["underlying"],
            "event_type": "leveraged_etf_launch",
            "headline": f"New leveraged ETF launched: {ne['etf_ticker']} ({ne['name']}) for {ne['underlying']}",
            "source": "leveraged_etf_monitor",
            "evidence_delta": 3,  # Moderate signal, lagging indicator
            "confidence": 0.6,
            "summary": f"Market created leveraged ETF {ne['etf_ticker']} targeting {ne['underlying']} with ${ne['aum']/1e6:.0f}M AUM. "
                       f"Indicates rising retail/institutional speculation interest.",
            "is_ai_related": 1,
        })

    # 2. Bubble signals
    bubble_signals = get_bubble_signals()
    for bs in bubble_signals:
        ev_delta = -3 if bs["severity"] == "WARNING" else -5
        events.append({
            "ticker": bs["ticker"],
            "event_type": "leveraged_etf_bubble",
            "headline": f"[BUBBLE] {bs['detail']}",
            "source": "leveraged_etf_monitor",
            "evidence_delta": ev_delta,  # Negative — overheating is bearish
            "confidence": 0.7,
            "summary": bs["detail"],
            "is_ai_related": 1,
        })

    return events


def format_telegram_report() -> str:
    """Format a Telegram-friendly report of leveraged ETF status."""
    aum_data = track_aum()
    if not aum_data:
        return "📊 No leveraged ETFs tracked in universe."

    lines = ["📊 **Leveraged ETF Monitor**\n"]

    # Sort by total AUM
    for underlying, data in sorted(aum_data.items(), key=lambda x: -x[1]["total_aum"]):
        aum_m = data["total_aum"] / 1e6
        change = data["aum_change_30d_pct"]
        change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"

        # Warning emoji
        emoji = "🔴" if change > 100 else "🟡" if change > 50 else "⚪"

        lines.append(f"{emoji} **{underlying}**: ${aum_m:.0f}M ({change_str} 30d)")
        lines.append(f"   {data['etf_count']} ETFs | Bull/Bear: {data['bull_bear_ratio']:.0f}:1")

    # Universe tickers without leveraged ETFs
    all_tickers = set(config.all_tickers())
    covered = set(aum_data.keys())
    uncovered = sorted(all_tickers - covered)
    lines.append(f"\n🟢 {len(uncovered)} tickers with no leveraged ETFs (healthy)")

    bubble = get_bubble_signals()
    if bubble:
        lines.append(f"\n⚠️ **Active Signals**:")
        for b in bubble:
            lines.append(f"  {b['severity']}: {b['detail']}")

    return "\n".join(lines)
