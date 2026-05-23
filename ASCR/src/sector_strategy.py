"""Sector Strategy Engine — per-sector analysis logic, death signals, and parameters.

Each sector has:
1. What drives it (thesis)
2. What signals to watch (bullish + bearish keywords)
3. Death signals (specific events that end the thesis)
4. Risk parameters (stop-loss, conviction sensitivity)
5. Signal type (fundamental vs momentum vs sentiment)
"""
from src.utils import get_logger

logger = get_logger("sector_strategy")


SECTOR_STRATEGIES = {
    "memory_storage": {
        "thesis": "AI training needs massive memory bandwidth; supply constrained",
        "signal_type": "supply_demand",  # Price/supply driven
        "bullish_signals": [
            "memory shortage", "DRAM price increase", "HBM demand",
            "supply constraint", "allocation", "lead time extend",
            "AI training memory", "HBM3E", "HBM4",
        ],
        "death_signals": [
            "new fab completed", "capacity expansion complete", "memory price decline",
            "oversupply", "inventory build", "China DRAM production ramp",
            "CXMT volume production", "price cut", "average selling price decline",
            "fab utilization drop", "capex reduction memory",
        ],
        "stop_loss_pct": -20,
        "trailing_stop_pct": -25,
        "death_signal_action": "immediate_sell",  # Don't wait for rotation
        "sensitivity": "normal",
    },

    "optical": {
        "thesis": "AI data centers need massive optical interconnect; NVIDIA contracts drive demand",
        "signal_type": "contract_driven",  # Big contract announcements
        "bullish_signals": [
            "fiber optic contract", "optical transceiver", "800G", "1.6T",
            "co-packaged optics", "silicon photonics", "NVIDIA optical",
            "data center interconnect", "optical revenue beat",
        ],
        "death_signals": [
            "contract completed", "delivery finished", "new supplier qualified",
            "optical price erosion", "customer diversifying suppliers",
            "800G to 1.6T transition delay", "inventory correction optical",
            "cancellation", "order pushout",
        ],
        "stop_loss_pct": -20,
        "trailing_stop_pct": -25,
        "death_signal_action": "watch_then_sell",  # Monitor, sell if confirmed
        "sensitivity": "normal",
    },

    "compute": {
        "thesis": "AI compute demand exponential; GPU/accelerator shortage",
        "signal_type": "fundamental",
        "bullish_signals": [
            "GPU shortage", "AI accelerator demand", "data center GPU",
            "training cluster", "inference demand", "custom silicon",
            "AI chip revenue", "compute allocation",
        ],
        "death_signals": [
            "GPU oversupply", "compute demand plateau", "hyperscaler capex cut",
            "AI spending slowdown", "alternative architecture",
            "custom chip replacing GPU", "training efficiency breakthrough",
        ],
        "stop_loss_pct": -18,
        "trailing_stop_pct": -22,
        "death_signal_action": "watch_then_sell",
        "sensitivity": "normal",
    },

    "power_cooling": {
        "thesis": "AI data centers consume massive power; cooling is bottleneck",
        "signal_type": "infrastructure",
        "bullish_signals": [
            "data center power", "cooling demand", "liquid cooling",
            "power density", "MW capacity", "nuclear for AI",
            "grid upgrade", "UPS demand", "power conversion",
        ],
        "death_signals": [
            "energy efficiency breakthrough", "power consumption reduction",
            "new cooling technology disruption", "data center power plateau",
            "renewable replacing nuclear", "cooling commoditization",
            "chip efficiency improvement reducing cooling needs",
        ],
        "stop_loss_pct": -22,
        "trailing_stop_pct": -28,  # More tolerance — slower moving
        "death_signal_action": "watch_then_sell",
        "sensitivity": "low",  # Infrastructure is sticky
    },

    "energy_grid": {
        "thesis": "AI power demand straining grid; utilities/nuclear benefiting",
        "signal_type": "infrastructure",
        "bullish_signals": [
            "nuclear restart", "grid expansion", "power purchase agreement",
            "data center utility contract", "grid upgrade",
            "electricity demand AI", "baseload power",
        ],
        "death_signals": [
            "nuclear policy reversal", "PPA cancellation",
            "grid capacity sufficient", "energy storage breakthrough",
            "distributed generation replacing grid",
        ],
        "stop_loss_pct": -22,
        "trailing_stop_pct": -28,
        "death_signal_action": "watch_then_sell",
        "sensitivity": "low",
    },

    "semicap": {
        "thesis": "Wafer fab expansion cycle; equipment makers benefit from capex surge",
        "signal_type": "capex_cycle",
        "bullish_signals": [
            "fab expansion", "wafer fab equipment", "EUV demand",
            "advanced packaging", "CoWoS capacity", "TSMC capex increase",
            "semiconductor equipment order", "backlog increase",
        ],
        "death_signals": [
            "capex guidance cut", "fab expansion delay", "order cancellation",
            "equipment order decline", "utilization rate drop",
            "capex cycle peak", "inventory correction semiconductor",
        ],
        "stop_loss_pct": -20,
        "trailing_stop_pct": -25,
        "death_signal_action": "immediate_sell",  # Capex cycles turn fast
        "sensitivity": "normal",
    },

    "networking": {
        "thesis": "AI clusters need high-bandwidth networking; custom silicon + switches",
        "signal_type": "fundamental",
        "bullish_signals": [
            "AI networking", "InfiniBand", "Ultra Ethernet",
            "network switch demand", "custom ASIC networking",
            "spine-leaf scale-out", "400G 800G switch",
        ],
        "death_signals": [
            "networking demand plateau", "switch inventory build",
            "commodity switch competition", "network disaggregation",
        ],
        "stop_loss_pct": -20,
        "trailing_stop_pct": -25,
        "death_signal_action": "watch_then_sell",
        "sensitivity": "normal",
    },

    "data_center": {
        "thesis": "AI driving massive data center buildout; REITs + builders benefit",
        "signal_type": "infrastructure",
        "bullish_signals": [
            "data center construction", "new campus", "MW commissioned",
            "hyperscaler lease", "data center REIT demand",
            "colocation demand", "edge data center",
        ],
        "death_signals": [
            "data center oversupply", "vacancy rate increase",
            "construction slowdown", "hyperscaler pause buildout",
            "lease rate decline",
        ],
        "stop_loss_pct": -18,
        "trailing_stop_pct": -22,
        "death_signal_action": "watch_then_sell",
        "sensitivity": "low",
    },

    "eda_ip": {
        "thesis": "AI chip design complexity drives EDA tool demand + ARM IP licensing",
        "signal_type": "fundamental",
        "bullish_signals": [
            "chip design complexity", "EDA revenue", "AI chip design",
            "ARM licensing", "custom silicon design", "chiplet",
        ],
        "death_signals": [
            "open-source EDA tools", "RISC-V replacing ARM",
            "chip design consolidation", "EDA pricing pressure",
        ],
        "stop_loss_pct": -18,
        "trailing_stop_pct": -22,
        "death_signal_action": "watch_then_sell",
        "sensitivity": "low",
    },

    # === SENTIMENT-DRIVEN (needs tighter leash) ===
    "new_additions": {
        "thesis": "Speculative AI plays; sentiment-driven, less fundamental backing",
        "signal_type": "sentiment",
        "bullish_signals": [
            "AI hype", "meme", "short squeeze", "retail interest",
            "social media buzz", "Reddit trending",
        ],
        "death_signals": [
            "short report", "SEC investigation", "dilution",
            "earnings miss", "guidance cut", "insider selling spree",
            "hype fading", "Reddit sentiment turning negative",
        ],
        "stop_loss_pct": -15,      # Tighter — sentiment can reverse fast
        "trailing_stop_pct": -20,
        "death_signal_action": "immediate_sell",
        "sensitivity": "high",     # React faster to signals
    },
}

# Emerging sectors to scan for (not yet in universe)
EMERGING_SECTOR_WATCHLIST = {
    "liquid_cooling": {
        "thesis": "GPU power density making air cooling insufficient; liquid/immersion cooling emerging",
        "scan_keywords": [
            "liquid cooling data center", "immersion cooling",
            "direct-to-chip cooling", "coolant distribution unit",
            "rear door heat exchanger", "cooling as a service",
        ],
        "candidate_tickers": ["LIQT", "NOVT", "GNRC"],  # Examples
        "watch_companies": ["CoolIT", "GRC", "Asetek", "Motivair", "ZutaCore"],
    },
    "ai_power_conversion": {
        "thesis": "AI racks need specialized power conversion (48V, GaN/SiC)",
        "scan_keywords": [
            "GaN power", "silicon carbide AI", "48V power distribution",
            "power shelf data center", "bus converter AI",
        ],
        "candidate_tickers": ["WOLF", "MPWR", "PI"],
        "watch_companies": ["Navitas", "EPC", "Delta Electronics"],
    },
    "advanced_packaging": {
        "thesis": "Chiplet + CoWoS + 3D packaging becomes the bottleneck",
        "scan_keywords": [
            "CoWoS capacity", "advanced packaging", "chiplet interconnect",
            "2.5D packaging", "3D stacking", "hybrid bonding",
            "substrate shortage",
        ],
        "candidate_tickers": ["ASX", "AMKR", "ONTO"],
        "watch_companies": ["ASE Group", "Amkor", "JCET"],
    },
    "ai_networking_optics_next_gen": {
        "thesis": "CPO and 1.6T/3.2T optics as next wave",
        "scan_keywords": [
            "co-packaged optics", "3.2T transceiver", "optical engine",
            "linear drive optics", "photonic integrated circuit",
        ],
        "candidate_tickers": ["RNWK", "POET"],
        "watch_companies": ["Ranovus", "POET Technologies", "Ayar Labs"],
    },
    "ai_edge_inference": {
        "thesis": "AI moving to edge devices; specialized inference chips",
        "scan_keywords": [
            "edge AI chip", "on-device inference", "NPU",
            "AI PC", "AI smartphone chip", "inference accelerator edge",
        ],
        "candidate_tickers": ["QCOM", "MCHP"],
        "watch_companies": ["Qualcomm", "MediaTek", "Hailo"],
    },
}


def get_sector_strategy(sector: str) -> dict:
    """Get strategy config for a sector. Falls back to defaults."""
    return SECTOR_STRATEGIES.get(sector, {
        "thesis": "Unknown sector",
        "signal_type": "fundamental",
        "bullish_signals": [],
        "death_signals": [],
        "stop_loss_pct": -20,
        "trailing_stop_pct": -25,
        "death_signal_action": "watch_then_sell",
        "sensitivity": "normal",
    })


def get_stop_levels(sector: str) -> tuple[float, float]:
    """Get (hard_stop_pct, trailing_stop_pct) for a sector."""
    s = get_sector_strategy(sector)
    return s["stop_loss_pct"], s["trailing_stop_pct"]


def get_sensitivity(sector: str) -> str:
    """Get signal sensitivity: high (sentiment) > normal > low (infrastructure)."""
    return get_sector_strategy(sector).get("sensitivity", "normal")


def classify_event_for_sector(headline: str, sector: str) -> dict:
    """Check if a headline contains bullish or death signals for a sector.

    Returns {is_bullish, is_death, matched_signals, severity}
    """
    s = get_sector_strategy(sector)
    headline_lower = headline.lower()

    bullish_matches = [kw for kw in s.get("bullish_signals", []) if kw.lower() in headline_lower]
    death_matches = [kw for kw in s.get("death_signals", []) if kw.lower() in headline_lower]

    return {
        "is_bullish": len(bullish_matches) > 0,
        "is_death": len(death_matches) > 0,
        "bullish_matches": bullish_matches,
        "death_matches": death_matches,
        "severity": "critical" if len(death_matches) >= 2 else "warning" if death_matches else "none",
        "action": s.get("death_signal_action", "watch_then_sell") if death_matches else None,
    }


def scan_emerging_sectors(headlines: list[str]) -> list[dict]:
    """Scan headlines for mentions of emerging sectors not yet in universe.

    Args:
        headlines: list of news headlines/summaries

    Returns list of {sector, matches, headline} for emerging sector hits
    """
    hits = []
    for headline in headlines:
        hl = headline.lower()
        for sector_name, sector_info in EMERGING_SECTOR_WATCHLIST.items():
            matches = [kw for kw in sector_info["scan_keywords"] if kw.lower() in hl]
            if matches:
                hits.append({
                    "sector": sector_name,
                    "thesis": sector_info["thesis"],
                    "matches": matches,
                    "headline": headline,
                    "candidates": sector_info.get("candidate_tickers", []),
                })

    if hits:
        sectors_found = set(h["sector"] for h in hits)
        logger.info(f"Emerging sector signals: {sectors_found}")

    return hits


def get_sector_for_ticker(ticker: str) -> str:
    """Look up which sector a ticker belongs to."""
    from src import config
    universe = config.universe()
    for sname, sval in universe.get("sectors", {}).items():
        if isinstance(sval, dict) and ticker in sval.get("tickers", []):
            return sname
    return "unknown"


def format_telegram_report() -> str:
    """Format sector strategies for Telegram."""
    lines = ["📊 **Sector Strategy Matrix**\n"]

    sensitivity_emoji = {"high": "🔴", "normal": "🟡", "low": "🟢"}

    for sector, s in sorted(SECTOR_STRATEGIES.items()):
        emoji = sensitivity_emoji.get(s["sensitivity"], "⚪")
        lines.append(f"{emoji} **{sector}** ({s['signal_type']})")
        lines.append(f"   Stop: {s['stop_loss_pct']}% hard / {s['trailing_stop_pct']}% trail")
        lines.append(f"   Death: {s['death_signal_action']}")
        if s["death_signals"]:
            lines.append(f"   Watch: {', '.join(s['death_signals'][:3])}")

    lines.append(f"\n🔎 **Emerging Sectors Watched**: {len(EMERGING_SECTOR_WATCHLIST)}")
    for name, info in EMERGING_SECTOR_WATCHLIST.items():
        lines.append(f"   • {name}: {info['thesis']}...")

    return "\n".join(lines)
