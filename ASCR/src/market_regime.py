"""Market regime detector — SPY/QQQ trend analysis.

Regimes:
  risk_on:   SPY above 50 & 200 SMA, QQQ above 50 & 200 SMA
  neutral:   mixed signals
  risk_off:  SPY below both, or QQQ below both

Regime affects ENTRY SIZING only. Never blocks discovery or tracking.
"""
import yfinance as yf
from datetime import datetime
from src.utils import get_logger

logger = get_logger("regime")

REGIME_RISK_ON = "risk_on"
REGIME_NEUTRAL = "neutral"
REGIME_RISK_OFF = "risk_off"

# Sizing multipliers per regime
SIZING_MULTIPLIERS = {
    REGIME_RISK_ON:  {"S": 1.0, "A": 1.0},
    REGIME_NEUTRAL:  {"S": 1.0, "A": 0.5},
    REGIME_RISK_OFF: {"S": 0.5, "A": 0.0},  # A blocked, S halved
}


def _get_vix_signal() -> dict:
    """Get VIX-based regime signal."""
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if hist.empty:
            return {"level": "NORMAL", "vix": 0}
        current_vix = float(hist["Close"].iloc[-1])
        if current_vix > 35:
            return {"level": "DANGER", "vix": current_vix}
        elif current_vix > 25:
            return {"level": "CAUTION", "vix": current_vix}
        elif current_vix > 18:
            return {"level": "WATCH", "vix": current_vix}
        return {"level": "NORMAL", "vix": current_vix}
    except Exception:
        return {"level": "NORMAL", "vix": 0}


def detect_regime() -> dict:
    """Detect current market regime from SPY/QQQ moving averages."""
    try:
        data = yf.download("SPY QQQ", period="1y", interval="1d", progress=False, threads=True)
        if data.empty:
            return {"regime": REGIME_NEUTRAL, "reason": "no data", "details": {}}

        spy_close = data["Close"]["SPY"].dropna()
        qqq_close = data["Close"]["QQQ"].dropna()

        if len(spy_close) < 200 or len(qqq_close) < 200:
            return {"regime": REGIME_NEUTRAL, "reason": "insufficient history", "details": {}}

        spy_now = float(spy_close.iloc[-1])
        spy_sma50 = float(spy_close.iloc[-50:].mean())
        spy_sma200 = float(spy_close.iloc[-200:].mean())

        qqq_now = float(qqq_close.iloc[-1])
        qqq_sma50 = float(qqq_close.iloc[-50:].mean())
        qqq_sma200 = float(qqq_close.iloc[-200:].mean())

        spy_above_50 = spy_now > spy_sma50
        spy_above_200 = spy_now > spy_sma200
        qqq_above_50 = qqq_now > qqq_sma50
        qqq_above_200 = qqq_now > qqq_sma200

        details = {
            "spy": {"price": spy_now, "sma50": spy_sma50, "sma200": spy_sma200,
                     "above_50": spy_above_50, "above_200": spy_above_200},
            "qqq": {"price": qqq_now, "sma50": qqq_sma50, "sma200": qqq_sma200,
                     "above_50": qqq_above_50, "above_200": qqq_above_200},
        }

        # Both fully above → risk on
        if spy_above_50 and spy_above_200 and qqq_above_50 and qqq_above_200:
            regime = REGIME_RISK_ON
            reason = "SPY & QQQ above both 50d & 200d SMA"
        # Either fully below → risk off
        elif (not spy_above_50 and not spy_above_200) or (not qqq_above_50 and not qqq_above_200):
            regime = REGIME_RISK_OFF
            reason = "SPY or QQQ below both 50d & 200d SMA"
        else:
            regime = REGIME_NEUTRAL
            reason = "Mixed signals"

        logger.info(f"Regime: {regime} — {reason}")
        # VIX cross-check
        vix_signal = _get_vix_signal()
        details["vix"] = vix_signal["vix"]
        details["vix_level"] = vix_signal["level"]

        # Take the more conservative regime if VIX disagrees
        vix_override = {"DANGER": REGIME_RISK_OFF, "CAUTION": REGIME_NEUTRAL}
        if vix_signal["level"] in vix_override:
            vix_regime = vix_override[vix_signal["level"]]
            if vix_regime == REGIME_RISK_OFF and regime != REGIME_RISK_OFF:
                reason += f" (VIX override: {vix_signal['vix']:.1f})"
                regime = REGIME_RISK_OFF

        return {"regime": regime, "reason": reason, "details": details}

    except Exception as e:
        logger.error(f"Regime detection failed: {e}")
        return {"regime": REGIME_NEUTRAL, "reason": f"error: {e}", "details": {}}


def get_sizing_multiplier(regime: str, rating: str) -> float:
    """Get position sizing multiplier for given regime and rating."""
    return SIZING_MULTIPLIERS.get(regime, SIZING_MULTIPLIERS[REGIME_NEUTRAL]).get(rating, 0)
