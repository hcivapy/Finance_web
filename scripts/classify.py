"""Turn raw feed entries into news items: filter, match stocks/assets, assign categories, dedupe, apply limits."""
import hashlib
import re
from datetime import timedelta

from common import BKK, EntityMatcher, compile_words, iso, parse_iso, utcnow
from fetch import short

# Google News spells some outlets differently from their own feeds; use one name per outlet.
SOURCE_ALIASES = {
    "The Wall Street Journal": "WSJ", "Bloomberg.com": "Bloomberg", "apnews.com": "AP News",
    "Associated Press": "AP News", "FT": "Financial Times", "rmb.reuters.com": "Reuters", "Kitco NEWS": "Kitco",
}

STOP = set("a an the and or of to in on for with at by from as is are was were be after over new says said its it this that".split())


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def item_id(title):
    return hashlib.sha1(norm_title(title).encode()).hexdigest()[:12]


def tokens(t):
    return {w for w in norm_title(t).split() if len(w) > 2 and w not in STOP}


def similar(a, b):
    """Near-duplicate headline check (same story worded slightly differently)."""
    if len(a) < 5 or len(b) < 5:
        return False
    return len(a & b) / len(a | b) >= 0.6


class Classifier:
    def __init__(self, config, sources, watchlist):
        self.config = config
        self.trusted = set(sources.get("trusted_sources", []))
        self.paywall_sources = set(sources.get("paywall_sources", []))
        self.stocks = [EntityMatcher(s) for s in watchlist["stocks"]]
        self.assets = [EntityMatcher({**a, "_asset": True}) for a in watchlist["assets"]]
        rules = config["category_rules"]
        self.rule = {k: compile_words(v.get("keywords"), v.get("exact_words")) for k, v in rules.items()}
        self.big_deal = compile_words(rules["deals"].get("big_deal_keywords"))
        self.exclude_title = compile_words([p for p in config.get("exclude_title_patterns", [])])
        self.priority = {s: i for i, s in enumerate(config.get("source_priority", []))}

    def rank(self, item):
        # Free-to-read first (user chose: show paywalled news but prefer a free copy of the same story), then priority list.
        return (item["paywall"], self.priority.get(item["source"], 999), -item["_ts"])

    def build(self, job, raw, now):
        """Raw entry -> item dict, or None if it should be dropped."""
        title, source = raw["title"], job.get("source") or job["name"]
        summary = short(raw["summary"])
        if job["kind"] != "feed":
            source = raw.get("source_name") or source
            if title.endswith(" - " + source):
                title = title[: -len(source) - 3].strip()
            summary = ""  # Google News "summary" is just the headline again
            if job["kind"] == "gnews_stock" and not job.get("all_sources") and source not in self.trusted:
                return None
            source = SOURCE_ALIASES.get(source, source)
        # Under 4 words = not a real headline (e.g. Reuters quote pages titled "NVDA.B").
        if not raw["link"] or len(title.split()) < 4:
            return None
        if self.exclude_title and self.exclude_title(title):
            return None
        published = raw["published"] or now
        if published > now + timedelta(hours=1):
            published = now
        if now - published > timedelta(hours=self.config["max_news_age_hours"]):
            return None

        text = f"{title} {summary}"
        allowed = set(job["categories"])
        tickers = [m.id for m in self.stocks if m.match(text)]
        assets = [m.id for m in self.assets if m.match(text)] if "crypto_gold" in allowed else []
        cats = []
        if tickers and "watchlist" in allowed:
            cats.append("watchlist")
        if "fed" in allowed and (job.get("take_all") or self.rule["fed"](text)):
            cats.append("fed")
        if "deals" in allowed and self.rule["deals"](text) and (tickers or (self.big_deal and self.big_deal(text))):
            cats.append("deals")
        if "products" in allowed and tickers and self.rule["products"](text):
            cats.append("products")
        if "market" in allowed and self.rule["market"](text):
            cats.append("market")
        if assets:
            cats.append("crypto_gold")
        if not cats:
            return None
        paywall = bool(job.get("paywall")) or source in self.paywall_sources
        return {
            "id": item_id(title), "title": title, "summary": summary, "link": raw["link"], "source": source,
            "paywall": paywall, "published": iso(published), "first_seen": iso(now),
            "categories": cats, "tickers": tickers + assets, "also": [], "ai": None,
            "_ts": published.timestamp(), "_tok": tokens(title),
        }

    def dedupe(self, items, existing):
        """Merge duplicates (same or near-same headline). Returns only items not already in `existing`."""
        seen_ids = {e["id"] for e in existing}
        seen_tok = [tokens(e["title"]) for e in existing]
        items = sorted(items, key=self.rank)
        kept = []
        for it in items:
            if it["id"] in seen_ids or any(similar(it["_tok"], t) for t in seen_tok):
                continue
            dup = next((k for k in kept if k["id"] == it["id"] or similar(k["_tok"], it["_tok"])), None)
            if dup:
                for c in it["categories"]:
                    if c not in dup["categories"]:
                        dup["categories"].append(c)
                for t in it["tickers"]:
                    if t not in dup["tickers"]:
                        dup["tickers"].append(t)
                if it["source"] != dup["source"] and all(a["source"] != it["source"] for a in dup["also"]) and len(dup["also"]) < 3:
                    dup["also"].append({"source": it["source"], "link": it["link"], "paywall": it["paywall"]})
                continue
            kept.append(it)
        return kept

    def apply_limits(self, items, today_items):
        """Daily caps. `today_items` = items already saved today (Thai date), so a re-run cannot exceed the cap."""
        lim = self.config["limits"]
        by_rank = sorted(items, key=self.rank)
        stock_ids = {m.id for m in self.stocks}

        def used(cat, pred=lambda it: True):
            return sum(1 for it in today_items if cat in it["categories"] and pred(it))

        # BTC & gold: ~N per asset per day.
        asset_ids = [m.id for m in self.assets]
        chosen = {}
        for aid in asset_ids:
            room = max(0, lim["crypto_gold_per_asset_per_day"] - used("crypto_gold", lambda it: aid in it["tickers"]))
            picks = [it for it in by_rank if "crypto_gold" in it["categories"] and aid in it["tickers"]]
            for it in picks[:room]:
                chosen.setdefault(it["id"], set()).add(aid)
        for it in items:
            if "crypto_gold" in it["categories"]:
                keep = chosen.get(it["id"], set())
                it["tickers"] = [t for t in it["tickers"] if t not in asset_ids or t in keep]
                if not keep:
                    it["categories"].remove("crypto_gold")

        def cap(cat, n, pred=lambda it: True):
            room = max(0, n - used(cat, pred))
            picks = [it for it in by_rank if cat in it["categories"] and pred(it)]
            for it in picks[room:]:
                it["categories"].remove(cat)

        cap("market", lim["market_per_day"])
        cap("deals", lim["deals_outside_watchlist_per_day"], lambda it: not (set(it["tickers"]) & stock_ids))
        return [it for it in items if it["categories"]]


def strip_private(item):
    return {k: v for k, v in item.items() if not k.startswith("_")}


def collect(results, classifier, existing, now=None):
    """Fetch results -> (new items, per-source status list)."""
    now = now or utcnow()
    items, status = [], []
    for job, raws, err in results:
        built = 0
        for raw in raws:
            it = classifier.build(job, raw, now)
            if it:
                items.append(it)
                built += 1
        status.append({"name": job["name"], "ok": err is None, "error": err, "fetched": len(raws), "relevant": built})
    new = classifier.dedupe(items, existing)
    today = now.astimezone(BKK).date()
    today_items = [e for e in existing if parse_iso(e["first_seen"]).astimezone(BKK).date() == today]
    new = classifier.apply_limits(new, today_items)
    return [strip_private(i) for i in new], status
