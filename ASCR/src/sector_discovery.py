"""Sector Discovery — detect emerging frontier sectors via anomaly detection.

Philosophy:
  We can't predict what the next hot sector is (we didn't know about optical before it happened).
  But we CAN detect anomalies: companies that suddenly appear in AI context when they never did before.

How it works:
  1. Collect ALL company names mentioned in frontier technology news (not just our universe)
  2. Track mention frequency over time in `mention_tracker` table
  3. Detect anomalies: a company that went from 0 mentions -> 3+ mentions in a week
  4. Weekly: send anomaly list to LLM to classify:
     "Is this a real new investable bottleneck node, or just noise?"
  5. Report to human for decision

NOT automated universe addition — human decides.
"""
import os
import re
import json
import time
import hashlib
import sqlite3
import feedparser
from datetime import datetime, timedelta
from urllib.parse import quote

from src import config, db
from src.utils import get_logger

logger = get_logger("sector_discovery")

# Broad queries designed to catch companies we DON'T already know about
DISCOVERY_QUERIES = [
    # Generic AI infra (catches new entrants)
    "AI infrastructure company contract 2026",
    "AI supply chain new supplier partnership",
    "data center supplier deal billion 2026",
    "AI hardware startup funding round",
    # Physical infrastructure (catches cooling, power, materials)
    "data center cooling company contract",
    "data center power supply company",
    "AI server component supplier",
    "data center construction materials company",
    # Next-gen technology (catches what we don't know yet)
    "new technology AI data center breakthrough",
    "AI chip packaging company",
    "photonic computing company",
    "quantum computing supply chain",
    "AI networking new technology company",
    # Bottleneck signals (new bottleneck = new sector)
    "AI bottleneck shortage constraint 2026",
    "data center bottleneck supply constraint",
    "semiconductor supply chain bottleneck new",
    # Earnings mentions (companies benefiting from AI unexpectedly)
    "earnings beat AI demand unexpected revenue",
    "revenue surprise data center AI segment",
]


def _frontier_query_specs() -> list[dict]:
    """Load domain-general frontier discovery queries.

    If the config is missing or invalid, the legacy AI supply-chain queries still
    run so discovery does not fail closed.
    """
    try:
        from src.frontier_domains import discovery_query_specs
        return discovery_query_specs()
    except Exception as exc:
        logger.warning(f"Frontier domain query load failed: {exc}")
        return []


def _discovery_query_specs() -> list[dict]:
    """Return query specs used by mention scanning."""
    specs = [{"query": query, "domain_id": "legacy_ai_supply_chain", "domain_name": "Legacy AI supply chain"}
             for query in DISCOVERY_QUERIES]
    seen = {spec["query"].lower() for spec in specs}
    for spec in _frontier_query_specs():
        query = spec.get("query", "")
        if query and query.lower() not in seen:
            specs.append(spec)
            seen.add(query.lower())
    return specs


def _frontier_prompt_context() -> str:
    try:
        from src.frontier_domains import prompt_context
        return prompt_context()
    except Exception as exc:
        logger.warning(f"Frontier prompt context load failed: {exc}")
        return "- ai_infrastructure: AI infrastructure supply-chain bottlenecks."


def _get_known_names() -> set:
    """Build a set of known company names + tickers to skip."""
    known = set(config.all_tickers())
    name_map = {
        "NVDA": ["NVIDIA", "Jensen Huang"],
        "AMD": ["AMD", "Lisa Su"],
        "INTC": ["Intel"],
        "MU": ["Micron"],
        "AVGO": ["Broadcom"],
        "MRVL": ["Marvell"],
        "TSM": ["TSMC", "Taiwan Semiconductor"],
        "ASML": ["ASML"],
        "AMAT": ["Applied Materials"],
        "LRCX": ["Lam Research"],
        "KLAC": ["KLA"],
        "COHR": ["Coherent"],
        "LITE": ["Lumentum"],
        "GLW": ["Corning"],
        "CIEN": ["Ciena"],
        "VRT": ["Vertiv"],
        "ETN": ["Eaton"],
        "CEG": ["Constellation Energy"],
        "ANET": ["Arista"],
        "CSCO": ["Cisco"],
        "ARM": ["ARM", "Arm Holdings"],
        "CDNS": ["Cadence"],
        "SNPS": ["Synopsys"],
        "SMCI": ["Super Micro", "Supermicro"],
        "DELL": ["Dell"],
        "QCOM": ["Qualcomm"],
        "WDC": ["Western Digital"],
        "STX": ["Seagate"],
        "CRDO": ["Credo"],
        "DLR": ["Digital Realty"],
        "EQIX": ["Equinix"],
    }
    for ticker, names in name_map.items():
        known.update(n.upper() for n in names)
        known.add(ticker)
    known.update(["GOOGL", "GOOG", "GOOGLE", "ALPHABET", "AMZN", "AMAZON",
                  "MSFT", "MICROSOFT", "META", "FACEBOOK", "AAPL", "APPLE",
                  "TSLA", "TESLA", "ORCL", "ORACLE", "IBM", "OPENAI",
                  "ANTHROPIC", "DEEPSEEK", "CHATGPT"])
    return known


def init_discovery_db():
    """Create mention tracking tables."""
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mention_tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TEXT NOT NULL,
                company_name TEXT NOT NULL,
                ticker_guess TEXT,
                headline TEXT,
                source_query TEXT,
                context TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_date TEXT NOT NULL,
                company_name TEXT NOT NULL,
                ticker_guess TEXT,
                mention_count INTEGER,
                first_seen TEXT,
                headlines TEXT,
                llm_assessment TEXT,
                is_real_signal INTEGER DEFAULT 0,
                domain_guess TEXT,
                sector_guess TEXT,
                thesis TEXT,
                status TEXT DEFAULT 'new',
                human_decision TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        mention_columns = {row[1] for row in conn.execute("PRAGMA table_info(mention_tracker)").fetchall()}
        if "context" not in mention_columns:
            conn.execute("ALTER TABLE mention_tracker ADD COLUMN context TEXT")

        anomaly_columns = {row[1] for row in conn.execute("PRAGMA table_info(discovery_anomalies)").fetchall()}
        if "domain_guess" not in anomaly_columns:
            conn.execute("ALTER TABLE discovery_anomalies ADD COLUMN domain_guess TEXT")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_mention_company ON mention_tracker(company_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mention_date ON mention_tracker(scan_date)")


def _extract_company_names(headline: str) -> list[str]:
    """Extract potential company names from a headline."""
    names = []

    # "$TICK" or "(TICK)"
    tickers = re.findall(r'\$([A-Z]{2,5})\b', headline)
    tickers += re.findall(r'\(([A-Z]{2,5})\)', headline)
    names.extend(tickers)

    # "Company Inc/Corp/Ltd/Technologies/Systems/Semiconductor"
    corp_pattern = re.findall(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:Inc|Corp|Ltd|Technologies|Systems|'
        r'Semiconductor|Holdings|Group|Solutions|Energy|Power|Electronics|Devices|Materials)',
        headline
    )
    names.extend(corp_pattern)

    COMMON_WORDS = {
        # Grammar / filler
        "The", "This", "That", "With", "From", "Into", "Over", "Under", "After",
        "Before", "About", "Their", "Could", "Would", "Should", "Where", "Which",
        "While", "Since", "Until", "What", "When", "Will", "Have", "Been", "More",
        "Than", "Most", "Some", "Each", "Just", "Also", "Only", "Very", "Much",
        "Such", "Both", "Even", "Same", "Back", "Down", "Last", "Next", "Year",
        "Says", "Said", "New", "May", "Can", "How", "Why", "Big", "Top", "All",
        "Now", "But", "Not", "Our", "Its", "His", "Her", "Are", "Was", "Were",
        "Has", "Had", "Does", "Did", "Being", "Get", "Got", "Make", "Made",
        "Take", "Took", "Come", "Came", "Give", "Gave", "Look", "See", "Saw",
        "Use", "Used", "Way", "Set", "Put", "Run", "Own", "Far", "Yet",
        "Despite", "Among", "Between", "Through", "Against", "During",
        "According", "Already", "Another", "Because", "Become", "Every",
        "First", "Second", "Third", "Other", "Still", "Right", "Going",
        # Business / finance generic
        "CEO", "CFO", "CTO", "COO", "IPO", "SEC", "NYSE", "GDP", "ETF", "EPS",
        "Market", "Stock", "Stocks", "Share", "Shares", "Price", "Trade",
        "Trading", "Buy", "Sell", "Earnings", "Revenue", "Revenues", "Profit",
        "Loss", "Growth", "Demand", "Supply", "Report", "Quarter", "Quarterly",
        "Annual", "Billion", "Million", "Trillion", "Company", "Companies",
        "Global", "World", "Business", "Industry", "Sector", "Forecast",
        "Agreement", "Announces", "Announced", "Announcing", "Partners",
        "Partnership", "Deal", "Deals", "Contract", "Contracts", "Investment",
        "Investments", "Investor", "Investors", "Funding", "Fund", "Funds",
        "Capital", "Financial", "Securities", "Advisory", "Consulting",
        "Management", "Venture", "Portfolio", "Assets", "Debt", "Equity",
        "Facility", "Raises", "Raised", "Raising", "Signs", "Signed",
        "Agreement", "Results", "Outlook", "Guidance", "Beat", "Miss",
        "Surpass", "Exceeds", "Falls", "Rising", "Falling", "Soars",
        # Tech generic
        "Data", "Center", "Centers", "Cloud", "Computing", "Software",
        "Hardware", "Technology", "Technologies", "Digital", "Network",
        "Networking", "Service", "Services", "Platform", "Solution",
        "Solutions", "Innovation", "System", "Systems", "Device", "Devices",
        "Chip", "Chips", "Silicon", "Server", "Servers", "Storage",
        "Memory", "Optical", "Power", "Energy", "Semiconductor",
        "Infrastructure", "Advanced", "Packaging", "Artificial", "Intelligence",
        "Machine", "Learning", "Research", "Development", "Analytics",
        "Cooling", "Quantum", "Photonic", "Bottleneck", "Shortage",
        "Capacity", "Production", "Manufacturing", "Assembly", "Chain",
        # Media / source
        "Wall", "Street", "Reuters", "Bloomberg", "CNBC", "Yahoo",
        "Finance", "Motley", "Fool", "TradingView", "MarketWatch",
        "Barrons", "Seeking", "Alpha", "Insider", "Wire", "News",
        "Source", "Sources", "Press", "Release", "Update", "Breaking",
        "Exclusive", "Overview", "Analysis", "Review", "Latest",
        "Growing", "Pipeline", "Despite", "Tools", "Examples",
        # Days/months
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday", "January", "February", "March", "April",
        "June", "July", "August", "September", "October", "November",
        "December",
        # Strategy terms (not companies)
        "Strategy", "Strategic", "Operations", "Operating", "Performance",
        "Competitive", "Advantage", "Leader", "Leading", "Largest",
        # Action verbs that look like names
        "Secures", "Secured", "Targets", "Target", "Drives", "Driving",
        "Launches", "Launch", "Expands", "Expanding", "Builds", "Building",
        "Announces", "Reports", "Reporting", "Reaches", "Reaching",
        "Accelerate", "Accelerates", "Powering", "Powering", "Enabling",
        "Mitigate", "Collaboration", "Strengthens", "Provider", "Providers",
        "Supplier", "Suppliers", "Customer", "Customers", "Buyer", "Buyers",
        "Startup", "Startups", "Partner", "Crowns", "Foundational",
        # Media / publication names
        "Magazine", "Journal", "Times", "Post", "Herald", "Gazette",
        "Tribune", "Observer", "Chronicle", "Dynamics", "Insider",
        "Crunchbase", "TechCrunch", "Wired", "Verge", "Axios",
        "Protocol", "Information", "Register", "AIMultiple",
        # Misc
        "Record", "Risk", "Help", "Retailers", "Oracle",
        "Faster", "Guidance", "Play", "Review",
        # Publication fragments that appear as multi-word
        "Wall St", "Stock Titan", "Fast Company", "Tech Buzz",
        "News Money", "Connect News", "Lake Tahoe",
        "Big Tech", "MIT Technology", "Photonics Spectra",
        "New American", "Says Trend",
    }

    # Only keep multi-word capitalized phrases as potential company names
    multi_word = re.findall(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', headline)

    # Also check whole phrases against skip list
    SKIP_PHRASES = {
        "Wall St", "Stock Titan", "Fast Company", "Tech Buzz",
        "News Money", "Connect News", "Big Tech", "MIT Technology",
        "Photonics Spectra", "New American", "Says Trend",
        "Earnings Beat", "Earnings Beat Estimates", "Earnings Miss",
        "Data Center", "Data Centers", "Breakthrough Technologies",
        "Lake Tahoe", "Google Signs", "Price Target",
    }

    for phrase in multi_word:
        if phrase in SKIP_PHRASES:
            continue
        words_in_phrase = phrase.split()
        # Skip if all words are common
        non_common = [w for w in words_in_phrase if w not in COMMON_WORDS]
        if non_common:
            names.append(phrase)

    return list(set(names))


def scan_for_new_mentions() -> dict:
    """Scan news and record all non-universe company mentions.
    Returns {company: [headlines]}
    """
    init_discovery_db()
    today = datetime.now().strftime("%Y-%m-%d")
    known = _get_known_names()
    mentions = {}
    seen_titles = set()
    query_specs = _discovery_query_specs()

    for spec in query_specs:
        query = spec["query"]
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)

            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                title_key = title.lower()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

                names = _extract_company_names(title)
                for name in names:
                    name_upper = name.upper()
                    if name_upper in known or len(name) < 3:
                        continue
                    if name not in mentions:
                        mentions[name] = []
                    mentions[name].append({
                        "headline": title,
                        "query": query,
                        "domain_id": spec.get("domain_id", ""),
                        "domain_name": spec.get("domain_name", ""),
                    })

            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"Discovery scan '{query}': {e}")

    with db.get_conn() as conn:
        for company, headlines in mentions.items():
            for h in headlines:
                conn.execute(
                    "INSERT INTO mention_tracker (scan_date, company_name, headline, source_query, context) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        today,
                        company,
                        h["headline"],
                        h["query"],
                        json.dumps({
                            "domain_id": h.get("domain_id", ""),
                            "domain_name": h.get("domain_name", ""),
                        }),
                    )
                )
        conn.commit()

    logger.info(
        f"Scanned {len(seen_titles)} headlines across {len(query_specs)} queries, "
        f"found {len(mentions)} non-universe companies"
    )
    return mentions


def detect_anomalies(window_days: int = 7, min_mentions: int = 3) -> list[dict]:
    """Detect companies with anomalous mention frequency.
    Anomaly = mentioned min_mentions+ times in last window_days,
    but 0-1 times in the 30 days before that.
    """
    init_discovery_db()

    today = datetime.now()
    recent_start = (today - timedelta(days=window_days)).strftime("%Y-%m-%d")
    baseline_start = (today - timedelta(days=window_days + 30)).strftime("%Y-%m-%d")

    with db.get_conn() as conn:
        recent = conn.execute("""
            SELECT company_name, COUNT(*) as cnt,
                   MIN(scan_date) as first_seen
            FROM mention_tracker
            WHERE scan_date >= ?
            GROUP BY company_name
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, (recent_start, min_mentions)).fetchall()

        anomalies = []
        for r in recent:
            company = r["company_name"]
            baseline = conn.execute("""
                SELECT COUNT(*) as cnt FROM mention_tracker
                WHERE company_name = ? AND scan_date >= ? AND scan_date < ?
            """, (company, baseline_start, recent_start)).fetchone()["cnt"]

            if baseline <= 1:
                existing = conn.execute(
                    "SELECT id FROM discovery_anomalies WHERE company_name = ? AND status != 'dismissed'",
                    (company,)
                ).fetchone()

                if not existing:
                    headline_rows = conn.execute("""
                        SELECT DISTINCT headline
                        FROM mention_tracker
                        WHERE company_name = ? AND scan_date >= ?
                        ORDER BY headline
                        LIMIT 5
                    """, (company, recent_start)).fetchall()
                    headlines = [row["headline"] for row in headline_rows]
                    anomalies.append({
                        "company": company,
                        "recent_mentions": r["cnt"],
                        "baseline_mentions": baseline,
                        "first_seen": r["first_seen"],
                        "headlines": headlines,
                    })

    logger.info(f"Anomaly detection: {len(anomalies)} new anomalies")
    return anomalies


def build_anomaly_assessment_prompt(anomalies: list[dict]) -> str:
    """Build a domain-general anomaly assessment prompt."""
    companies_text = ""
    for i, a in enumerate(anomalies[:10]):
        headlines = "\n    ".join(a["headlines"][:3])
        companies_text += f"""
  {i+1}. **{a['company']}** (mentioned {a['recent_mentions']}x in last 7 days, {a['baseline_mentions']}x in prior 30 days)
    Headlines:
    {headlines}
"""

    domain_context = _frontier_prompt_context()

    return f"""You are a frontier technology and hard-tech investment analyst. Below are companies that SUDDENLY started appearing in frontier-domain news after being absent. Your job is NOT to predict the future. Classify whether each company has a REAL, SPECIFIC connection to an investable frontier bottleneck, or whether it is just noise.

Configured frontier domains:
{domain_context}

For each company, answer:
1. Is this a real new frontier bottleneck node? (yes/no/unclear)
2. If yes: what configured domain best fits? Use one of the domain ids above when possible.
3. If yes: what sector or bottleneck? (e.g., "humanoid actuators", "space propulsion", "quantum cryogenics", "power conversion")
4. If yes: what's the thesis? (one sentence)
5. What's the ticker? (if you know it, otherwise null)
6. Confidence: high/medium/low

Companies to assess:
{companies_text}

IMPORTANT GUIDELINES:
- "Real" means the company MAKES, SUPPLIES, OPERATES, or OWNS something specifically needed for one of the frontier domains.
- A company merely using AI, robots, space imagery, or quantum language is NOT a real signal.
- Look for specific validation: contracts, funded programs, supplier awards, backlog, shipments, certifications, capacity expansion, or measurable technical milestones.
- Be skeptical. Most anomalies are noise. Only flag genuine new bottleneck nodes.

Respond with ONLY valid JSON array:
[
  {{
    "company": "name",
    "is_real": true/false,
    "domain": "configured_domain_id or null",
    "sector": "sector name or null",
    "thesis": "one sentence or null",
    "ticker": "TICK or null",
    "confidence": "high|medium|low",
    "reasoning": "one sentence why you think this"
  }}
]"""


def assess_anomalies_with_llm(anomalies: list[dict]) -> list[dict]:
    """Use LLM to assess whether anomalies are real frontier bottleneck nodes."""
    if not anomalies:
        return []

    prompt = build_anomaly_assessment_prompt(anomalies)

    try:
        from src.gemini_client import call_gemini
        result = call_gemini(
            prompt,
            model="gemini-2.5-flash",
            purpose="sector_anomaly_assessment",
        )
        if not result:
            raise ValueError("Empty Gemini response")

        text = result.strip()
        if "```" in text:
            for part in text.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    text = part
                    break

        assessments = json.loads(text)
        if not isinstance(assessments, list):
            assessments = [assessments]
        return assessments

    except Exception as e:
        logger.error(f"LLM assessment failed: {e}")
        try:
            from anthropic import Anthropic
            from dotenv import load_dotenv
            load_dotenv()
            client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            from src.llm_usage import log_ai_call
            log_ai_call(
                model="claude-haiku-4-5",
                purpose="sector_anomaly_assessment_fallback",
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            )
            text = resp.content[0].text.strip()
            if "```" in text:
                for part in text.split("```"):
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("["):
                        text = part
                        break
            return json.loads(text)
        except Exception as e2:
            logger.error(f"Both LLM backends failed: {e2}")
            return []


def run_weekly_discovery() -> dict:
    """Full weekly discovery run."""
    logger.info("=== Weekly Sector Discovery ===")

    mentions = scan_for_new_mentions()
    anomalies = detect_anomalies(window_days=7, min_mentions=3)

    if not anomalies:
        logger.info("No anomalies detected this week")
        return {"anomalies": 0, "real_signals": 0}

    assessments = assess_anomalies_with_llm(anomalies)

    real_signals = []
    with db.get_conn() as conn:
        for i, anomaly in enumerate(anomalies):
            assessment = assessments[i] if i < len(assessments) else {}
            is_real = assessment.get("is_real", False)
            domain = assessment.get("domain") or assessment.get("sector")
            sector = assessment.get("sector") or domain

            conn.execute("""
                INSERT INTO discovery_anomalies
                (detected_date, company_name, ticker_guess, mention_count,
                 first_seen, headlines, llm_assessment, is_real_signal,
                 domain_guess, sector_guess, thesis, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().strftime("%Y-%m-%d"),
                anomaly["company"],
                assessment.get("ticker"),
                anomaly["recent_mentions"],
                anomaly["first_seen"],
                json.dumps(anomaly["headlines"][:5]),
                json.dumps(assessment),
                1 if is_real else 0,
                domain,
                sector,
                assessment.get("thesis"),
                "flagged" if is_real else "dismissed",
            ))

            if is_real:
                real_signals.append({
                    "company": anomaly["company"],
                    "ticker": assessment.get("ticker"),
                    "domain": domain,
                    "sector": sector,
                    "thesis": assessment.get("thesis"),
                    "mentions": anomaly["recent_mentions"],
                    "confidence": assessment.get("confidence", "low"),
                    "reasoning": assessment.get("reasoning", ""),
                    "headlines": anomaly["headlines"][:3],
                })
        conn.commit()

    if real_signals or anomalies:
        _send_discovery_report(anomalies, real_signals)

    result = {
        "companies_scanned": len(mentions),
        "anomalies": len(anomalies),
        "real_signals": len(real_signals),
        "signals": real_signals,
    }
    logger.info(f"Discovery done: {result}")
    return result


def _send_discovery_report(anomalies: list, real_signals: list):
    """Send weekly discovery report via Telegram."""
    try:
        from src.telegram_notifier import send

        lines = ["🔭 <b>Weekly Sector Discovery Report</b>"]
        lines.append(f"Anomalies detected: {len(anomalies)}")
        lines.append(f"Real signals: {len(real_signals)}\n")

        if real_signals:
            lines.append("━━━ 🎯 New Frontier Bottleneck Nodes ━━━\n")
            for s in real_signals:
                emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(s["confidence"], "⚪")
                ticker_str = f" ({s['ticker']})" if s.get("ticker") else ""
                lines.append(f"{emoji} <b>{s['company']}{ticker_str}</b>")
                lines.append(f"   Domain: {s.get('domain') or s.get('sector') or '?'}")
                lines.append(f"   Sector: {s.get('sector', '?')}")
                lines.append(f"   Thesis: {s.get('thesis', '?')}")
                lines.append(f"   Mentions: {s['mentions']}x this week (was 0)")
                lines.append(f"   Evidence: {s.get('reasoning', '')}")
                if s.get("headlines"):
                    for h in s["headlines"][:2]:
                        lines.append(f"   • {h}")
                lines.append("")

            lines.append("⚡ Review and decide: /discover to see status")
        else:
            lines.append("No new frontier bottleneck nodes detected this week. 📊")
            if anomalies:
                lines.append("\nDismissed (noise):")
                for a in anomalies[:5]:
                    lines.append(f"  • {a['company']} ({a['recent_mentions']}x)")

        send("\n".join(lines))
    except Exception as e:
        logger.error(f"Telegram report failed: {e}")


def run_daily_scan():
    """Lightweight daily scan — just collect mentions, no LLM."""
    logger.info("--- Daily mention scan ---")
    mentions = scan_for_new_mentions()
    logger.info(f"Daily scan: {len(mentions)} non-universe companies mentioned")
    return mentions


def get_discovery_status() -> dict:
    """Get current discovery status for /discover command."""
    init_discovery_db()

    with db.get_conn() as conn:
        flagged = conn.execute("""
            SELECT * FROM discovery_anomalies
            WHERE status = 'flagged'
            ORDER BY detected_date DESC LIMIT 10
        """).fetchall()

        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        top_mentions = conn.execute("""
            SELECT company_name, COUNT(*) as cnt
            FROM mention_tracker
            WHERE scan_date >= ?
            GROUP BY company_name
            ORDER BY cnt DESC LIMIT 10
        """, (week_ago,)).fetchall()

        mention_contexts = conn.execute("""
            SELECT context
            FROM mention_tracker
            WHERE scan_date >= ?
        """, (week_ago,)).fetchall()

        total_scans = conn.execute(
            "SELECT COUNT(DISTINCT scan_date) FROM mention_tracker"
        ).fetchone()[0]
        total_companies = conn.execute(
            "SELECT COUNT(DISTINCT company_name) FROM mention_tracker"
        ).fetchone()[0]
        total_anomalies = conn.execute(
            "SELECT COUNT(*) FROM discovery_anomalies"
        ).fetchone()[0]
        real_count = conn.execute(
            "SELECT COUNT(*) FROM discovery_anomalies WHERE is_real_signal = 1"
        ).fetchone()[0]

    domain_counts = {}
    for row in mention_contexts:
        try:
            context = json.loads(row["context"] or "{}")
        except Exception:
            context = {}
        domain_id = context.get("domain_id")
        if domain_id:
            domain_counts[domain_id] = domain_counts.get(domain_id, 0) + 1
    top_domains = [
        {"domain": domain, "count": count}
        for domain, count in sorted(domain_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ]

    return {
        "flagged": [dict(r) for r in flagged],
        "top_mentions": [{"company": r["company_name"], "count": r["cnt"]} for r in top_mentions],
        "top_domains": top_domains,
        "total_scans": total_scans,
        "total_companies": total_companies,
        "total_anomalies": total_anomalies,
        "real_signals": real_count,
    }


def format_telegram_status() -> str:
    """Format discovery status for Telegram."""
    status = get_discovery_status()

    lines = ["🔭 <b>Sector Discovery Status</b>\n"]
    lines.append(f"Scan days: {status['total_scans']}")
    lines.append(f"Companies tracked: {status['total_companies']}")
    lines.append(f"Anomalies found: {status['total_anomalies']}")
    lines.append(f"Real signals: {status['real_signals']}\n")

    if status["flagged"]:
        lines.append("━━━ 🎯 Pending Review ━━━")
        for f in status["flagged"]:
            ticker = f"({f['ticker_guess']})" if f.get("ticker_guess") else ""
            lines.append(f"  • <b>{f['company_name']}</b> {ticker}")
            domain = f.get("domain_guess") or f.get("sector_guess") or "?"
            lines.append(f"    {domain} / {f.get('sector_guess', '?')} — {(f.get('thesis') or '?')}")
            lines.append(f"    Mentions: {f['mention_count']}x | Detected: {f['detected_date']}")

    if status.get("top_domains"):
        lines.append("\n━━━ 🧭 Top Domains (7d) ━━━")
        for d in status["top_domains"][:5]:
            lines.append(f"  {d['count']}x  {d['domain']}")

    if status["top_mentions"]:
        lines.append("\n━━━ 📊 Top Mentions (7d) ━━━")
        for m in status["top_mentions"][:5]:
            lines.append(f"  {m['count']}x  {m['company']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "weekly":
        result = run_weekly_discovery()
        print(f"Anomalies: {result['anomalies']}, Real: {result['real_signals']}")
    else:
        mentions = run_daily_scan()
        print(f"Daily scan: {len(mentions)} companies")
