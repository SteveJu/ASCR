#!/usr/bin/env python3
"""Weekly health check for ASCR + ASCR-H.
Outputs a Telegram-ready summary."""

import os, sys, re, sqlite3, importlib, subprocess
from datetime import datetime, timedelta

RADAR = os.environ.get("ASCR_PROJECT_DIR", "../ASCR")
PAPER = os.environ.get("ASCR_H_PROJECT_DIR", "../ASCR-H")
issues = []

# 1. IMPORTS
for project, path in [("radar", RADAR), ("paper", PAPER)]:
    # Clean slate for each project
    to_del = [k for k in sys.modules if k.startswith("src")]
    for k in to_del: del sys.modules[k]
    # Reset path
    sys.path = [p for p in sys.path if "/ASCR" not in p and "/ASCR-H" not in p]
    sys.path.insert(0, path)
    os.chdir(path)
    for f in sorted(os.listdir("src")):
        if not f.endswith(".py") or f.startswith("__"): continue
        mod = f"src.{f[:-3]}"
        try:
            importlib.import_module(mod)
        except Exception as e:
            issues.append(f"❌ {project} import {mod}: {str(e).split(chr(10))[0]}")

# 2. SHADOWED IMPORTS
for project, path in [("radar", RADAR), ("paper", PAPER)]:
    for f in sorted(os.listdir(os.path.join(path, "src"))):
        if not f.endswith(".py") or f.startswith("__"): continue
        content = open(os.path.join(path, "src", f)).read()
        lines = content.split("\n")
        top_imports = set()
        for line in lines[:30]:
            m = re.match(r"^import (\w+)", line.strip())
            if m: top_imports.add(m.group(1))
        for i, line in enumerate(lines[30:], start=30):
            m = re.match(r"\s+import (\w+)$", line)
            if m and m.group(1) in top_imports:
                issues.append(f"❌ {project} {f}:L{i+1}: shadowed 'import {m.group(1)}'")

# 3. DB INTEGRITY
for name, dbpath in [
    ("radar", f"{RADAR}/data/ascr.sqlite"),
    ("experience", f"{RADAR}/data/experience.sqlite"),
    ("paper", f"{PAPER}/data/ascr_h.sqlite"),
]:
    if not os.path.exists(dbpath):
        issues.append(f"❌ {name} DB missing"); continue
    conn = sqlite3.connect(dbpath)
    r = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if r != "ok": issues.append(f"❌ {name} DB: {r}")
    conn.close()

# 4. DATA FRESHNESS
conn = sqlite3.connect(f"{RADAR}/data/ascr.sqlite")
latest_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0] or ""
latest_score = conn.execute("SELECT MAX(date) FROM scores").fetchone()[0] or ""
latest_event = conn.execute("SELECT MAX(date) FROM events").fetchone()[0] or ""
event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
ticker_count = conn.execute("SELECT COUNT(DISTINCT ticker) FROM scores WHERE date=(SELECT MAX(date) FROM scores)").fetchone()[0]
conn.close()

conn = sqlite3.connect(f"{PAPER}/data/ascr_h.sqlite")
latest_equity = conn.execute("SELECT MAX(date) FROM paper_equity_curve").fetchone()[0] or ""
positions = conn.execute("SELECT ticker, quantity, avg_entry_price, current_value FROM paper_positions WHERE status='open' AND quantity > 0").fetchall()
account = conn.execute("SELECT cash FROM account").fetchone()
cash = account[0] if account else 0
pos_value = sum(p[3] or 0 for p in positions)
total_equity = cash + pos_value
ghosts = conn.execute("SELECT ticker FROM paper_positions WHERE status='closed' AND quantity > 0").fetchall()
orphans = conn.execute("SELECT ticker FROM paper_positions WHERE status='open' AND quantity <= 0").fetchall()
if ghosts: issues.append(f"❌ Ghost positions: {[g[0] for g in ghosts]}")
if orphans: issues.append(f"❌ Orphan positions: {[o[0] for o in orphans]}")
if cash < 0: issues.append(f"❌ Negative cash: ${cash:,.2f}")
conn.close()

# 5. LAUNCHD
result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
expected = [
    "com.ascr.daily", "com.ASCR.fast-scan",
    "com.ASCR.discovery", "com.ASCR.universe-scan", "com.ASCR.event-daemon",
    "com.ASCR-H.daily", "com.ASCR-H.intraday", "com.ASCR-H.weekly",
]
pids = {}
for job in expected:
    if job not in result.stdout:
        issues.append(f"❌ launchd {job} not loaded")
    else:
        line = [l for l in result.stdout.split("\n") if job in l][0]
        pid = line.split()[0]
        if pid != "-": pids[job] = pid

# 6. RECOMMENDER
to_del = [k for k in sys.modules if k.startswith("src")]
for k in to_del: del sys.modules[k]
sys.path = [p for p in sys.path if "/ASCR-H" not in p]
sys.path.insert(0, RADAR)
os.chdir(RADAR)
try:
    from src.recommender import get_rankings
    r = get_rankings()
    if len(r) == 0: issues.append("❌ recommender: 0 rankings")
except Exception as e:
    issues.append(f"❌ recommender: {e}")

# REPORT
today = datetime.now().strftime("%Y-%m-%d")
R = []
R.append(f"📋 <b>Weekly Health Check</b> — {today}\n")
if not issues:
    R.append("✅ <b>All clear</b>\n")
else:
    R.append(f"⚠️ <b>{len(issues)} issues:</b>")
    for i in issues: R.append(f"  {i}")
    R.append("")
R.append(f"<b>Data:</b> prices={latest_price} scores={latest_score} events={latest_event}")
R.append(f"  {ticker_count} tickers | {event_count} total events | equity_curve={latest_equity}")
R.append(f"\n<b>Portfolio:</b> cash=${cash:,.0f} pos=${pos_value:,.0f} total=${total_equity:,.0f}")
R.append(f"  Open: {', '.join(p[0] for p in positions) or 'none'}")
R.append(f"\n<b>Services:</b>")
for job in expected:
    short = job.split(".")[-1]
    pid = pids.get(job)
    R.append(f"  {'🟢' if pid else '⚪'} {short}" + (f" (PID {pid})" if pid else ""))
print("\n".join(R))
