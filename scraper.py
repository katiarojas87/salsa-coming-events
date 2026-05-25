#!/usr/bin/env python3
"""
scraper.py
==========
Main coordinator. Runs both scrapers and combines results.

Uses:
  - salsalovers_scraper.py  → scrapes agenda.salsalovers.be
  - latinworld_scraper.py   → scrapes latinworld.nl

Saves: raw_events_YYYY-MM-DD.json
Next step: run scorer.py

Run:
  python scraper.py
"""

import asyncio
import json
import sys
from datetime import date, timedelta, datetime

from salsalovers_scraper import scrape_salsalovers, get_upcoming_weekend_dates
from latinworld_scraper import scrape_latinworld

NL_DAYS = {4: "Vrijdag", 5: "Zaterdag", 6: "Zondag",
           0: "Maandag", 1: "Dinsdag", 2: "Woensdag", 3: "Donderdag"}
NL_MON  = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"Mei",6:"Jun",
           7:"Jul",8:"Aug",9:"Sep",10:"Okt",11:"Nov",12:"Dec"}


async def main():
    print("=" * 60)
    print("  🕺 Salsa Events Scraper — @kaatsandoval")
    print("=" * 60)

    target_dates = get_upcoming_weekend_dates()
    print(f"\n📅 Weekend: {target_dates[0]} (Fri) → {target_dates[2]} (Sun)")

    # Run both scrapers
    salsa_events = await scrape_salsalovers(target_dates)
    latin_events = await scrape_latinworld(target_dates)

    all_events = salsa_events + latin_events
    print(f"\n📋 Total: {len(all_events)} events "
          f"(SalsaLovers: {len(salsa_events)}, LatinWorld: {len(latin_events)})")

    if not all_events:
        print("\n❌ No events found for this weekend.")
        sys.exit(0)

    # Group by date
    days = {}
    for d in target_dates:
        day_events = [e for e in all_events if e.get('date') == str(d)]
        days[str(d)] = {
            "date":   str(d),
            "label":  f"{NL_DAYS[d.weekday()]} {d.day} {NL_MON[d.month]}",
            "events": day_events,
        }

    output = {
        "generated_at":      datetime.now().isoformat(),
        "instagram_account": "@kaatsandoval",
        "weekend": {
            "friday":   str(target_dates[0]),
            "saturday": str(target_dates[1]),
            "sunday":   str(target_dates[2]),
        },
        "days": list(days.values()),
        "total_events": len(all_events),
    }

    out_file = f"raw_events_{target_dates[0]}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Saved: {out_file}")
    print("\n📊 Summary:")
    for day_data in output["days"]:
        print(f"  {day_data['label']}: {len(day_data['events'])} events")

    print(f"\n✅ Done! Now run: python scorer.py {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
