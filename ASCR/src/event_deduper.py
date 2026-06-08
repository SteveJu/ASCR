"""Event deduplication and clustering.

Uses rapidfuzz for fuzzy title matching + TF-IDF for semantic grouping.
Each cluster keeps highest-quality or earliest source.
Events with new info (contract value, warrant, guidance) bypass dedup.
"""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz
from src.utils import get_logger

logger = get_logger("deduper")

SOURCE_SUFFIX_RE = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,100}$")
NON_SIGNAL_WORD_RE = re.compile(r"[^a-z0-9$%.]+")

LOW_VALUE_DUP_SOURCES = {
    "price",
    "leveraged_etf",
    "bubble_detector",
    "calendar",
}

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


def stable_hash(value: str) -> str:
    """Return a stable short content hash."""
    return hashlib.md5((value or "").encode("utf-8")).hexdigest()


def canonical_news_title(title: str) -> str:
    """Normalize a news title so syndicated copies share one identity.

    Google News commonly appends publisher names as ``" - Yahoo Finance"``.
    The exact suffix varies by syndication partner, so raw-title hashing lets
    the same story through repeatedly. Keep numbers and tickers; remove source
    suffixes, punctuation, and casing differences.
    """
    text = (title or "").strip().lower()
    text = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("\u00a0", " ")
    )

    # Strip at most two trailing source-like suffixes, e.g. " - MSN - Reuters".
    for _ in range(2):
        stripped = SOURCE_SUFFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped

    text = NON_SIGNAL_WORD_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(url: str) -> str:
    """Normalize URL enough for exact-source identity checks."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return url.strip()

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        urlencode(query, doseq=True),
        "",
    ))


def article_hash(article: dict) -> str:
    """Content hash for fetched articles.

    For ordinary news, hash the source-neutral canonical title. For structured
    filings, prefer the canonical SEC URL so two filings with similar prose do
    not collapse into one story.
    """
    source = str(article.get("source_query") or article.get("source") or "").lower()
    title = article.get("title") or article.get("headline") or ""
    url = normalize_url(article.get("url", ""))

    if source.startswith("sec") and url:
        seed = f"{source}:{url}"
    elif source in {"filing", "sec_edgar"} and url:
        seed = f"{source}:{url}"
    else:
        seed = canonical_news_title(title) or url
    return stable_hash(seed)


def enrich_article_identity(article: dict) -> dict:
    """Return a copy with canonical title and source-neutral hash fields."""
    enriched = dict(article)
    canonical = canonical_news_title(enriched.get("title") or enriched.get("headline") or "")
    enriched["_canonical_title"] = canonical
    enriched["_hash"] = article_hash(enriched)
    return enriched


def _fact_tokens(headline: str) -> set:
    h = (headline or "").lower()
    facts = set()
    facts.update(re.findall(r"\$ ?\d+(?:\.\d+)?\s*(?:billion|million|bn|mn|m|b)", h))
    facts.update(re.findall(r"\b\d+(?:\.\d+)?%", h))
    facts.update(re.findall(r"\bq[1-4]\b", h))
    facts.update(re.findall(r"\bfy ?20\d{2}\b|\b20\d{2}\b", h))
    facts.update(re.findall(r"\bphase ?(?:i{1,3}|iv|[1-4])\b", h))
    for kw in INCREMENTAL_KEYWORDS:
        if kw.startswith(r"\$"):
            continue
        if kw.lower() in h:
            facts.add(kw.lower())
    return facts


def _has_new_incremental_info(headline: str, representative: str) -> bool:
    """True when a similar headline appears to add facts not in the representative."""
    if not _has_incremental_info(headline):
        return False
    new_facts = _fact_tokens(headline)
    old_facts = _fact_tokens(representative)
    return bool(new_facts - old_facts)


def deduplicate_articles(articles: list, similarity_threshold: float = 92) -> list:
    """Deduplicate fetched articles before filtering or LLM analysis."""
    kept = []
    seen_hashes = set()

    for article in articles or []:
        enriched = enrich_article_identity(article)
        title = enriched.get("title") or enriched.get("headline") or ""
        canonical = enriched.get("_canonical_title", "")
        if not canonical and not enriched.get("url"):
            continue

        article_id = enriched["_hash"]
        if article_id in seen_hashes:
            continue

        duplicate = False
        for existing in kept:
            existing_title = existing.get("_canonical_title", "")
            if not canonical or not existing_title:
                continue
            if canonical == existing_title:
                duplicate = True
                break
            if fuzz.WRatio(canonical, existing_title) >= similarity_threshold:
                if not _has_new_incremental_info(title, existing.get("title") or existing.get("headline") or ""):
                    duplicate = True
                    break

        if duplicate:
            continue

        seen_hashes.add(article_id)
        kept.append(enriched)

    skipped = len(articles or []) - len(kept)
    if skipped:
        logger.info(f"Article dedup: {len(articles or [])} articles -> {len(kept)} kept, {skipped} skipped")
    return kept


def find_duplicate_article(conn, article: dict, similarity_threshold: float = 94) -> dict | None:
    """Find an existing DB event that represents the same news article/story."""
    enriched = enrich_article_identity(article)
    title = enriched.get("title") or enriched.get("headline") or ""
    canonical = enriched.get("_canonical_title", "")
    if not canonical and not enriched.get("url"):
        return None

    legacy_hash = stable_hash(title)
    current_hash = enriched["_hash"]
    row = conn.execute(
        "SELECT id, date, ticker, headline, title, source, hash FROM events "
        "WHERE hash IN (?, ?) LIMIT 1",
        (current_hash, legacy_hash),
    ).fetchone()
    if row:
        return {"reason": "hash", "event": dict(row)}

    rows = conn.execute("""
        SELECT id, date, ticker, headline, title, source, hash
        FROM events
        WHERE COALESCE(headline, title, '') != ''
        ORDER BY id DESC
        LIMIT 3000
    """).fetchall()

    for existing in rows:
        existing = dict(existing)
        if str(existing.get("source") or "").lower() in LOW_VALUE_DUP_SOURCES:
            continue
        existing_title = existing.get("headline") or existing.get("title") or ""
        existing_canonical = canonical_news_title(existing_title)
        if not existing_canonical:
            continue
        if canonical == existing_canonical:
            return {"reason": "canonical_title", "event": existing}
        if fuzz.WRatio(canonical, existing_canonical) >= similarity_threshold:
            if not _has_new_incremental_info(title, existing_title):
                return {"reason": "similar_title", "event": existing}

    return None


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
