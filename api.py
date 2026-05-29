#!/usr/bin/env python3
"""
api.py — FastAPI server for n8n integration
Exposes scraper.py and scorer.py as HTTP endpoints

Run:  uvicorn api:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import asyncio, json, os, re
from datetime import datetime
import anthropic

# Import your existing scrapers
from salsalovers_scraper import scrape_salsalovers, get_upcoming_weekend_dates
from latinworld_scraper import scrape_latinworld

app = FastAPI(title="Salsa Events API", version="1.0")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── HEALTH CHECK ─────────────────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "ok", "service": "Salsa Events API"}

# ── SCRAPE ENDPOINT ──────────────────────────────────────────────────────
@app.post("/scrape")
async def scrape():
    """Run both scrapers and return combined raw events."""
    try:
        target_dates = get_upcoming_weekend_dates()
        salsa = await scrape_salsalovers(target_dates)
        latin = await scrape_latinworld(target_dates)
        all_events = salsa + latin

        NL_DAYS = {4:"Vrijdag",5:"Zaterdag",6:"Zondag",
                   0:"Maandag",1:"Dinsdag",2:"Woensdag",3:"Donderdag"}
        NL_MON  = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"Mei",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Okt",11:"Nov",12:"Dec"}

        days = {}
        for d in target_dates:
            day_events = [e for e in all_events if e.get("date") == str(d)]
            days[str(d)] = {
                "date":   str(d),
                "label":  f"{NL_DAYS[d.weekday()]} {d.day} {NL_MON[d.month]}",
                "events": day_events,
            }

        return {
            "generated_at": datetime.now().isoformat(),
            "weekend": {
                "friday":   str(target_dates[0]),
                "saturday": str(target_dates[1]),
                "sunday":   str(target_dates[2]),
            },
            "days": list(days.values()),
            "total_events": len(all_events),
            "salsalovers_count": len(salsa),
            "latinworld_count": len(latin),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── SCORE ENDPOINT ───────────────────────────────────────────────────────
@app.post("/score")
async def score(raw: dict):
    """Receive raw events JSON, call Claude, return ranked top 3 per day."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    days   = raw.get("days", [])
    weekend= raw.get("weekend", {})

    sections = []
    for day in days:
        label  = day["label"]
        events = day["events"]
        if not events:
            sections.append(f"# {label}\nNo events.")
            continue
        lines = []
        for i, e in enumerate(events, 1):
            lines.append(
                f"{i}. {e.get('name','')}\n"
                f"   Organizer: {e.get('organizer','')}\n"
                f"   DJs: {e.get('djs','')}\n"
                f"   Time: {e.get('time','')}\n"
                f"   City: {e.get('city','')}\n"
                f"   Address: {e.get('address','')}\n"
                f"   Price: {e.get('price','')}\n"
                f"   Music: {e.get('music_genres','')}\n"
                f"   Description: {str(e.get('description',''))[:200]}\n"
                f"   Facebook: {e.get('facebook_url','')}\n"
                f"   Instagram: {e.get('instagram_url','')}\n"
                f"   Source: {e.get('source','')}\n"
                f"   URL: {e.get('url','')}"
            )
        sections.append(f"# {label}\n" + "\n".join(lines))

    events_text = "\n\n".join(sections)

    prompt = f"""You are an expert salsa social scene analyst for Belgium and the Netherlands.

Here are the events for the upcoming weekend ({weekend.get("friday","?")} to {weekend.get("sunday","?")}):

{events_text}

-----------------------------------
EXCLUSION RULES
-----------------------------------

BEFORE scoring, remove any event that is 100% bachata-only.
This feed is for salsa and mixed Latin events (salsa, SBK, kizomba, mixed).
- EXCLUDE events where the program contains ONLY "Bachata" with no salsa or kizomba.
- EXCLUDE events with names like "Bachata Gala", "Bachata Dreams", "Bachata Only".
- Events may be in Dutch, French, or English — apply exclusion rules regardless of language.
  Examples of French bachata-only events to exclude: "Soirée Bachata", "Gala Bachata", "100% Bachata".
- If fewer than 3 valid events remain for a day, include the best available.

-----------------------------------
SCORING MODEL
-----------------------------------

Use this weighted popularity framework:

Final Score =
    Facebook Attendees * 0.40
  + Instagram Followers * 0.25
  + Organizer Reputation * 0.15
  + Venue Prestige * 0.10
  + Event Frequency / Recurring Reputation * 0.05
  + Google / Social Signals * 0.05

-----------------------------------
EVALUATION CRITERIA
-----------------------------------

1. Facebook Attendees (Highest Importance)
Evaluate:
- Going count
- Interested count
- Facebook event engagement
- comments/shares/activity

Interpretation:
- "Going" is stronger than "Interested"
- Events with active discussion are stronger
- Sold-out or crowded reputation matters

2. Instagram Followers
Evaluate:
- organizer Instagram
- venue Instagram
- event-specific account
- engagement quality (not just follower count)

Interpretation:
- strong salsa communities matter more than generic nightlife accounts
- local dance influence matters strongly

3. Organizer Reputation
Determine:
- whether organizer is well-known in Belgium or Netherlands Latin scene
- recurring reputation
- dancer trust
- teacher reputation
- DJ reputation
- community prestige

Examples:
- famous social brands
- respected dance schools
- iconic DJs
- known festival organizers

4. Venue Prestige
Evaluate:
- whether venue is iconic or premium
- whether venue historically hosts important socials
- size and atmosphere
- rooftop / waterfront / historic locations
- known dance quality

Examples:
- MAS Antwerp
- famous Amsterdam socials
- prestigious dance venues

5. Event Frequency / Recurring Strength
Evaluate:
- weekly recurring events
- established monthly socials
- long-running parties
- events with loyal communities

Interpretation:
- recurring events often outperform random one-offs

6. Google / Social Signals
Evaluate:
- mentions online
- reposts
- dance community hype
- visibility across platforms
- search popularity
- tagged content
- reels/videos/photos

-----------------------------------
IMPORTANT REASONING RULES
-----------------------------------

You MUST think like a real salsa dancer in Belgium and the Netherlands.
Do NOT rank only by numbers.

LANGUAGE: Events may be described in Dutch, French, or English.
You must read and evaluate all events regardless of language.
French-language events from Belgium (Brussels, Wallonia) are equally
important and prestigious as Dutch-language events. Do not underrank
an event simply because its description is in French.
The target audience is salsa dancers — rank events by their relevance
and popularity within the salsa community specifically.

You should infer:
- prestige
- hype
- exclusivity
- community importance
- dancer excitement
- social visibility

Some smaller events may outrank larger events if they are considered:
- elite
- highly respected
- famous within the dance scene
- culturally important

You should also consider:
- famous DJs
- live bands
- workshops included
- special editions
- rooftop events
- outdoor summer socials
- festival pre-parties
- anniversary editions

-----------------------------------
OUTPUT FORMAT
-----------------------------------

Respond ONLY with valid JSON — no markdown, no explanation:
{{
  "friday":   [top 3 events],
  "saturday": [top 3 events],
  "sunday":   [top 3 events]
}}

Each event object:
{{ "rank":1, "name":"", "organizer":"", "djs":"", "time":"", "city":"",
   "address":"", "price":"", "program":"Salsa · Bachata · SBK",
   "description":"", "score":85, "why":"reason",
   "facebook_url":"", "instagram_url":"", "image_url":"", "url":"", "source":"" }}

Include TOP 3 per day. If fewer than 3 exist, include all available."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        messages=[{"role":"user","content":prompt}]
    )

    text = message.content[0].text
    clean = re.sub(r"```(?:json)?\s*", "", text).strip()
    clean = re.sub(r"```\s*$", "", clean).strip()

    try:
        ranked = json.loads(clean)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500,
            detail=f"Claude returned invalid JSON: {str(e)}. Raw: {text[:300]}")

    return {
        "generated_at": datetime.now().isoformat(),
        "weekend": weekend,
        "ranked_events": ranked,
    }
