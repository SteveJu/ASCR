"""Fetch historical SEC 8-K filings and Form 4 insider trades from EDGAR."""
import requests
import time
import json
from datetime import datetime, timedelta
from src.config import all_tickers, START_DATE, END_DATE
from src import db

HEADERS = {"User-Agent": "StockRadarBacktest research@example.com"}
BASE = "https://efts.sec.gov/LATEST/search-index"
FULL_TEXT = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"


def _get_cik(ticker: str) -> str | None:
    """Get CIK number for a ticker."""
    url = "https://www.sec.gov/cgi-bin/browse-edgar"
    params = {"action": "getcompany", "company": ticker, "CIK": ticker,
              "type": "", "dateb": "", "owner": "include", "count": 1,
              "search_text": "", "action": "getcompany", "output": "atom"}
    # Use the tickers.json mapping
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS)
        data = r.json()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


def fetch_8k_filings():
    """Fetch 8-K filings for all universe tickers from EDGAR full-text search."""
    tickers = all_tickers()
    conn = db.get_conn()
    total = 0

    print(f"Fetching 8-K filings for {len(tickers)} tickers...")

    for ticker in tickers:
        cik = _get_cik(ticker)
        if not cik:
            print(f"  {ticker}: CIK not found, skipping")
            continue

        # Use EDGAR XBRL API
        url = f"https://efts.sec.gov/LATEST/search-index?q=%228-K%22&dateRange=custom&startdt={START_DATE}&enddt={END_DATE}&forms=8-K"

        # Actually use the submissions API which is more reliable
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            r = requests.get(submissions_url, headers=HEADERS)
            r.raise_for_status()
            data = r.json()

            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            titles = recent.get("primaryDocDescription", [])

            count = 0
            for i, form in enumerate(forms):
                if form not in ("8-K", "8-K/A"):
                    continue
                filing_date = dates[i] if i < len(dates) else ""
                if filing_date < START_DATE or filing_date > END_DATE:
                    continue

                accession = accessions[i] if i < len(accessions) else ""
                title = titles[i] if i < len(titles) else ""

                # Get filing details
                acc_clean = accession.replace("-", "")
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_clean}/{accession}-index.htm"

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO sec_filings (ticker, filing_date, form_type, accession, title, url) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (ticker, filing_date, form, accession, title, filing_url))
                    count += 1
                except Exception:
                    pass

            if count > 0:
                print(f"  {ticker}: {count} 8-K filings")
                total += count
        except Exception as e:
            print(f"  {ticker}: error - {e}")

        time.sleep(0.15)  # Rate limit: 10 req/sec

    conn.commit()
    conn.close()
    print(f"\nTotal: {total} 8-K filings")


def fetch_insider_trades():
    """Fetch Form 4 insider trades from EDGAR."""
    tickers = all_tickers()
    conn = db.get_conn()
    total = 0

    print(f"\nFetching Form 4 insider trades for {len(tickers)} tickers...")

    for ticker in tickers:
        cik = _get_cik(ticker)
        if not cik:
            continue

        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            r = requests.get(submissions_url, headers=HEADERS)
            r.raise_for_status()
            data = r.json()

            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])

            count = 0
            for i, form in enumerate(forms):
                if form != "4":
                    continue
                filing_date = dates[i] if i < len(dates) else ""
                if filing_date < START_DATE or filing_date > END_DATE:
                    continue

                accession = accessions[i] if i < len(accessions) else ""

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO insider_trades "
                        "(ticker, filing_date, transaction_type, accession) "
                        "VALUES (?, ?, 'unknown', ?)",
                        (ticker, filing_date, accession))
                    count += 1
                except Exception:
                    pass

            if count > 0:
                print(f"  {ticker}: {count} Form 4 filings")
                total += count
        except Exception as e:
            print(f"  {ticker}: error - {e}")

        time.sleep(0.15)

    conn.commit()
    conn.close()
    print(f"\nTotal: {total} Form 4 filings")


if __name__ == "__main__":
    db.init_db()
    fetch_8k_filings()
    fetch_insider_trades()
