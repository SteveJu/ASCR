"""SEC EDGAR fetcher — 13F, Form 4, 8-K, 13D/G filings."""
import requests
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from src import config, db
from src.utils import get_logger

logger = get_logger("sec_fetcher")

HEADERS = None
CIK_MAP = None  # ticker -> cik cache

def _headers():
    global HEADERS
    if HEADERS is None:
        cfg = config.universe()
        # Use sec config from models or fallback
        ua = f"ASCR {os.environ.get('SEC_USER_AGENT_EMAIL', 'research@example.com')}"
        HEADERS = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
    return HEADERS

def _get_cik_map():
    """Build ticker -> CIK mapping from SEC."""
    global CIK_MAP
    if CIK_MAP is not None:
        return CIK_MAP
    try:
        resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=_headers(), timeout=15)
        time.sleep(0.15)
        if resp.status_code == 200:
            data = resp.json()
            CIK_MAP = {}
            for entry in data.values():
                CIK_MAP[entry["ticker"].upper()] = str(entry["cik_str"])
            return CIK_MAP
    except Exception as e:
        logger.error(f"Failed to load CIK map: {e}")
    CIK_MAP = {}
    return CIK_MAP

def _get_cik(ticker: str) -> str:
    m = _get_cik_map()
    return m.get(ticker.upper(), "")

def fetch_recent_filings(ticker: str, form_types=("8-K", "10-Q", "10-K", "4"), days=7):
    """Fetch recent SEC filings for a ticker."""
    cik = _get_cik(ticker)
    if not cik:
        return []

    sub_url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    try:
        resp = requests.get(sub_url, headers=_headers(), timeout=15)
        time.sleep(0.15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        descs = recent.get("primaryDocDescription", [])

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        results = []
        for i, form in enumerate(forms):
            if form in form_types and dates[i] >= cutoff:
                acc = accessions[i]
                if not db.is_filing_seen(acc):
                    desc = descs[i] if i < len(descs) else ""
                    results.append({
                        "accession": acc,
                        "form": form,
                        "date": dates[i],
                        "description": desc,
                        "ticker": ticker,
                        "cik": cik,
                    })
        return results
    except Exception as e:
        logger.error(f"Error fetching filings for {ticker}: {e}")
        return []

def fetch_form4_purchases(ticker: str, days=7):
    """Fetch Form 4 insider PURCHASES for a ticker."""
    filings = fetch_recent_filings(ticker, form_types=("4",), days=days)
    purchases = []

    for filing in filings:
        cik = filing["cik"]
        acc = filing["accession"].replace("-", "")
        cik_clean = cik.lstrip("0")

        try:
            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc}/index.json"
            resp = requests.get(index_url, headers=_headers(), timeout=15)
            time.sleep(0.15)
            if resp.status_code != 200:
                db.mark_filing(filing["accession"], ticker, "4", filing["date"])
                continue

            items = resp.json().get("directory", {}).get("item", [])
            xml_name = None
            for item in items:
                if item.get("name", "").endswith(".xml") and "primary" not in item["name"].lower():
                    xml_name = item["name"]
                    break

            if not xml_name:
                db.mark_filing(filing["accession"], ticker, "4", filing["date"])
                continue

            xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc}/{xml_name}"
            resp = requests.get(xml_url, headers=_headers(), timeout=15)
            time.sleep(0.15)

            root = ET.fromstring(resp.content)
            reporter = root.find(".//reportingOwner/reportingOwnerId/rptOwnerName")
            reporter_name = reporter.text if reporter is not None else "Unknown"
            title_el = root.find(".//reportingOwner/reportingOwnerRelationship/officerTitle")
            title = title_el.text if title_el is not None else ""

            for txn in root.findall(".//nonDerivativeTransaction"):
                code_el = txn.find(".//transactionCoding/transactionCode")
                if code_el is not None and code_el.text == "P":
                    shares_el = txn.find(".//transactionAmounts/transactionShares/value")
                    price_el = txn.find(".//transactionAmounts/transactionPricePerShare/value")
                    shares = float(shares_el.text) if shares_el is not None else 0
                    price = float(price_el.text) if price_el is not None and price_el.text else 0
                    if shares > 0:
                        purchases.append({
                            "ticker": ticker,
                            "reporter": reporter_name,
                            "title": title,
                            "shares": shares,
                            "price": price,
                            "value": shares * price,
                            "date": filing["date"],
                            "accession": filing["accession"],
                        })
        except Exception as e:
            logger.warning(f"Error parsing Form 4 {filing['accession']}: {e}")

        db.mark_filing(filing["accession"], ticker, "4", filing["date"])

    return purchases

def fetch_all(tickers: list = None, days=7):
    """Fetch all filing types for all tickers."""
    if tickers is None:
        tickers = config.all_tickers()

    logger.info(f"Fetching SEC filings for {len(tickers)} tickers (last {days} days)...")

    all_filings = []
    all_purchases = []

    for ticker in tickers:
        filings = fetch_recent_filings(ticker, days=days)
        for f in filings:
            db.mark_filing(f["accession"], ticker, f["form"], f["date"], f.get("description", ""))
            all_filings.append(f)

        purchases = fetch_form4_purchases(ticker, days=days)
        all_purchases.extend(purchases)

        time.sleep(0.2)

    logger.info(f"Found {len(all_filings)} new filings, {len(all_purchases)} insider purchases.")
    return {"filings": all_filings, "purchases": all_purchases}

if __name__ == "__main__":
    result = fetch_all()
    for p in result["purchases"]:
        print(f"{p['ticker']}: {p['reporter']} bought {p['shares']:,.0f} @ ${p['price']:.2f}")
