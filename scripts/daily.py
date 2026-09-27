"""Daily run (GitHub Actions, 00:00 UTC = 07:00 Thailand).

fetch all sources -> filter/classify/dedupe -> one AI call to label new items -> save news + history.
A broken source or a failed AI call never stops the run: news is still saved, just without labels.
"""
import sys
from datetime import timedelta

from ai import AIError, cost_usd, label_items
from classify import Classifier, collect
import os

from common import DATA_DIR, iso, load_configs, parse_iso, utcnow, write_json
from fetch import build_jobs, fetch_all
import storage

CATEGORY_ORDER = ["watchlist", "fed", "deals", "products", "crypto_gold", "market"]


def ai_priority(item):
    return min(CATEGORY_ORDER.index(c) for c in item["categories"])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    now = utcnow()
    config, sources, watchlist = load_configs()

    old = storage.load_news()
    existing = storage.prune_news(old.get("items", []), config["news_days"], now)

    results = fetch_all(build_jobs(sources, watchlist))
    new_items, source_status = collect(results, Classifier(config, sources, watchlist), existing, now)

    # Items left unlabelled by an earlier failed AI run get another chance while still fresh.
    retry = [i for i in existing if i.get("ai") is None
             and now - parse_iso(i["first_seen"]) <= timedelta(hours=config["max_news_age_hours"])]
    candidates = sorted(new_items + retry, key=ai_priority)
    cap = config["limits"]["max_items_to_ai_per_day"]
    to_label, over_cap = candidates[:cap], candidates[cap:]

    ai_status = {"ok": False, "sent": len(to_label), "labeled": 0, "over_cap": len(over_cap),
                 "model": config["ai"]["model"]}
    newly_labeled = []
    if to_label:
        try:
            labels, usage = label_items(to_label, config, watchlist)
            for it in to_label:
                if it["id"] in labels:
                    it["ai"] = labels[it["id"]]
                    newly_labeled.append(it)
            cost = cost_usd(config, usage, config["ai"]["model"])
            month_total = storage.log_usage("daily", usage, cost, len(to_label))
            ai_status.update(ok=True, labeled=len(newly_labeled), input_tokens=usage.input_tokens,
                             output_tokens=usage.output_tokens, cost_usd=cost, month_to_date_usd=month_total)
        except AIError as e:
            ai_status.update(code=e.code, message=e.message, detail=e.detail)
    else:
        ai_status.update(ok=True, message="ไม่มีข่าวใหม่ให้ติดป้าย")

    items = sorted(new_items + existing, key=lambda i: i["published"], reverse=True)
    added = storage.add_to_history(newly_labeled)
    months = config["history_months"]
    deleted = storage.apply_retention(months, now)
    storage.rebuild_history_index()
    if deleted:
        storage.rebuild_weekly_index()
    storage.write_expiring(months, config["expiry_warning_days"], now)

    # The app reads this merged list (stocks/ folder + assets) for names, chart links and the Stocks tab.
    write_json(os.path.join(DATA_DIR, "watchlist.json"), {
        "generated": iso(now),
        "stocks": watchlist["stocks"], "assets": watchlist["assets"], "indexes": watchlist.get("indexes", []),
        "errors": watchlist["errors"],
    })

    storage.save_news({
        "generated": iso(now),
        "ai": ai_status,
        "stats": {"new_items": len(new_items), "total_items": len(items), "history_added": added,
                  "sources_ok": sum(s["ok"] for s in source_status), "sources_total": len(source_status),
                  "deleted_old_files": [d.replace("\\", "/").split("/")[-1] for d in deleted]},
        "sources": source_status,
        "categories": sources["categories"],
        "items": items,
    })

    # Log for the GitHub Actions page
    print(f"Stocks: {len(watchlist['stocks'])}")
    for e in watchlist["errors"]:
        print(f"  skipped stock file {e['file']}: {e['error']}")
    print(f"Sources OK: {sum(s['ok'] for s in source_status)}/{len(source_status)}")
    for s in source_status:
        if not s["ok"]:
            print(f"  skipped: {s['name']} ({s['error']})")
    print(f"New items: {len(new_items)} | kept 7 days: {len(items)} | sent to AI: {len(to_label)} (over cap: {len(over_cap)})")
    by_cat = {c: sum(c in i["categories"] for i in new_items) for c in CATEGORY_ORDER}
    print("New by category:", by_cat)
    if ai_status["ok"] and to_label:
        fund = sum(1 for i in newly_labeled if i["ai"]["label"] == "fundamental")
        print(f"AI: labelled {len(newly_labeled)}/{len(to_label)}, fundamental {fund}, "
              f"tokens in/out {ai_status['input_tokens']}/{ai_status['output_tokens']}, cost ${ai_status['cost_usd']}")
    elif not ai_status["ok"]:
        print(f"AI FAILED ({ai_status['code']}): {ai_status['message']} {ai_status.get('detail', '')}")
    print(f"History: +{added} | deleted old files: {len(deleted)}")


if __name__ == "__main__":
    main()
