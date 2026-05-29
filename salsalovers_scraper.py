#!/usr/bin/env python3
"""
salsalovers_scraper.py
======================
Scrapes agenda.salsalovers.be/parties

Strategy:
  1. Load listing page → collect all /parties/{mongoID} URLs
  2. Visit EVERY detail page → extract ALL data from there
     (name, date, time, address, organizer, price, socials)
  3. Filter by upcoming Fri/Sat/Sun + 120km from Antwerp

Run standalone:
  python salsalovers_scraper.py
  → saves salsalovers_raw_YYYY-MM-DD.json

Or import:
  from salsalovers_scraper import scrape_salsalovers, get_upcoming_weekend_dates
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

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ANTWERP_COORDS  = (51.2194, 4.4025)
MAX_DISTANCE_KM = 120
BASE_URL        = "https://agenda.salsalovers.be"
TARGET_URL      = f"{BASE_URL}/parties"

NL_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}
NL_DAYS = {4: "Vrijdag", 5: "Zaterdag", 6: "Zondag",
           0: "Maandag", 1: "Dinsdag", 2: "Woensdag", 3: "Donderdag"}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_upcoming_weekend_dates():
    today   = date.today()
    weekday = today.weekday()
    days_to_fri = (4 - weekday) % 7
    if days_to_fri == 0:
        days_to_fri = 7
    friday = today + timedelta(days=days_to_fri)
    return [friday, friday + timedelta(1), friday + timedelta(2)]


def parse_dutch_date(text: str):
    if not text:
        return None
    t = text.strip().lower()
    # ISO
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # Dutch: "vrijdag 29 mei 2026"
    m = re.search(
        r"(\d{1,2})\s+(" + "|".join(NL_MONTHS) + r")(?:\s+(\d{4}))?", t
    )
    if m:
        try:
            return date(
                int(m.group(3)) if m.group(3) else date.today().year,
                NL_MONTHS[m.group(2)],
                int(m.group(1)),
            )
        except ValueError:
            pass
    return None


def inner_text(html: str) -> str:
    text = re.sub(r'&nbsp;', ' ', html)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

# ─── GEOCODING ────────────────────────────────────────────────────────────────

_geocache: dict = {}
_geo = Nominatim(user_agent="salsalovers_scraper/1.0", timeout=10)

def geocode(location: str):
    key = location.strip().lower()
    if key in _geocache:
        return _geocache[key]
    time.sleep(1.1)
    for query in [f"{location}, Belgium", f"{location}, Netherlands", location]:
        try:
            loc = _geo.geocode(query)
            if loc:
                coords = (loc.latitude, loc.longitude)
                _geocache[key] = coords
                return coords
        except Exception:
            pass
    _geocache[key] = None
    return None


def is_within_range(city: str, address: str = "", coords=None) -> tuple:
    """Returns (bool, float) — (in_range, distance_km)"""
    if coords:
        dist = geodesic(ANTWERP_COORDS, coords).km
        return dist <= MAX_DISTANCE_KM, round(dist, 1)

    city_lower = (city or "").lower()
    addr_lower = (address or "").lower()
    combined   = f"{city_lower} {addr_lower}"

    too_far = ['amsterdam', 'nijmegen', 'arnhem', 'almere', 'zwolle',
               'enschede', 'groningen', 'leusden', 'amersfoort', 'venlo', 'haarlem']
    if any(f in combined for f in too_far):
        return False, 999

    nearby = [
        'antwerp', 'antwerpen', 'gent', 'ghent', 'brussel', 'brussels',
        'mechelen', 'leuven', 'hasselt', 'genk', 'liege', 'luik',
        'brugge', 'bruges', 'kortrijk', 'aalst', 'dendermonde', 'wuustwezel',
        'turnhout', 'herentals', 'mol', 'blankenberge', 'lint', 'assebroek',
        'schepdaal', 'sint-pieters-leeuw', 'sint-niklaas',
        'rotterdam', 'tilburg', 'breda', 'roosendaal', 'bergen op zoom',
        'dordrecht', 'eindhoven', 'helmond', 's-hertogenbosch', 'den bosch',
        'rosmalen', 'middelburg', 'vlissingen', 'goes', 'zierikzee',
        'terneuzen', 'koewacht', 'sprundel', 'gorinchem',
        'delft', 'den haag', 'maastricht', 'nuth', 'leiden', 'utrecht',
    ]
    if any(n in combined for n in nearby):
        return True, 0

    loc = " ".join(filter(None, [address, city])).strip()
    if not loc:
        return True, 0

    gc = geocode(loc) or geocode(city)
    if not gc:
        return True, 0
    dist = geodesic(ANTWERP_COORDS, gc).km
    return dist <= MAX_DISTANCE_KM, round(dist, 1)

# ─── STEP 1: COLLECT URLS FROM LISTING ───────────────────────────────────────

def collect_urls(html: str) -> list:
    """Extract all unique /parties/{mongoID} URLs from listing page."""
    seen = set()
    urls = []
    for m in re.finditer(r'href="(/parties/([a-f0-9]{24}))"', html, re.IGNORECASE):
        party_id = m.group(2)
        if party_id not in seen:
            seen.add(party_id)
            urls.append({
                "id":  party_id,
                "url": f"{BASE_URL}{m.group(1)}",
            })
    print(f"  Found {len(urls)} unique event URLs on listing page")
    return urls

# ─── STEP 2: PARSE DETAIL PAGE ────────────────────────────────────────────────

def parse_detail(html: str, stub: dict) -> dict:
    event = {
        "source":        "SalsaLovers",
        "id":            stub["id"],
        "url":           stub["url"],
        "name":          "",
        "organizer":     "",
        "date":          "",
        "date_text":     "",
        "day":           "",
        "time":          "",
        "address":       "",
        "city":          "",
        "description":   "",
        "price":         "",
        "is_free":       False,
        "facebook_url":  "",
        "instagram_url": "",
        "image_url":     "",
        "coordinates":   None,
        "_distance_km":  0,
    }

    # ── Name ──────────────────────────────────────────────────────────────────
    # From inspect: <div class="c-event__header__title">Merecumbé Latin SunDay</div>
    name_m = re.search(
        r'class="[^"]*c-event__header__title[^"]*"[^>]*>\s*([^<]+?)\s*</div>',
        html, re.IGNORECASE
    )
    if name_m:
        name = name_m.group(1).strip()
        if name and len(name) > 2 and 'salsalovers' not in name.lower():
            event['name'] = name

    # Fallback: JSON-LD name
    if not event['name']:
        for jld_raw in re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        ):
            try:
                data = json.loads(jld_raw.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get('@type') in ('Event', 'DanceEvent', 'SocialEvent'):
                        if item.get('name'):
                            event['name'] = item['name'].strip()
                            break
            except Exception:
                pass

    # ── Date ──────────────────────────────────────────────────────────────────
    date_m = re.search(
        r'(vrijdag|zaterdag|zondag|maandag|dinsdag|woensdag|donderdag)\s+'
        r'(\d{1,2})\s+'
        r'(januari|februari|maart|april|mei|juni|juli|augustus|'
        r'september|oktober|november|december)'
        r'(?:\s+(\d{4}))?',
        html, re.IGNORECASE
    )
    if date_m:
        day_word   = date_m.group(1)
        day_num    = int(date_m.group(2))
        month_word = date_m.group(3).lower()
        year       = int(date_m.group(4)) if date_m.group(4) else date.today().year
        try:
            event_date        = date(year, NL_MONTHS[month_word], day_num)
            event['date']     = str(event_date)
            event['date_text']= f"{day_word} {day_num} {month_word} {year}"
            event['day']      = NL_DAYS.get(event_date.weekday(), "").lower()
        except ValueError:
            pass

    # JSON-LD date fallback
    if not event['date']:
        for jld_raw in re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        ):
            try:
                data = json.loads(jld_raw.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get('startDate'):
                        sd = item['startDate'][:10]
                        parsed = parse_dutch_date(sd)
                        if parsed:
                            event['date']     = str(parsed)
                            event['date_text']= str(parsed)
                            event['day']      = NL_DAYS.get(parsed.weekday(), "").lower()
                        break
            except Exception:
                pass

    # ── Time ──────────────────────────────────────────────────────────────────
    # From inspect, structure is:
    # <div class="c-event__date__doors ...">
    #   <div class="c-event__date-wrapper ...">
    #     <div class="c-event__date__title ...">Deuren:</div>
    #     <div ...> 19u00 </div>           ← start time
    #   </div>
    #   <div class="c-event__date-wrapper ...">
    #     <div class="c-event__date__title ...">Einde:</div>
    #     <div ...>23u00 </div>            ← end time
    #   </div>
    # </div>

    # Step 1: find the Deuren time
    deuren_m = re.search(
        r'Deuren:</div>\s*<div[^>]*>\s*(\d{1,2})u(\d{2})\s*</div>',
        html, re.IGNORECASE
    )
    # Step 2: find the Einde time
    einde_m = re.search(
        r'Einde:</div>\s*<div[^>]*>\s*(\d{1,2})u(\d{2})\s*</div>',
        html, re.IGNORECASE
    )

    if deuren_m and einde_m:
        start = f"{deuren_m.group(1)}:{deuren_m.group(2)}"
        end   = f"{einde_m.group(1)}:{einde_m.group(2)}"
        event['time'] = f"{start} - {end}"
    elif deuren_m:
        event['time'] = f"{deuren_m.group(1)}:{deuren_m.group(2)}"
    elif einde_m:
        event['time'] = f"{einde_m.group(1)}:{einde_m.group(2)}"

    # ── Address ───────────────────────────────────────────────────────────────
    m = re.search(
        r'class="[^"]*c-event__location__address[^"]*"[^>]*>(.*?)</',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        address = inner_text(m.group(1))
        event['address'] = address
        city_m = re.search(
            r'\d{4}\s*[A-Z]{0,2}\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-]{1,25})',
            address
        )
        if city_m:
            event['city'] = city_m.group(1).strip()
        elif ',' in address:
            event['city'] = address.split(',')[-1].strip()

    # JSON-LD address fallback
    if not event['address']:
        for jld_raw in re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        ):
            try:
                data = json.loads(jld_raw.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item.get('location'), dict):
                        addr = item['location'].get('address', {})
                        if isinstance(addr, dict):
                            street = addr.get('streetAddress', '')
                            city   = addr.get('addressLocality', '')
                            event['address'] = f"{street}, {city}".strip(', ')
                            event['city']    = city
                        elif isinstance(addr, str):
                            event['address'] = addr
                        break
            except Exception:
                pass

    # ── Organizer ─────────────────────────────────────────────────────────────
    # From inspect: <div class="c-event__header__sub-title">Georganiseerd door Merecumbe</div>
    org_m = re.search(
        r'c-event__header__sub-title[^>]*>\s*(?:Georganiseerd door\s*)?([^<]+?)\s*</div>',
        html, re.IGNORECASE
    )
    if org_m:
        organizer = org_m.group(1).strip()
        organizer = re.sub(r'^Georganiseerd door\s*', '', organizer, flags=re.IGNORECASE).strip()
        if organizer:
            event['organizer'] = organizer

    # ── Description ───────────────────────────────────────────────────────────
    desc_container = re.search(
        r'class="[^"]*c-event__description[^"]*"[^>]*>([\s\S]{10,5000})',
        html, re.IGNORECASE
    )
    if desc_container:
        block = desc_container.group(1)
        paragraphs = re.findall(r'<p[^>]*>([\s\S]*?)</p>', block, re.IGNORECASE)
        if paragraphs:
            desc = '\n'.join(inner_text(p) for p in paragraphs if inner_text(p)).strip()
            event['description'] = desc[:2000]
            event['is_free'] = bool(re.search(
                r'\bgratis\b|\bfree\b|\bentrada gratis\b', desc, re.IGNORECASE
            ))

    # ── Price ─────────────────────────────────────────────────────────────────
    price_m = re.search(
        r'(?:inkom|entrance|prijs|price|€)\s*[:\-]?\s*([€$]?\s*\d+(?:[,\.]\d+)?)',
        html, re.IGNORECASE
    )
    if price_m:
        event['price'] = price_m.group(0).strip()
    elif event['is_free']:
        event['price'] = 'Gratis'

    # ── Image ─────────────────────────────────────────────────────────────────
    img_m = re.search(
        r'src="(https://strapi\.salsalovers\.be/[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
        html, re.IGNORECASE
    )
    if img_m:
        event['image_url'] = img_m.group(1)

    # ── Facebook ──────────────────────────────────────────────────────────────
    fb_event = re.search(r'href="(https://(?:www\.)?facebook\.com/events/\d+[^"]*)"', html)
    fb_page  = re.search(
        r'href="(https://(?:www\.)?facebook\.com/(?!events|share|sharer|login|dialog)[^"]{3,})"',
        html
    )
    if fb_event:
        event['facebook_url'] = fb_event.group(1)
    elif fb_page:
        event['facebook_url'] = fb_page.group(1)

    # ── Instagram ─────────────────────────────────────────────────────────────
    ig = re.search(r'href="(https://(?:www\.)?instagram\.com/[^"?]+)"', html)
    if ig:
        event['instagram_url'] = ig.group(1)

    # ── Coordinates ───────────────────────────────────────────────────────────
    coord_m = re.search(
        r'"lat(?:itude)?"\s*[=:]\s*"?(-?\d+\.\d+)"?.{0,50}'
        r'"l(?:ng|on)(?:gitude)?"\s*[=:]\s*"?(-?\d+\.\d+)"?',
        html, re.DOTALL | re.IGNORECASE
    )
    if not coord_m:
        coord_m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+),\d+z', html)
    if coord_m:
        event['coordinates'] = [float(coord_m.group(1)), float(coord_m.group(2))]

    return event

# ─── BROWSER CONFIG ───────────────────────────────────────────────────────────

BROWSER_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
]
BROWSER_CTX = dict(
    user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    viewport={"width": 1280, "height": 900},
    locale="nl-BE",
    timezone_id="Europe/Brussels",
    extra_http_headers={"Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8"},
)

# ─── MAIN SCRAPE FUNCTION ─────────────────────────────────────────────────────

async def scrape_salsalovers(target_dates: list) -> list:
    from playwright.async_api import async_playwright

    target_set = set(str(d) for d in target_dates)

    print(f"\n🌐 Loading SalsaLovers listing...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        ctx     = await browser.new_context(**BROWSER_CTX)
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await ctx.new_page()

        try:
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=45000)
        except Exception as e:
            print(f"  ⚠️  Timeout (normal for Cloudflare): {e}")

        # Scroll to load all events
        prev = 0
        for _ in range(12):
            await page.keyboard.press("End")
            await asyncio.sleep(1.2)
            h = await page.evaluate("document.body.scrollHeight")
            if h == prev:
                break
            prev = h

        html = await page.content()
        print(f"  HTML size: {len(html):,} chars")

        stubs = collect_urls(html)
        if not stubs:
            await browser.close()
            return []

        # Visit every detail page — filter by date after parsing
        sem = asyncio.Semaphore(4)

        async def fetch_detail(stub):
            async with sem:
                dp = await ctx.new_page()
                try:
                    await dp.goto(stub['url'], wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(random.uniform(0.4, 0.8))
                    detail_html = await dp.content()
                    return parse_detail(detail_html, stub)
                except Exception as e:
                    print(f"  ⚠️  {stub['id']}: {e}")
                    return None
                finally:
                    await dp.close()

        print(f"  Fetching {len(stubs)} detail pages...")
        results = await asyncio.gather(*[fetch_detail(s) for s in stubs])
        await browser.close()

    # Filter by date
    weekend_events = []
    for e in results:
        if e and e.get('date') in target_set:
            weekend_events.append(e)

    print(f"  {len(weekend_events)} events match the target weekend")

    # Filter by distance
    kept = []
    print(f"\n📍 Distance filter (SalsaLovers)...")
    for e in weekend_events:
        in_range, dist = is_within_range(
            e.get('city', ''), e.get('address', ''), e.get('coordinates')
        )
        status = "✅" if in_range else "❌"
        print(f"  {status} {e.get('name','?')[:45]} @ {e.get('city','')} → {dist}km")
        if in_range:
            kept.append({**e, "_distance_km": dist})

    print(f"  → {len(kept)} SalsaLovers events kept")
    return kept

# ─── STANDALONE RUN ───────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  💃 SalsaLovers Scraper — @kaatsandoval")
    print("=" * 60)

    target_dates = get_upcoming_weekend_dates()
    print(f"\n📅 Weekend: {target_dates[0]} → {target_dates[2]}")

    events = await scrape_salsalovers(target_dates)

    if not events:
        print("\n❌ No events found.")
        sys.exit(0)

    NL_MON = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"Mei",6:"Jun",
              7:"Jul",8:"Aug",9:"Sep",10:"Okt",11:"Nov",12:"Dec"}
    days = {}
    for d in target_dates:
        day_events = [e for e in events if e['date'] == str(d)]
        days[str(d)] = {
            "date":   str(d),
            "label":  f"{NL_DAYS[d.weekday()]} {d.day} {NL_MON[d.month]}",
            "events": day_events,
        }

    output = {
        "generated_at": datetime.now().isoformat(),
        "source":       "SalsaLovers",
        "weekend": {
            "friday":   str(target_dates[0]),
            "saturday": str(target_dates[1]),
            "sunday":   str(target_dates[2]),
        },
        "days":         list(days.values()),
        "total_events": len(events),
    }

    out_file = f"salsalovers_raw_{target_dates[0]}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Saved: {out_file}")
    print("\n📊 Summary:")
    for day_data in output["days"]:
        print(f"  {day_data['label']}: {len(day_data['events'])} events")
        for e in day_data["events"]:
            print(f"    - {e['name']} @ {e.get('city','')} {e['time']}")

    print("\n✅ Done!")


if __name__ == "__main__":
    asyncio.run(main())
