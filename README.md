# Salsa Lovers Scraper 🕺

Scrapes **agenda.salsalovers.be/parties** using a real Chromium browser,
filters events within **120km of Antwerp** for the upcoming **Fri/Sat/Sun**,
and ranks them by Facebook/Instagram popularity.

## Why Playwright (not requests)?

The site returns `403 Host not in allowlist` to plain HTTP requests.
Playwright launches a real Chromium browser that renders the JavaScript SPA
and behaves like a human visitor — bypassing bot detection.

## Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install the Chromium browser (one-time)
playwright install chromium

# 3. Run the scraper
python salsa_scraper.py
```

## Output

Saves a JSON file: `salsa_events_YYYY-MM-DD.json`

```json
{
  "weekend": { "friday": "2026-05-29", "saturday": "2026-05-30", "sunday": "2026-05-31" },
  "days": [
    {
      "date": "2026-05-29",
      "label": "Vrijdag 29 Mei",
      "top_events": [
        {
          "rank": 1,
          "name": "Salsa Fiesta Latina",
          "location": "De Centrale, Gent",
          "popularity": {
            "score": 12500,
            "fb_page_followers": 12500,
            "ig_followers": 3200,
            "fb_event_attendees": 0
          }
        }
      ]
    }
  ]
}
```

## Automate (every Wednesday 19h)

### macOS/Linux — cron
```bash
crontab -e
# Add this line:
0 19 * * 3 cd /path/to/scraper && python salsa_scraper.py >> scraper.log 2>&1
```

### Windows — Task Scheduler
1. Open Task Scheduler → Create Basic Task
2. Trigger: Weekly → Wednesday → 19:00
3. Action: Start a program → `python` → Arguments: `C:\path\to\salsa_scraper.py`

### GitHub Actions (cloud, free)
Create `.github/workflows/salsa.yml`:
```yaml
on:
  schedule:
    - cron: '0 18 * * 3'  # 18h UTC = 19h Brussels (CEST)
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt && playwright install chromium
      - run: python salsa_scraper.py
      - uses: actions/upload-artifact@v4
        with:
          name: salsa-events
          path: salsa_events_*.json
```

## Notes

- Run from your **local machine or a Belgian VPS** — datacenter IPs may
  be blocked by Cloudflare.
- The scraper captures XHR/fetch API calls from the SPA automatically,
  with HTML parsing as a fallback.
- Popularity scoring uses **public** Facebook/Instagram pages — no API
  keys needed, but results vary based on what each site exposes publicly.
- For full Facebook Graph API accuracy, add your `FB_ACCESS_TOKEN` to
  the environment and uncomment the Graph API block in `salsa_scraper.py`.
