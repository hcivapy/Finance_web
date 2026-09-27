"""Shared helpers: paths, config loading, time, keyword matching, file writing."""
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
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def load_configs():
    return (
        load_json(os.path.join(ROOT, "config.json")),
        load_json(os.path.join(ROOT, "sources.json")),
        load_json(os.path.join(ROOT, "watchlist.json")),
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
