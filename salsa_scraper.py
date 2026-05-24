#!/usr/bin/env python3
"""
Salsa Lovers Agenda Scraper
============================
Scrapes agenda.salsalovers.be/parties using a real browser (Playwright)
to bypass bot detection, filters events within 120km of Antwerp for the
upcoming Fri/Sat/Sun, ranks them by Facebook/Instagram popularity,
and outputs structured JSON ready for Instagram carousel generation.

Run:   python salsa_scraper.py
Deps:  pip install playwright geopy requests
       playwright install chromium
"""

import asyncio
import json
import re
import sys
import time
import random
from datetime import date, timedelta, datetime
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
import requests

# ─── CONFIG ────────────────────────────────────────────────────────────────────
ANTWERP_COORDS = (51.2194, 4.4025)
MAX_DISTANCE_KM = 120
TARGET_URL = "https://agenda.salsalovers.be/parties"

# ─── DATE HELPERS ──────────────────────────────────────────────────────────────

def get_upcoming_weekend_dates() -> list[date]:
    """
    Returns [Friday, Saturday, Sunday] of the upcoming weekend.
    If today is Wed/Thu, returns the same week's weekend.
    Otherwise returns next week's.
    """
    today = date.today()
    weekday = today.weekday()  # Mon=0 … Sun=6

    # days until next Friday
    days_to_friday = (4 - weekday) % 7
    if days_to_friday == 0 and weekday > 4:  # already past Friday
        days_to_friday = 7
    if days_to_friday == 0:
        days_to_friday = 7  # next Friday, not today

    # If today is Wednesday or Thursday → this coming weekend
    if weekday in (2, 3):
        days_to_friday = (4 - weekday) % 7

    friday = today + timedelta(days=days_to_friday)
    return [friday, friday + timedelta(1), friday + timedelta(2)]

# ─── GEOCODING ─────────────────────────────────────────────────────────────────

_geocache: dict[str, tuple] = {}
_geolocator = Nominatim(user_agent="salsa_scraper_kaatsandoval/1.0")

def geocode_venue(location_text: str) -> tuple | None:
    """Returns (lat, lon) or None."""
    key = location_text.strip().lower()
    if key in _geocache:
        return _geocache[key]
    try:
        time.sleep(1)  # Nominatim rate limit
        loc = _geolocator.geocode(location_text + ", Belgium", timeout=10)
        if loc:
            coords = (loc.latitude, loc.longitude)
            _geocache[key] = coords
            return coords
        # Try without "Belgium"
        loc = _geolocator.geocode(location_text, timeout=10)
        if loc:
            coords = (loc.latitude, loc.longitude)
            _geocache[key] = coords
            return coords
    except Exception as e:
        print(f"  ⚠️  Geocoding failed for '{location_text}': {e}")
    return None

def is_within_range(location_text: str) -> bool:
    """Returns True if the location is within MAX_DISTANCE_KM of Antwerp."""
    coords = geocode_venue(location_text)
    if not coords:
        return True  # Keep if we can't determine (don't filter wrongly)
    dist = geodesic(ANTWERP_COORDS, coords).km
    print(f"  📍 {location_text} → {dist:.0f} km from Antwerp")
    return dist <= MAX_DISTANCE_KM

# ─── BROWSER SCRAPER ───────────────────────────────────────────────────────────

async def scrape_parties(target_dates: list[date]) -> list[dict]:
    """
    Opens agenda.salsalovers.be/parties in a stealth Chromium browser,
    waits for the React/Vue app to render, then extracts all event cards.
    """
    from playwright.async_api import async_playwright

    date_strs = {d.strftime("%Y-%m-%d") for d in target_dates}
    # Also Dutch month names for fallback matching
    nl_months = {
        1: "januari", 2: "februari", 3: "maart", 4: "april",
        5: "mei", 6: "juni", 7: "juli", 8: "augustus",
        9: "september", 10: "oktober", 11: "november", 12: "december"
    }
    date_labels = set()
    for d in target_dates:
        date_labels.add(f"{d.day} {nl_months[d.month]}")
        date_labels.add(d.strftime("%-d %B").lower())  # e.g. "29 May"

    all_events = []

    async with async_playwright() as p:
        print("🌐 Launching stealth browser...")
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,900",
            ]
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="nl-BE",
            timezone_id="Europe/Brussels",
            extra_http_headers={
                "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )

        # Remove webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = {runtime: {}};"
        )

        page = await context.new_page()

        # Capture XHR/fetch API calls — the SPA likely fetches from an internal API
        api_data = []

        async def on_response(response):
            ct = response.headers.get("content-type", "")
            if "json" in ct and "salsalovers" in response.url:
                try:
                    body = await response.json()
                    api_data.append({"url": response.url, "data": body})
                    print(f"  🔌 API call captured: {response.url[:80]}")
                except Exception:
                    pass

        page.on("response", on_response)

        print(f"📡 Navigating to {TARGET_URL} ...")
        try:
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=45000)
        except Exception as e:
            print(f"  ⚠️  Navigation warning: {e}")

        # Human-like delay
        await asyncio.sleep(random.uniform(2, 4))

        # Scroll down to trigger lazy loading
        for _ in range(5):
            await page.keyboard.press("End")
            await asyncio.sleep(random.uniform(0.8, 1.5))

        # ── Try to extract from captured API calls first ──────────────────────
        if api_data:
            print(f"✅ Got {len(api_data)} API responses — parsing directly")
            for api_resp in api_data:
                data = api_resp["data"]
                # Handle array or paginated response
                items = data if isinstance(data, list) else data.get("parties", data.get("data", data.get("items", [])))
                if isinstance(items, list):
                    for item in items:
                        event = parse_api_event(item)
                        if event:
                            all_events.append(event)

        # ── Fallback: parse rendered HTML ─────────────────────────────────────
        if not all_events:
            print("🔍 No API data — parsing rendered HTML...")
            html = await page.content()
            all_events = parse_html_events(html)

        await browser.close()

    print(f"\n📋 Total events found on page: {len(all_events)}")
    return all_events


def parse_api_event(item: dict) -> dict | None:
    """Parse a single event object from the JSON API response."""
    try:
        return {
            "id": str(item.get("_id", item.get("id", ""))),
            "name": item.get("name", item.get("title", "")),
            "date": item.get("date", item.get("startDate", item.get("eventDate", ""))),
            "time": item.get("time", item.get("startTime", "")),
            "location": item.get("location", item.get("venue", item.get("city", ""))),
            "city": item.get("city", ""),
            "organizer": item.get("organizer", item.get("organization", "")),
            "description": item.get("description", "")[:300],
            "facebook_url": item.get("facebookUrl", item.get("facebook", "")),
            "instagram_url": item.get("instagramUrl", item.get("instagram", "")),
            "ticket_url": item.get("ticketUrl", item.get("tickets", "")),
            "image_url": item.get("imageUrl", item.get("image", item.get("photo", ""))),
            "price": item.get("price", item.get("entrance", "")),
            "url": f"https://agenda.salsalovers.be/parties/{item.get('_id', '')}",
        }
    except Exception:
        return None


def parse_html_events(html: str) -> list[dict]:
    """
    Parse event cards from the rendered HTML.
    salsalovers.be uses a Vue/React SPA — event cards typically have
    date, title, location in structured divs or JSON-LD.
    """
    events = []

    # Try JSON-LD structured data first
    json_ld_blocks = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
    for block in json_ld_blocks:
        try:
            data = json.loads(block.strip())
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") in ("Event", "DanceEvent", "SocialEvent"):
                        events.append({
                            "name": item.get("name", ""),
                            "date": item.get("startDate", ""),
                            "location": item.get("location", {}).get("name", "") if isinstance(item.get("location"), dict) else str(item.get("location", "")),
                            "city": item.get("location", {}).get("address", {}).get("addressLocality", "") if isinstance(item.get("location"), dict) else "",
                            "description": item.get("description", "")[:300],
                            "url": item.get("url", ""),
                            "image_url": item.get("image", ""),
                            "organizer": item.get("organizer", {}).get("name", "") if isinstance(item.get("organizer"), dict) else "",
                        })
            elif isinstance(data, dict) and data.get("@type") in ("Event", "DanceEvent"):
                events.append({
                    "name": data.get("name", ""),
                    "date": data.get("startDate", ""),
                    "location": data.get("location", {}).get("name", "") if isinstance(data.get("location"), dict) else "",
                    "city": "",
                    "description": data.get("description", "")[:300],
                    "url": data.get("url", ""),
                    "image_url": data.get("image", ""),
                    "organizer": "",
                })
        except json.JSONDecodeError:
            continue

    # Try embedded __NUXT_DATA__ or __NEXT_DATA__ (common in SPA frameworks)
    for pattern in [r'__NUXT_DATA__\s*=\s*(\[.*?\]);', r'__NEXT_DATA__\s*=\s*({.*?})\s*</script>']:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                print(f"  Found SPA state data ({len(str(data))} chars)")
                # Recursively search for event-like objects
                extracted = extract_events_from_state(data)
                events.extend(extracted)
            except Exception:
                pass

    # Last resort: look for visible text patterns
    if not events:
        # Pattern: date + title + location in consecutive elements
        # Dutch date pattern: "vrijdag 30 mei" or "29 mei"
        date_pattern = re.compile(
            r'(vrijdag|zaterdag|zondag)\s+(\d{1,2})\s+(januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)',
            re.IGNORECASE
        )
        for match in date_pattern.finditer(html):
            start = match.start()
            snippet = html[max(0, start-50):start+500]
            # Try to extract title and location from surrounding text
            clean = re.sub(r'<[^>]+>', ' ', snippet)
            clean = re.sub(r'\s+', ' ', clean).strip()
            events.append({
                "name": clean[:100],
                "date": match.group(0),
                "location": "",
                "city": "",
                "description": clean,
                "url": "",
                "image_url": "",
                "organizer": "",
            })

    return events


def extract_events_from_state(data, depth=0) -> list[dict]:
    """Recursively search SPA state for event-like objects."""
    events = []
    if depth > 10:
        return events
    if isinstance(data, dict):
        if any(k in data for k in ("eventDate", "startDate", "partyDate")):
            events.append({
                "name": data.get("name", data.get("title", "")),
                "date": data.get("eventDate", data.get("startDate", data.get("partyDate", ""))),
                "location": data.get("location", data.get("venue", data.get("city", ""))),
                "city": data.get("city", ""),
                "description": data.get("description", "")[:300],
                "url": data.get("url", ""),
                "image_url": data.get("image", data.get("imageUrl", data.get("photo", ""))),
                "organizer": data.get("organizer", ""),
                "facebook_url": data.get("facebookUrl", data.get("facebook", "")),
            })
        for v in data.values():
            events.extend(extract_events_from_state(v, depth + 1))
    elif isinstance(data, list):
        for item in data:
            events.extend(extract_events_from_state(item, depth + 1))
    return events

# ─── FILTERING ─────────────────────────────────────────────────────────────────

def normalize_date(raw_date: str, target_dates: list[date]) -> date | None:
    """
    Try to parse a raw date string and match it to one of our target dates.
    Handles ISO dates, Dutch text dates, and partial dates.
    """
    if not raw_date:
        return None

    nl_months = {
        "januari": 1, "februari": 2, "maart": 3, "april": 4,
        "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
        "september": 9, "oktober": 10, "november": 11, "december": 12,
        # English fallback
        "january": 1, "february": 2, "march": 3, "may": 5, "june": 6,
        "july": 7, "august": 8, "october": 10,
    }

    raw = raw_date.strip().lower()

    # ISO format: 2026-05-29 or 2026-05-29T20:00:00
    iso_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        try:
            return date(y, m, d)
        except ValueError:
            pass

    # Dutch: "vrijdag 29 mei" or "29 mei" or "zaterdag 31 mei 2026"
    dutch_match = re.search(r"(\d{1,2})\s+(" + "|".join(nl_months.keys()) + r")(?:\s+(\d{4}))?", raw)
    if dutch_match:
        day = int(dutch_match.group(1))
        month = nl_months[dutch_match.group(2)]
        year = int(dutch_match.group(3)) if dutch_match.group(3) else date.today().year
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # Check if raw string directly contains a target date
    for td in target_dates:
        if td.strftime("%Y-%m-%d") in raw or td.strftime("%d/%m/%Y") in raw:
            return td

    return None


def filter_events(events: list[dict], target_dates: list[date]) -> dict[date, list[dict]]:
    """
    Filter events by:
    1. Date matches one of our target Fri/Sat/Sun
    2. Location within 120km of Antwerp
    Returns a dict grouped by date.
    """
    grouped: dict[date, list[dict]] = {d: [] for d in target_dates}
    seen_ids = set()

    print(f"\n🗓️  Filtering {len(events)} events for dates: {[str(d) for d in target_dates]}")

    for event in events:
        # Dedup
        eid = event.get("id") or event.get("name", "") + event.get("date", "")
        if eid in seen_ids:
            continue
        seen_ids.add(eid)

        # Date filter
        raw_date = event.get("date", "")
        event_date = normalize_date(raw_date, target_dates)
        if event_date not in grouped:
            continue

        # Location filter
        location = event.get("location", event.get("city", ""))
        city = event.get("city", "")
        location_str = f"{location} {city}".strip() or location or city
        if not location_str:
            print(f"  ⚠️  No location for '{event.get('name', '?')}' — keeping")
            grouped[event_date].append(event)
            continue

        print(f"\n  Checking: {event.get('name', '?')} @ {location_str}")
        if is_within_range(location_str):
            grouped[event_date].append(event)
        else:
            print(f"  ❌ Too far (>{MAX_DISTANCE_KM}km) — skipping")

    return grouped

# ─── POPULARITY RANKING ────────────────────────────────────────────────────────

def get_facebook_page_followers(fb_url: str) -> int:
    """
    Fetch approximate Facebook follower count via the public page.
    Uses a lightweight HTML scrape (no API key needed for public pages).
    """
    if not fb_url:
        return 0
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(fb_url, headers=headers, timeout=10, allow_redirects=True)
        html = resp.text

        # Look for follower count patterns in the HTML
        patterns = [
            r'"follower_count":\s*(\d+)',
            r'(\d[\d,]+)\s*(?:followers|volgers)',
            r'"page_likers":\{"count":(\d+)',
            r'(\d[\d,.]+)K?\s*(?:likes|vind-ik-leuks)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                raw = match.group(1).replace(",", "").replace(".", "")
                return int(raw)
    except Exception as e:
        print(f"  ⚠️  FB followers fetch failed for {fb_url}: {e}")
    return 0


def get_instagram_followers(ig_handle: str) -> int:
    """
    Fetch Instagram follower count from public profile page.
    """
    if not ig_handle:
        return 0
    handle = ig_handle.lstrip("@").strip("/").split("/")[-1]
    if not handle:
        return 0
    try:
        url = f"https://www.instagram.com/{handle}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        html = resp.text

        # Meta tag or JSON embed
        patterns = [
            r'"edge_followed_by":\{"count":(\d+)',
            r'"followers":(\d+)',
            r'(\d[\d,]+)\s*Followers',
            r'"follower_count":(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return int(match.group(1).replace(",", ""))
    except Exception as e:
        print(f"  ⚠️  IG followers fetch failed for @{handle}: {e}")
    return 0


def get_facebook_event_attendees(fb_event_url: str) -> int:
    """Fetch 'going' count from a Facebook event page."""
    if not fb_event_url or "events" not in fb_event_url:
        return 0
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(fb_event_url, headers=headers, timeout=10)
        html = resp.text
        patterns = [
            r'"going_count":(\d+)',
            r'(\d[\d,]+)\s*(?:going|gaan)',
            r'"attendee_count":(\d+)',
            r'"interestedCount":(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return int(match.group(1).replace(",", ""))
    except Exception as e:
        print(f"  ⚠️  FB event attendees fetch failed: {e}")
    return 0


def compute_popularity_score(event: dict) -> dict:
    """
    Compute a popularity score for an event.
    Score = max(fb_page_followers, ig_followers) + fb_event_attendees * 10
    Returns the event dict with popularity fields added.
    """
    print(f"\n  📊 Scoring: {event.get('name', '?')}")

    fb_url = event.get("facebook_url", "")
    ig_handle = event.get("instagram_url", event.get("instagram_handle", ""))
    fb_event_url = event.get("facebook_event_url", fb_url if "events" in (fb_url or "") else "")

    fb_followers = get_facebook_page_followers(fb_url) if fb_url and "events" not in fb_url else 0
    ig_followers = get_instagram_followers(ig_handle) if ig_handle else 0
    fb_attendees = get_facebook_event_attendees(fb_event_url) if fb_event_url else 0

    # Also check organizer page
    organizer_ig = event.get("organizer_instagram", "")
    if organizer_ig and not ig_followers:
        ig_followers = get_instagram_followers(organizer_ig)

    score = max(fb_followers, ig_followers) + fb_attendees * 10

    print(f"    FB followers: {fb_followers:,} | IG followers: {ig_followers:,} | FB attendees: {fb_attendees:,} → score: {score:,}")

    return {
        **event,
        "fb_followers": fb_followers,
        "ig_followers": ig_followers,
        "fb_attendees": fb_attendees,
        "popularity_score": score,
    }


def rank_events(grouped: dict[date, list[dict]]) -> dict[date, list[dict]]:
    """Score and rank events per date, return top 3 per day."""
    ranked = {}
    for day, events in grouped.items():
        if not events:
            ranked[day] = []
            continue
        print(f"\n🏆 Ranking {len(events)} events for {day}...")
        scored = [compute_popularity_score(e) for e in events]
        scored.sort(key=lambda e: e["popularity_score"], reverse=True)
        ranked[day] = scored[:3]
    return ranked

# ─── OUTPUT FORMATTING ─────────────────────────────────────────────────────────

def format_output(ranked: dict[date, list[dict]], target_dates: list[date]) -> dict:
    """Format the final output for Instagram carousel generation."""
    nl_days = {
        4: "Vrijdag", 5: "Zaterdag", 6: "Zondag",
        0: "Maandag", 1: "Dinsdag", 2: "Woensdag", 3: "Donderdag"
    }
    nl_months_short = {
        1: "jan", 2: "feb", 3: "mrt", 4: "apr", 5: "mei", 6: "jun",
        7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec"
    }

    output = {
        "generated_at": datetime.now().isoformat(),
        "weekend": {
            "friday": str(target_dates[0]),
            "saturday": str(target_dates[1]),
            "sunday": str(target_dates[2]),
        },
        "instagram_account": "@kaatsandoval",
        "days": []
    }

    for day in target_dates:
        events = ranked.get(day, [])
        day_label = f"{nl_days[day.weekday()]} {day.day} {nl_months_short[day.month].capitalize()}"

        day_data = {
            "date": str(day),
            "label": day_label,
            "top_events": []
        }

        for rank, event in enumerate(events, 1):
            day_data["top_events"].append({
                "rank": rank,
                "name": event.get("name", ""),
                "time": event.get("time", ""),
                "location": event.get("location", ""),
                "city": event.get("city", ""),
                "organizer": event.get("organizer", ""),
                "price": event.get("price", ""),
                "description": event.get("description", "")[:200],
                "url": event.get("url", ""),
                "facebook_url": event.get("facebook_url", ""),
                "instagram_url": event.get("instagram_url", ""),
                "image_url": event.get("image_url", ""),
                "popularity": {
                    "score": event.get("popularity_score", 0),
                    "fb_page_followers": event.get("fb_followers", 0),
                    "ig_followers": event.get("ig_followers", 0),
                    "fb_event_attendees": event.get("fb_attendees", 0),
                }
            })

        output["days"].append(day_data)

    return output

# ─── MAIN ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  🕺 Salsa Lovers Agenda Scraper — @kaatsandoval")
    print("=" * 60)

    # 1. Calculate target dates
    target_dates = get_upcoming_weekend_dates()
    print(f"\n📅 Target weekend: {target_dates[0]} (Fri) → {target_dates[2]} (Sun)")

    # 2. Scrape
    all_events = await scrape_parties(target_dates)

    if not all_events:
        print("\n⚠️  No events found. The site may require a Belgian/residential IP.")
        print("   Run this script from your local machine or a VPS in Belgium.")
        sys.exit(1)

    # 3. Filter by date and location
    grouped = filter_events(all_events, target_dates)

    total_filtered = sum(len(v) for v in grouped.values())
    print(f"\n✅ Events after filtering: {total_filtered}")
    for d, events in grouped.items():
        print(f"   {d}: {len(events)} events")

    # 4. Rank by popularity
    ranked = rank_events(grouped)

    # 5. Format output
    output = format_output(ranked, target_dates)

    # 6. Save JSON
    out_path = f"salsa_events_{target_dates[0].strftime('%Y-%m-%d')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Results saved to: {out_path}")

    # 7. Pretty print summary
    print("\n" + "=" * 60)
    print("  📱 WEEKEND SALSA EVENTS — TOP 3 PER DAY")
    print("=" * 60)

    for day_data in output["days"]:
        print(f"\n📅 {day_data['label'].upper()}")
        if not day_data["top_events"]:
            print("   No events found within 120km of Antwerp")
        for e in day_data["top_events"]:
            score = e["popularity"]["score"]
            print(f"\n  #{e['rank']} {e['name']}")
            print(f"     📍 {e['location']} {e['city']}")
            print(f"     ⏰ {e['time']}")
            print(f"     🎫 {e['price']}")
            print(f"     📊 Popularity score: {score:,}")
            if e["popularity"]["fb_page_followers"]:
                print(f"        FB followers: {e['popularity']['fb_page_followers']:,}")
            if e["popularity"]["ig_followers"]:
                print(f"        IG followers: {e['popularity']['ig_followers']:,}")
            if e["popularity"]["fb_event_attendees"]:
                print(f"        FB event going: {e['popularity']['fb_event_attendees']:,}")

    print("\n✅ Done! Use the JSON output to generate your Instagram carousel.")
    return output


if __name__ == "__main__":
    asyncio.run(main())
