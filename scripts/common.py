"""Shared helpers: paths, config loading, time, keyword matching, file writing."""
import glob
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
HISTORY_DIR = os.path.join(ROOT, "history")
WEEKLY_DIR = os.path.join(ROOT, "weekly")

# Thailand has no daylight saving, so a fixed offset is exact (and avoids needing tzdata on Windows).
BKK = timezone(timedelta(hours=7), "Asia/Bangkok")


def load_json(path, default=None):
    try:
        # utf-8-sig also accepts files saved with a BOM (some Windows editors add one)
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


STOCKS_DIR = os.path.join(ROOT, "stocks")
STOCK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,11}$")
COUNTRY_ORDER = {"US": 0, "JP": 1, "UK": 2}


def _check_stock(s):
    """Return an error message for a malformed stock file, or None if it is usable."""
    if not isinstance(s, dict):
        return "ไม่ใช่ข้อมูลหุ้น"
    if not isinstance(s.get("id"), str) or not STOCK_ID_RE.match(s["id"]):
        return "ช่อง id ต้องเป็นตัวพิมพ์ใหญ่/ตัวเลข เช่น AAPL"
    if not isinstance(s.get("name"), str) or not s["name"].strip():
        return "ไม่มีช่อง name"
    for k in ("keywords", "exact_words", "weak_words", "context", "exclude", "tickers"):
        if k in s and not (isinstance(s[k], list) and all(isinstance(x, str) for x in s[k])):
            return f"ช่อง {k} ต้องเป็นรายการข้อความ [\"...\", \"...\"]"
    if not (s.get("keywords") or s.get("exact_words") or s.get("weak_words")):
        return "ต้องมีอย่างน้อยหนึ่งช่อง: keywords / exact_words / weak_words"
    return None


def load_watchlist():
    """watchlist.json (assets, indexes) + one file per stock in stocks/.

    A broken stock file is skipped and reported in "errors" — it never stops the daily run.
    """
    wl = load_json(os.path.join(ROOT, "watchlist.json"))
    stocks, errors = {}, []
    for s in wl.get("stocks", []):  # older layout: stocks listed inside watchlist.json
        stocks[s["id"]] = s
    for path in sorted(glob.glob(os.path.join(STOCKS_DIR, "*.json"))):
        name = os.path.basename(path)
        try:
            s = load_json(path)
        except (ValueError, OSError) as e:
            errors.append({"file": f"stocks/{name}", "error": f"รูปแบบ JSON ผิด ({str(e)[:80]})"})
            continue
        err = _check_stock(s)
        if err:
            errors.append({"file": f"stocks/{name}", "error": err})
            continue
        stocks[s["id"]] = {**s, "file": f"stocks/{name}"}
    wl["stocks"] = sorted(stocks.values(), key=lambda s: (COUNTRY_ORDER.get(s.get("country"), 9), s.get("added", ""), s["id"]))
    wl["errors"] = errors
    return wl


def load_configs():
    return (
        load_json(os.path.join(ROOT, "config.json")),
        load_json(os.path.join(ROOT, "sources.json")),
        load_watchlist(),
    )


def write_json(path, obj):
    """Write atomically so a crash never leaves a half-written file for the app to read."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def month_key(dt):
    """YYYY-MM in Thai time."""
    return dt.astimezone(BKK).strftime("%Y-%m")


def add_months(year, month, n):
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


# ---------- keyword matching ----------
# keywords: case-insensitive whole words; trailing * = prefix match.
# exact_words: case-sensitive whole words.

def _pattern(word):
    prefix = word.endswith("*")
    w = re.escape(word.rstrip("*"))
    tail = r"[A-Za-z0-9'\-]*" if prefix else ""
    return r"(?<![A-Za-z0-9])" + w + tail + r"(?![A-Za-z0-9])"


def compile_words(keywords=None, exact_words=None):
    """Return a matcher function text -> bool, or None if there are no words."""
    parts = []
    if keywords:
        parts.append(re.compile("|".join(_pattern(k) for k in keywords), re.IGNORECASE))
    if exact_words:
        parts.append(re.compile("|".join(_pattern(k) for k in exact_words)))
    if not parts:
        return None
    return lambda text: any(p.search(text) for p in parts)


class EntityMatcher:
    """Matches one watchlist stock or asset against a news text."""

    def __init__(self, spec):
        self.id = spec["id"]
        self.strong = compile_words(spec.get("keywords"), spec.get("exact_words"))
        self.weak = compile_words(None, spec.get("weak_words"))
        self.context = compile_words(spec.get("context"))
        self.exclude = compile_words(spec.get("exclude"))
        # For assets, "context" applies to strong words too (e.g. "gold" needs a financial context word).
        self.context_required_for_strong = spec.get("_asset", False) and self.context is not None

    def match(self, text):
        if self.exclude and self.exclude(text):
            return False
        if self.strong and self.strong(text):
            if self.context_required_for_strong:
                return self.context(text)
            return True
        if self.weak and self.weak(text):
            return bool(self.context and self.context(text))
        return False
