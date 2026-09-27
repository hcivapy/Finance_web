"""Files the app reads: data/news.json, history/*.json|csv, history/index.json, history/expiring.json,
weekly/index.json, weekly/YYYY-MM.csv, data/ai_usage.json. Also deletes data older than history_months."""
import csv
import glob
import os
import re
from datetime import datetime, timedelta

from common import (BKK, DATA_DIR, HISTORY_DIR, WEEKLY_DIR, add_months, iso, load_json, parse_iso, utcnow,
                    write_json)

NEWS_PATH = os.path.join(DATA_DIR, "news.json")
USAGE_PATH = os.path.join(DATA_DIR, "ai_usage.json")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
WEEK_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- 7-day news file

def load_news():
    return load_json(NEWS_PATH, {"items": []})


def save_news(doc):
    write_json(NEWS_PATH, doc)


def prune_news(items, days, now=None):
    now = now or utcnow()
    cutoff = now - timedelta(days=days)
    return [i for i in items if parse_iso(i["first_seen"]) >= cutoff]


# ---------------------------------------------------------------- long-term history (fundamental news only)

CSV_COLUMNS = ["date", "tickers", "direction", "importance", "impact_area", "reason", "title", "source", "link",
               "paywall", "model"]


def history_record(item):
    ai = item["ai"]
    return {
        "id": item["id"],
        "date": parse_iso(item["published"]).astimezone(BKK).strftime("%Y-%m-%d"),
        "tickers": ai["tickers"],
        "direction": ai["direction"],
        "importance": ai["importance"],
        "impact_area": ai["impact_area"],
        "reason": ai["reason"],
        "title": item["title"],
        "source": item["source"],
        "link": item["link"],
        "paywall": item["paywall"],
        "categories": item["categories"],
        "model": ai["model"],
    }


def write_month_csv(path, records, columns, rowfn):
    # utf-8-sig = UTF-8 with BOM so Excel shows Thai text correctly.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in records:
            w.writerow(rowfn(r))


def _history_row(r):
    return [r["date"], " ".join(r["tickers"]), r["direction"], r["importance"], r["impact_area"], r["reason"],
            r["title"], r["source"], r["link"], "yes" if r["paywall"] else "", r["model"]]


def add_to_history(items):
    """Append newly labelled fundamental items to history/YYYY-MM.json and rebuild that month's CSV."""
    by_month = {}
    fund_ids = {it["id"] for it in items if it.get("ai") and it["ai"].get("label") == "fundamental"}
    for it in items:
        if it.get("ai") and it["ai"].get("label") == "fundamental":
            if it["ai"].get("same_event_as") in fund_ids:
                continue  # same event from another outlet — count it once

            rec = history_record(it)
            by_month.setdefault(rec["date"][:7], []).append(rec)
    for month, recs in by_month.items():
        path = os.path.join(HISTORY_DIR, f"{month}.json")
        doc = load_json(path, {"month": month, "items": []})
        have = {r["id"] for r in doc["items"]}
        doc["items"].extend(r for r in recs if r["id"] not in have)
        doc["items"].sort(key=lambda r: r["date"], reverse=True)
        write_json(path, doc)
        write_month_csv(os.path.join(HISTORY_DIR, f"{month}.csv"), doc["items"], CSV_COLUMNS, _history_row)
    return sum(len(v) for v in by_month.values())


def history_months():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(HISTORY_DIR, "*.json"))
                  if MONTH_RE.match(os.path.basename(p)[:-5]))


def rebuild_history_index():
    """One file with every month for the app's History tab (faster than 12 requests on a phone)."""
    items, months = [], history_months()
    for m in months:
        items.extend(load_json(os.path.join(HISTORY_DIR, f"{m}.json"))["items"])
    items.sort(key=lambda r: r["date"], reverse=True)
    write_json(os.path.join(HISTORY_DIR, "index.json"), {"generated": iso(utcnow()), "months": months, "items": items})


# ---------------------------------------------------------------- retention (same rule for history/ and weekly/)

def oldest_kept_month(months_to_keep, now=None):
    now = (now or utcnow()).astimezone(BKK)
    y, m = add_months(now.year, now.month, -(months_to_keep - 1))
    return f"{y:04d}-{m:02d}"


def deletion_date(month, months_to_keep):
    """A month's files are deleted on the 1st day of the month `months_to_keep` months later (Thai time)."""
    y, m = add_months(int(month[:4]), int(month[5:7]), months_to_keep)
    return f"{y:04d}-{m:02d}-01"


def apply_retention(months_to_keep, now=None):
    """Delete history and weekly files older than the retention window. Returns list of deleted paths."""
    keep_from = oldest_kept_month(months_to_keep, now)
    deleted = []
    for p in glob.glob(os.path.join(HISTORY_DIR, "*.json")) + glob.glob(os.path.join(HISTORY_DIR, "*.csv")):
        name = os.path.basename(p).rsplit(".", 1)[0]
        if MONTH_RE.match(name) and name < keep_from:
            os.remove(p)
            deleted.append(p)
    for p in glob.glob(os.path.join(WEEKLY_DIR, "*.json")) + glob.glob(os.path.join(WEEKLY_DIR, "*.csv")):
        name = os.path.basename(p).rsplit(".", 1)[0]
        month = name[:7] if (WEEK_RE.match(name) or MONTH_RE.match(name)) else None
        if month and month < keep_from:
            os.remove(p)
            deleted.append(p)
    return deleted


def write_expiring(months_to_keep, warning_days, now=None):
    """Tell the app which month is deleted next and when, so it can show the download banner 7 days before."""
    months = sorted(set(history_months()) | set(weekly_months()))
    nxt = None
    if months:
        m = months[0]
        csv_h = f"history/{m}.csv" if os.path.exists(os.path.join(HISTORY_DIR, f"{m}.csv")) else None
        csv_w = f"weekly/{m}.csv" if os.path.exists(os.path.join(WEEKLY_DIR, f"{m}.csv")) else None
        nxt = {"month": m, "delete_on": deletion_date(m, months_to_keep), "history_csv": csv_h, "weekly_csv": csv_w}
    write_json(os.path.join(HISTORY_DIR, "expiring.json"), {
        "generated": iso(now or utcnow()), "history_months": months_to_keep, "warning_days": warning_days,
        "next_deletion": nxt,
    })


# ---------------------------------------------------------------- weekly summaries

def weekly_files():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(WEEKLY_DIR, "*.json"))
                  if WEEK_RE.match(os.path.basename(p)[:-5]))


def weekly_months():
    return sorted({d[:7] for d in weekly_files()})


def _weekly_row(w):
    comp = " | ".join(f"{c['t']} ({c['direction']}): {c['summary']}" for c in w["summary"]["companies"])
    s = w["summary"]
    return [w["date"], w["period_start"], w["period_end"], s["headline"], s["overview"], comp, s["macro"],
            s["crypto_gold"], s["watch_next"], w["news_count"], w["model"]]


WEEKLY_CSV_COLUMNS = ["date", "period_start", "period_end", "headline", "overview", "companies", "macro",
                      "crypto_gold", "watch_next", "news_count", "model"]


def rebuild_weekly_index(last_attempt=None):
    dates = weekly_files()
    entries = []
    for d in reversed(dates):
        w = load_json(os.path.join(WEEKLY_DIR, f"{d}.json"))
        entries.append({"date": d, "period_start": w["period_start"], "period_end": w["period_end"],
                        "headline": w["summary"]["headline"]})
    # One CSV per month so the "before deletion" banner can offer the month's summaries as a download.
    for month in {d[:7] for d in dates}:
        ws = [load_json(os.path.join(WEEKLY_DIR, f"{d}.json")) for d in dates if d.startswith(month)]
        write_month_csv(os.path.join(WEEKLY_DIR, f"{month}.csv"), ws, WEEKLY_CSV_COLUMNS, _weekly_row)
    prev = load_json(os.path.join(WEEKLY_DIR, "index.json"), {})
    write_json(os.path.join(WEEKLY_DIR, "index.json"), {
        "generated": iso(utcnow()), "weeks": entries,
        "last_attempt": last_attempt or prev.get("last_attempt"),
    })


# ---------------------------------------------------------------- AI usage log (for monthly cost checks)

def log_usage(kind, usage, cost, items):
    doc = load_json(USAGE_PATH, {"entries": []})
    today = datetime.now(BKK).strftime("%Y-%m-%d")
    doc["entries"].append({"date": today, "kind": kind, "items": items, "input_tokens": usage.input_tokens,
                           "output_tokens": usage.output_tokens, "cost_usd": cost})
    doc["entries"] = doc["entries"][-400:]
    this_month = today[:7]
    doc["month_to_date_usd"] = round(sum(e["cost_usd"] for e in doc["entries"] if e["date"].startswith(this_month)), 4)
    doc["month"] = this_month
    write_json(USAGE_PATH, doc)
    return doc["month_to_date_usd"]
