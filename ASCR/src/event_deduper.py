"""Event deduplication and clustering.

Uses rapidfuzz for fuzzy title matching + TF-IDF for semantic grouping.
Each cluster keeps highest-quality or earliest source.
Events with new info (contract value, warrant, guidance) bypass dedup.
"""
import re
from rapidfuzz import fuzz
from src import db
from src.utils import get_logger

logger = get_logger("deduper")

# Keywords that indicate genuinely new incremental information
INCREMENTAL_KEYWORDS = [
    r'\$[\d.]+\s*(billion|million|B|M|bn|mn)',  # new dollar amount
    "warrant", "prepayment", "capacity reservation", "guidance raise",
    "guidance cut", "guidance update", "dilution", "offering", "ATM",
    "convertible", "customer loss", "contract value", "revenue impact",
    "going concern", "material definitive agreement", "amended",
    "extension", "expanded", "additional", "follow-on", "phase 2",
    "phase II", "increased to", "raised to", "upsized",
]

INCREMENTAL_RE = [re.compile(kw, re.IGNORECASE) if kw.startswith(r'\$') else None for kw in INCREMENTAL_KEYWORDS]


def _has_incremental_info(headline: str) -> bool:
    """Check if headline contains keywords suggesting genuinely new info."""
    h = headline.lower()
    for i, kw in enumerate(INCREMENTAL_KEYWORDS):
        if INCREMENTAL_RE[i] is not None:
            if INCREMENTAL_RE[i].search(headline):
                return True
        else:
            if kw.lower() in h:
                return True
    return False


def deduplicate_events(events: list, similarity_threshold: float = 80) -> list:
    """Deduplicate events by fuzzy matching headlines.

    Returns events with added fields:
      - is_duplicate: bool
      - cluster_id: int
      - is_incremental: bool (possible new info despite similarity)
    """
    if not events:
        return []

    clusters = []  # list of lists
    result = []

    for event in events:
        headline = event.get("title", "") or event.get("headline", "")
        ticker = event.get("ticker", "")
        matched_cluster = None

        for ci, cluster in enumerate(clusters):
            # Only match within same ticker
            if cluster[0].get("ticker", "") != ticker:
                continue

            rep_headline = cluster[0].get("title", "") or cluster[0].get("headline", "")
            ratio = fuzz.WRatio(headline, rep_headline)

            if ratio >= similarity_threshold:
                matched_cluster = ci
                break

        is_incremental = _has_incremental_info(headline)

        if matched_cluster is not None:
            clusters[matched_cluster].append(event)
            event["is_duplicate"] = True
            event["cluster_id"] = matched_cluster
            event["is_incremental"] = is_incremental

            if is_incremental:
                event["dedup_action"] = "keep_incremental"
            else:
                event["dedup_action"] = "skip_duplicate"
        else:
            cluster_id = len(clusters)
            clusters.append([event])
            event["is_duplicate"] = False
            event["cluster_id"] = cluster_id
            event["is_incremental"] = False
            event["dedup_action"] = "keep_primary"

        result.append(event)

    # Stats
    primary = sum(1 for e in result if e["dedup_action"] == "keep_primary")
    incremental = sum(1 for e in result if e["dedup_action"] == "keep_incremental")
    skipped = sum(1 for e in result if e["dedup_action"] == "skip_duplicate")
    logger.info(f"Dedup: {len(result)} events → {primary} primary, {incremental} incremental, {skipped} skipped")

    return result


def filter_actionable(events: list) -> list:
    """Return only actionable events (primary + incremental)."""
    return [e for e in events if e.get("dedup_action") != "skip_duplicate"]
