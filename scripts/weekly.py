"""Weekly summary (GitHub Actions, Sunday 00:00 UTC = 07:00 Thailand).

Summarises the last 7 days of news labelled "fundamental" into weekly/YYYY-MM-DD.json.
If the AI fails, no summary file is written (the app shows "no summary this week") and old summaries stay.
"""
import os
import sys
from datetime import timedelta

from ai import AIError, cost_usd, weekly_summary
from common import BKK, WEEKLY_DIR, iso, load_configs, parse_iso, utcnow, write_json
import storage


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    now = utcnow()
    config, _, watchlist = load_configs()
    today = now.astimezone(BKK)
    date = today.strftime("%Y-%m-%d")
    start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    news = storage.load_news()
    week = [i for i in news.get("items", [])
            if i.get("ai") and i["ai"].get("label") == "fundamental"
            and now - parse_iso(i["first_seen"]) <= timedelta(days=7)]
    week.sort(key=lambda i: ({"high": 0, "medium": 1, "low": 2}[i["ai"]["importance"]], i["published"]))
    attempt = {"date": date, "at": iso(now), "ok": False}

    if not week:
        attempt.update(code="no_news", message="สัปดาห์นี้ไม่มีข่าวที่ AI ติดป้ายว่ากระทบพื้นฐาน จึงไม่มีสรุป")
        print(attempt["message"])
    else:
        try:
            summary, usage = weekly_summary(week, config, watchlist, f"{start} to {end}")
            model = config["ai"]["weekly_model"]
            cost = cost_usd(config, usage, model)
            write_json(os.path.join(WEEKLY_DIR, f"{date}.json"), {
                "date": date, "period_start": start, "period_end": end, "generated": iso(now),
                "news_count": len(week), "model": model, "summary": summary,
            })
            storage.log_usage("weekly", usage, cost, len(week))
            attempt.update(ok=True)
            print(f"Weekly summary saved: weekly/{date}.json ({len(week)} news, ${cost})")
        except AIError as e:
            attempt.update(code=e.code, message=e.message)
            print(f"Weekly summary FAILED ({e.code}): {e.message} {e.detail}")

    storage.apply_retention(config["history_months"], now)
    storage.rebuild_weekly_index(last_attempt=attempt)
    storage.write_expiring(config["history_months"], config["expiry_warning_days"], now)


if __name__ == "__main__":
    main()
