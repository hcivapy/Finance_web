"""Download and parse RSS/Atom feeds. A failing source is recorded and skipped, never fatal."""
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

from common import parse_iso

UA = "Mozilla/5.0 (compatible; MyMarketWatch/1.0; personal RSS reader)"
TIMEOUT = 25
SUMMARY_MAX = 280  # keep only a short teaser (copyright: never store full articles)

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


def clean_text(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def short(s, n=SUMMARY_MAX):
    s = clean_text(s)
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = parse_iso(s)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def parse_feed(content):
    """Return list of dicts: title, link, summary, published (datetime|None), source_name (Google News only)."""
    root = ET.fromstring(content)
    out = []
    if root.tag == ATOM + "feed":
        for e in root.iter(ATOM + "entry"):
            link = ""
            for l in e.findall(ATOM + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            out.append({
                "title": clean_text(e.findtext(ATOM + "title")),
                "link": link.strip(),
                "summary": e.findtext(ATOM + "summary") or e.findtext(ATOM + "content") or "",
                "published": parse_date(e.findtext(ATOM + "published") or e.findtext(ATOM + "updated")),
                "source_name": None,
            })
        return out
    for it in root.iter("item"):
        src = it.find("source")
        out.append({
            "title": clean_text(it.findtext("title")),
            "link": (it.findtext("link") or it.findtext("guid") or "").strip(),
            "summary": it.findtext("description") or "",
            "published": parse_date(it.findtext("pubDate") or it.findtext(DC + "date")),
            "source_name": src.text.strip() if src is not None and src.text else None,
        })
    return out


def get(url):
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return parse_feed(r.content)


def _fetch_one(job):
    try:
        items = get(job["url"])
        return job, items, None
    except requests.HTTPError as e:
        return job, [], f"HTTP {e.response.status_code}"
    except requests.RequestException as e:
        return job, [], type(e).__name__
    except ET.ParseError:
        return job, [], "อ่านไฟล์ข่าวไม่ได้ (รูปแบบเสีย)"
    except Exception as e:  # never let one source kill the run
        return job, [], f"{type(e).__name__}: {str(e)[:80]}"


def google_news_url(template, query):
    return template.replace("{query}", urllib.parse.quote(query))


def build_jobs(sources, watchlist):
    """Every fetch job: direct feeds, Google News topics, per-stock searches, Reuters batch searches."""
    jobs = []
    for f in sources["feeds"]:
        if f.get("enabled", True):
            jobs.append({"name": f["name"], "url": f["url"], "kind": "feed", "categories": f["categories"],
                         "paywall": f.get("paywall", False), "take_all": f.get("take_all", False),
                         "source": f.get("source") or f["name"].split(" (")[0]})
    gn = sources["google_news"]
    tpl = gn["url_template"]
    for t in gn["topics"]:
        if t.get("enabled", True):
            jobs.append({"name": t["name"], "url": google_news_url(tpl, t["query"]), "kind": "gnews_site",
                         "categories": t["categories"], "paywall": t.get("paywall", False)})
    stocks = watchlist["stocks"]
    for s in stocks:
        if s.get("search"):
            jobs.append({"name": f"Google News: {s['id']}", "url": google_news_url(tpl, f"{s['search']} when:2d"),
                         "kind": "gnews_stock", "categories": ["watchlist", "deals", "products"],
                         "all_sources": s.get("search_all_sources", False)})
    # Reuters has no RSS of its own: search it through Google News, several companies per query.
    group = 3  # Google News returns at most 100 results per query; big names fill that quickly
    for i in range(0, len(stocks), group):
        # Flat "A OR B OR C" — tested: nested brackets or extra words make Google News return nothing.
        names = " OR ".join(s["search"] for s in stocks[i:i + group] if s.get("search"))
        ids = ",".join(s["id"] for s in stocks[i:i + group])
        jobs.append({"name": f"Reuters: {ids}", "url": google_news_url(tpl, f"site:reuters.com ({names}) when:2d"),
                     "kind": "gnews_site", "categories": ["watchlist", "deals", "products"], "paywall": False})
    return jobs


def fetch_all(jobs):
    """Direct feeds run in parallel; Google News requests go one at a time to stay polite."""
    direct = [j for j in jobs if j["kind"] == "feed"]
    gnews = [j for j in jobs if j["kind"] != "feed"]
    results = []
    with ThreadPoolExecutor(10) as ex:
        results.extend(ex.map(_fetch_one, direct))
    for j in gnews:
        results.append(_fetch_one(j))
        time.sleep(1.0)
    return results
