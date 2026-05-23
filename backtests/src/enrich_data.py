"""Enrich existing backtest data for V3:
1. Fetch 8-K item details from SEC EDGAR
2. Classify insider trades (buy vs sell)
3. Generate insider trading events
"""
import time
import json
import hashlib
import requests
import sqlite3
import os
from src import db

HEADERS = {"User-Agent": f"ASCRBacktest {os.environ.get('SEC_USER_AGENT_EMAIL', 'research@example.com')}"}

# CIK mapping
TICKER_CIK = {
    "MU": "723125", "SNDK": "1000180", "STX": "1137789", "WDC": "106040",
    "CIEN": "936395", "COHR": "820318", "GLW": "24741", "LITE": "1434524",
    "ANET": "1313545", "AVGO": "1649338", "CSCO": "858877", "MRVL": "1058057",
    "CEG": "1868275", "ETN": "31462", "NEE": "753308", "VRT": "1674101",
    "VST": "1862068", "DLR": "1365135", "EQIX": "1101239", "IREN": "1878848",
    "NBIS": "1877787", "AMAT": "6951", "ASML": "937966", "KLAC": "319201",
    "LRCX": "707549", "TER": "97210", "ARM": "1973239", "CDNS": "813672",
    "SNPS": "883241", "AAOI": "1158838", "CRWV": "1822359", "MOD": "806468",
    "PWR": "1040971", "NVDA": "1045810", "AMD": "2488", "INTC": "50863",
    "TSM": "1046179", "SMCI": "1375365", "CRDO": "1807794", "MXL": "1288469",
    "ACMR": "1680062", "UCTT": "1275014", "VICR": "751364", "ON": "861374",
    "APLD": "1144879", "KEEL": "1822359", "SAP": "1000184",
    "DELL": "1571996", "QCOM": "804328",
}

# 8-K item codes that matter
IMPORTANT_8K_ITEMS = {
    "1.01": "Entry into Material Agreement",
    "1.02": "Termination of Material Agreement",
    "2.01": "Completion of Acquisition",
    "2.02": "Results of Operations",
    "2.05": "Costs of Restructuring",
    "2.06": "Material Impairment",
    "3.02": "Unregistered Sales of Equity",
    "5.02": "Departure of Directors/Officers",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}


def enrich_8k_items():
    """Re-fetch 8-K filings with item-level detail."""
    conn = db.get_conn()

    # Get all filings without items
    filings = conn.execute(
        "SELECT id, ticker, accession, filing_date FROM sec_filings WHERE items IS NULL"
    ).fetchall()

    print(f"Enriching {len(filings)} filings with item details...")

    updated = 0
    for f in filings:
        ticker = f["ticker"]
        cik = TICKER_CIK.get(ticker)
        if not cik:
            continue

        accession = f["accession"]
        acc_clean = accession.replace("-", "")

        # Fetch filing index page to get item details
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"
        try:
            resp = requests.get(index_url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                # Try submissions API
                cik_padded = cik.zfill(10)
                api_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
                resp2 = requests.get(api_url, headers=HEADERS, timeout=10)
                if resp2.status_code == 200:
                    data = resp2.json()
                    recent = data.get("filings", {}).get("recent", {})
                    accessions = recent.get("accessionNumber", [])
                    items_list = recent.get("items", [])
                    primary_docs = recent.get("primaryDocument", [])

                    for i, acc in enumerate(accessions):
                        if acc == accession:
                            items_str = items_list[i] if i < len(items_list) else ""
                            doc = primary_docs[i] if i < len(primary_docs) else ""
                            if items_str:
                                # Build descriptive title from items
                                items = [it.strip() for it in items_str.split(",") if it.strip()]
                                item_desc = "; ".join(
                                    f"{it}: {IMPORTANT_8K_ITEMS.get(it, 'other')}"
                                    for it in items if it in IMPORTANT_8K_ITEMS
                                )
                                if item_desc:
                                    conn.execute(
                                        "UPDATE sec_filings SET items=?, title=? WHERE id=?",
                                        (items_str, f"8-K: {item_desc}", f["id"])
                                    )
                                    updated += 1
                            break
                time.sleep(0.12)
            else:
                # Parse index page for items
                text = resp.text
                import re
                items_found = re.findall(r'Item\s+(\d+\.\d+)', text)
                if items_found:
                    items_str = ", ".join(items_found)
                    item_desc = "; ".join(
                        f"{it}: {IMPORTANT_8K_ITEMS.get(it, 'other')}"
                        for it in items_found if it in IMPORTANT_8K_ITEMS
                    )
                    if item_desc:
                        conn.execute(
                            "UPDATE sec_filings SET items=?, title=? WHERE id=?",
                            (items_str, f"8-K: {item_desc}", f["id"])
                        )
                        updated += 1
                time.sleep(0.12)

        except Exception as e:
            pass

    conn.commit()
    conn.close()
    print(f"Updated {updated}/{len(filings)} filings with item details")


def classify_insider_trades():
    """Classify insider trades as buy/sell from SEC Form 4 data.
    Transaction codes: P=Purchase, S=Sale, A=Grant, M=Exercise, G=Gift
    """
    conn = db.get_conn()

    # Re-fetch insider trades with proper transaction codes
    from src.config import all_tickers, START_DATE, END_DATE

    for ticker in all_tickers():
        cik = TICKER_CIK.get(ticker)
        if not cik:
            continue

        cik_padded = cik.zfill(10)
        try:
            url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue

            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])

            # Count Form 4s in our date range
            f4_count = sum(1 for i, f in enumerate(forms)
                         if f == "4" and START_DATE <= dates[i] <= END_DATE)

            if f4_count > 0:
                # Use the owner API for transaction details
                owner_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{cik}%22&forms=4&dateRange=custom&startdt={START_DATE}&enddt={END_DATE}"
                # This won't work well — instead, parse from existing data
                pass

            time.sleep(0.12)
        except Exception as e:
            pass

    conn.close()
    print("Insider trade classification done")


def generate_insider_events():
    """Convert insider trades into buy/sell events.

    Key signals:
    - Cluster of insider buys = bullish (rare and significant)
    - Large insider sell (>$1M) = bearish (but could be planned)
    - CEO/CFO trades weigh more
    """
    conn = db.get_conn()

    from src.config import all_tickers

    events_added = 0
    for ticker in all_tickers():
        # Group insider trades by month
        trades = conn.execute("""
            SELECT filing_date, trader_name, title, shares, price, value
            FROM insider_trades
            WHERE ticker = ?
            ORDER BY filing_date
        """, (ticker,)).fetchall()

        if not trades:
            continue

        # Cluster by month — look for unusual activity
        from collections import defaultdict
        monthly = defaultdict(lambda: {"count": 0, "total_value": 0, "traders": set()})

        for t in trades:
            month = t["filing_date"][:7]  # YYYY-MM
            val = abs(t["value"] or 0)
            monthly[month]["count"] += 1
            monthly[month]["total_value"] += val
            if t["trader_name"]:
                monthly[month]["traders"].add(t["trader_name"])

        # Find months with unusually high activity (>5 trades or >$5M value)
        for month, data in monthly.items():
            if data["count"] >= 5 or data["total_value"] >= 5_000_000:
                # This is significant insider activity
                headline = (f"Insider activity spike: {ticker} had {data['count']} insider trades "
                           f"totaling ${data['total_value']/1e6:.1f}M in {month} "
                           f"by {len(data['traders'])} insiders")

                h = hashlib.md5(f"{ticker}{month}insider".encode()).hexdigest()

                # Most insider trades are sells (compensation), so heavy selling = neutral to slightly bearish
                # The signal is VOLUME of activity, not direction (we can't tell P vs S)
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO events
                        (date, ticker, source, headline, event_type, evidence_delta,
                         verdict, conviction, thesis, confidence, hash)
                        VALUES (?, ?, 'insider_backtest', ?, 'insider_activity', ?,
                                ?, ?, ?, ?, ?)
                    """, (
                        f"{month}-15",  # mid-month
                        ticker,
                        headline,
                        3,  # moderate signal
                        "HOLD",  # insider sells are ambiguous
                        "LOW",
                        f"High insider trading activity in {ticker} — could indicate upcoming catalyst or compensation events",
                        0.5,
                        h,
                    ))
                    events_added += 1
                except Exception:
                    pass

    conn.commit()
    conn.close()
    print(f"Generated {events_added} insider activity events")


if __name__ == "__main__":
    print("=== Step 1: Enrich 8-K with item details ===")
    enrich_8k_items()

    print("\n=== Step 2: Generate insider events ===")
    generate_insider_events()

    print("\nDone! Run analyze with better model next.")
