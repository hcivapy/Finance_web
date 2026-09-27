"""Claude API calls: one daily labelling call and one weekly summary call.

Any failure is converted into AIError(code, Thai message) so callers can keep going without labels.
The API key is read from the ANTHROPIC_API_KEY environment variable (GitHub Secret) — never from a file.
"""
import json
import os

import anthropic

IMPACT_AREAS = ["revenue", "margin", "competitive_advantage", "management", "regulation", "macro", "other"]

ERROR_TEXT = {
    "no_api_key": "ไม่พบ API key (ยังไม่ได้ใส่ ANTHROPIC_API_KEY ใน GitHub Secrets)",
    "auth": "API key ใช้ไม่ได้ (ผิด หมดอายุ หรือถูกลบ) — สร้างคีย์ใหม่แล้วแทนค่าใน GitHub Secrets",
    "permission": "API key ไม่มีสิทธิ์ใช้งานรุ่น AI นี้",
    "credit": "เครดิตใน Claude Console หมด — เติมเครดิต",
    "spend_limit": "ถึงวงเงินสูงสุดต่อเดือน (spend limit) ใน Claude Console แล้ว",
    "rate_limit": "เรียก AI ถี่เกินกำหนด ลองใหม่รอบหน้า",
    "server": "ระบบ AI ของ Anthropic ขัดข้องชั่วคราว",
    "network": "เชื่อมต่อระบบ AI ไม่ได้ (เครือข่าย/หมดเวลา)",
    "bad_output": "AI ตอบกลับผิดรูปแบบ",
    "bad_request": "คำขอไปยัง AI ไม่ถูกต้อง",
}


class AIError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:300]
        self.message = ERROR_TEXT.get(code, code)


def _call(model, system, user, schema, max_tokens):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AIError("no_api_key")
    client = anthropic.Anthropic(max_retries=3, timeout=600)
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise AIError("auth", str(e))
    except anthropic.PermissionDeniedError as e:
        raise AIError("permission", str(e))
    except anthropic.BadRequestError as e:
        text = str(e).lower()
        if "credit" in text or "billing" in text:
            raise AIError("credit", str(e))
        if "limit" in text or "spend" in text or "usage" in text:
            raise AIError("spend_limit", str(e))
        raise AIError("bad_request", str(e))
    except anthropic.RateLimitError as e:
        raise AIError("rate_limit", str(e))
    except anthropic.APIStatusError as e:
        raise AIError("server" if e.status_code >= 500 else "bad_request", f"{e.status_code} {e}")
    except anthropic.APIConnectionError as e:  # includes timeouts
        raise AIError("network", str(e))

    if msg.stop_reason != "end_turn":
        raise AIError("bad_output", f"stop_reason={msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("bad_output", f"invalid JSON: {e}")
    return data, msg.usage


def cost_usd(config, usage, model):
    p = config["ai"]["prices_usd_per_million"].get(model, {"input": 0, "output": 0})
    return round(usage.input_tokens / 1e6 * p["input"] + usage.output_tokens / 1e6 * p["output"], 5)


# ---------------------------------------------------------------- daily labelling

LABEL_SYSTEM = """You label financial news for a long-term value/growth investor (holding period: years).
They care only about news that changes a business's long-term fundamentals, not short-term price moves.

Default to NOISE. Most news is noise. Mark an item FUNDAMENTAL only when it reports a concrete new fact — something that
actually happened or was officially announced/decided — that could change a company's long-term revenue, margins,
competitive position, management or regulatory standing. Examples:
  reported earnings and guidance changes; signed large contracts, customers or orders; announced mergers, acquisitions,
  major investments or partnerships; launched or delayed products that shift competitive position; CEO/CFO changes;
  court rulings, new regulation, antitrust action, export controls or tariffs that hit a core business;
  announced capacity or supply-chain changes; large financing, IPO or buyback decisions.
  Macro (ticker MACRO): only US FOMC rate decisions and minutes, released US CPI/PCE/jobs/GDP data,
  and the Fed Chair's policy statements. Speeches by other Fed officials are low at most.
  Bitcoin (BTCUSD): passed laws or regulator decisions, reported spot ETF flows, major institutional adoption, large hacks.
  Gold (XAUUSD): reported central-bank buying/selling, gold ETF flows, major supply shocks.
NOISE (even when the topic sounds important): opinions, interviews and commentary (including by executives or famous
people); previews of upcoming earnings or data ("what to watch", "ahead of"); analysis, forecasts, explainers;
analyst ratings or price targets; price-move and market-wrap stories; technical/chart analysis; "stocks to buy";
lifestyle, workplace or human-interest stories; non-US central banks unless they directly affect a listed company;
minor product updates or marketing. If your reason would say the item does not change the business, it is NOISE.

For FUNDAMENTAL items also give:
- t: affected tickers from the allowed list (the hint in brackets is a keyword match and may be wrong — correct it).
  Use MACRO for economy-wide news that affects no single listed company.
- d: positive / negative / unclear for the long-term outlook of those tickers (for MACRO: for long-term US growth stocks).
- a: main impact area.
- p: importance. high = changes the business picture (results or guidance, big deal, CEO change, major regulation on a
  core revenue line, FOMC decision). medium = material but not thesis-changing. low = related to fundamentals but small.
- r: one short sentence (max ~20 words) saying what happened and why it matters. The sentence MUST be written in
  {lang}. Inside that sentence, leave company names, product names and financial terms as English words
  (e.g. data center, earnings, guidance, ETF, Treasury yield, FOMC). Get who-did-what right (who is suing whom, etc.).
  Example of the expected style when the language is Thai:
  "Nvidia รายงาน earnings สูงกว่าคาดและปรับ guidance ขึ้น สะท้อนความต้องการ data center ที่ยังแข็งแรง"
- s: if this item reports the same event as an EARLIER fundamental item in the list, that item's number; otherwise -1.

Put every item number in exactly one list: noise or fundamental."""


def label_schema(allowed_tickers, lang):
    return {
        "type": "object",
        "properties": {
            "noise": {"type": "array", "items": {"type": "integer"}},
            "fundamental": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer"},
                        "t": {"type": "array", "items": {"type": "string", "enum": allowed_tickers}},
                        "d": {"type": "string", "enum": ["positive", "negative", "unclear"]},
                        "a": {"type": "string", "enum": IMPACT_AREAS},
                        "p": {"type": "string", "enum": ["high", "medium", "low"]},
                        "r": {"type": "string", "description": f"One sentence written in {lang}"},
                        "s": {"type": "integer"},
                    },
                    "required": ["i", "t", "d", "a", "p", "r", "s"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["noise", "fundamental"],
        "additionalProperties": False,
    }


def label_items(items, config, watchlist):
    """Returns (labels: {item_id: ai_dict}, usage). Raises AIError on failure."""
    names = [f"{s['id']}={s['name']}" for s in watchlist["stocks"]] + [f"{a['id']}={a['name']}" for a in watchlist["assets"]]
    allowed = [s["id"] for s in watchlist["stocks"]] + [a["id"] for a in watchlist["assets"]] + ["MACRO"]
    lines = []
    for n, it in enumerate(items):
        hint = ",".join(it["tickers"]) or "-"
        summ = f" — {it['summary'][:200]}" if it["summary"] else ""
        lines.append(f"[{n}] ({hint}) {it['source']} | {it['title']}{summ}")
    lang = config["ai"]["reason_language"]
    user = ("Allowed tickers: " + "; ".join(names) + "; MACRO=economy-wide\n\nNews:\n" + "\n".join(lines)
            + f"\n\nRemember: write every reason (r) in {lang}.")
    system = LABEL_SYSTEM.replace("{lang}", lang)
    data, usage = _call(config["ai"]["model"], system, user, label_schema(allowed, lang), config["ai"]["max_output_tokens"])

    model = config["ai"]["model"]
    labels = {}
    for n in data.get("noise", []):
        if isinstance(n, int) and 0 <= n < len(items):
            labels[items[n]["id"]] = {"label": "noise", "model": model}
    for f in data.get("fundamental", []):
        n = f.get("i")
        if isinstance(n, int) and 0 <= n < len(items):
            tick = [t for t in f.get("t", []) if t in allowed] or ["MACRO"]
            lab = {"label": "fundamental", "tickers": tick, "direction": f["d"], "impact_area": f["a"],
                   "importance": f["p"], "reason": f["r"].strip(), "model": model}
            s = f.get("s", -1)
            if isinstance(s, int) and 0 <= s < len(items) and s != n:
                lab["same_event_as"] = items[s]["id"]  # history records the event once
            labels[items[n]["id"]] = lab
    if not labels and items:
        raise AIError("bad_output", "no usable labels")
    return labels, usage


# ---------------------------------------------------------------- weekly summary

WEEKLY_SYSTEM = """You write a weekly briefing for a long-term value/growth investor, in {lang}.
Input: this week's news that an earlier step labelled as affecting long-term fundamentals, with direction and reason.
Summarise what actually changed for each company, and for the macro backdrop and Bitcoin/gold.
Be factual and concise; do not give buy/sell advice or price predictions. Skip companies with no fundamental news.
State only facts contained in the input headlines and reasons; never add or reverse details (e.g. who is suing whom).
Every text field MUST be written in {lang}. Inside the {lang} text, leave tickers, company and product names, and
financial terms (earnings, guidance, data center, ETF, Treasury yield, FOMC) as English words."""


def weekly_schema(allowed):
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "overview": {"type": "string"},
            "companies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "string", "enum": allowed},
                        "direction": {"type": "string", "enum": ["positive", "negative", "unclear", "mixed"]},
                        "summary": {"type": "string"},
                    },
                    "required": ["t", "direction", "summary"],
                    "additionalProperties": False,
                },
            },
            "macro": {"type": "string"},
            "crypto_gold": {"type": "string"},
            "watch_next": {"type": "string"},
        },
        "required": ["headline", "overview", "companies", "macro", "crypto_gold", "watch_next"],
        "additionalProperties": False,
    }


def weekly_summary(items, config, watchlist, period_text):
    allowed = [s["id"] for s in watchlist["stocks"]] + [a["id"] for a in watchlist["assets"]]
    names = "; ".join(f"{s['id']}={s['name']}" for s in watchlist["stocks"] + watchlist["assets"])
    lines = []
    for it in items:
        ai = it["ai"]
        lines.append(f"- {it['published'][:10]} [{','.join(ai['tickers'])}] {ai['direction']}/{ai['importance']} "
                     f"{it['title']} ({it['source']}) — {ai['reason']}")
    user = (f"Week: {period_text}\nCompanies: {names}\n\n"
            f"headline: one line. overview: 2-4 sentences. companies: one entry per company with news. "
            f"macro / crypto_gold / watch_next: 1-3 sentences each (empty string if nothing).\n\n"
            f"Fundamental news this week ({len(items)} items):\n" + "\n".join(lines)
            + f"\n\nRemember: write every text field in {config['ai']['reason_language']}.")
    system = WEEKLY_SYSTEM.replace("{lang}", config["ai"]["reason_language"])
    # Sonnet 5 thinks before answering (adaptive thinking is on by default); thinking counts toward max_tokens.
    data, usage = _call(config["ai"]["weekly_model"], system, user, weekly_schema(allowed), 16000)
    if not data.get("overview") and not data.get("companies"):
        raise AIError("bad_output", "empty summary")
    return data, usage
