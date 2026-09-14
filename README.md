# Arb Screener

A personal website that shows live arbitrage betting opportunities pulled
from SharpAPI, with an adjustable total-stake calculator.

Right now it's running on **sample (fake) data** so you can see how it looks
and works before connecting your real SharpAPI key.

## What's in this project
- `app.py` — the backend. Calls SharpAPI and serves the page.
- `templates/index.html` — the page you see in your browser.
- `static/style.css` / `static/script.js` — styling and the refresh/stake logic.
- `Procfile` — tells Railway (or any Heroku-style host) to run the app with
  `gunicorn` instead of Flask's dev server.
- `.env.example` — the environment variables the app reads. Copy to `.env`
  for local testing; never commit a real `.env`.

## Running it yourself (optional, for testing)
You don't need to do this — Claude can deploy it for you — but if you want to
try it locally:

1. Install Python if you don't have it.
2. In a terminal, inside this folder, run:
   ```
   pip install -r requirements.txt
   python app.py
   ```
3. Open `http://localhost:5000` in your browser.

## Connecting your real SharpAPI key
Once you've upgraded to the Hobby plan and have a real API key:
1. Set an environment variable called `SHARPAPI_KEY` to your key.
   (Never paste it directly into the code or share it in chat.)
2. Set `SHARPAPI_BOOKS` to the exact 5 sportsbook ids you picked in your
   SharpAPI dashboard (comma-separated, no spaces) if they ever change —
   the default in `.env.example` is currently set to betrivers, fanduel,
   betmgm, draftkings, kalshi.
3. Restart the site. It will automatically switch from sample data to live data.

## Deploying so you have a permanent link (Railway example)
1. Create a free account at railway.app
2. Create a "New Project" → "Deploy from GitHub repo" (or use their CLI to
   upload this folder directly)
3. In the project's "Variables" tab, add `SHARPAPI_KEY` (and `SHARPAPI_BOOKS`
   if needed) — this keeps it secret and off your screen/chat entirely
4. Railway will detect the `Procfile` and run the app with gunicorn
   automatically
5. Railway will give you a public URL like `your-app.up.railway.app` —
   bookmark that and you're done

## How data accuracy is enforced
- **Book selection is enforced twice.** The sportsbook toggles are sent to
  SharpAPI's own filter, but the backend also re-checks every returned arb's
  legs client-side — if SharpAPI ever ignores or misparses that filter
  param, an arb involving a book you didn't select is dropped rather than
  shown as something you could actually bet.
- **Stale/suspicious arbs are filtered out.** Any opportunity SharpAPI flags
  as `possibly_stale` or with a `SUSPICIOUS`/`STALE` warning is dropped.
- **A sanity cap on profit is applied regardless of source.** Real cross-book
  arbs are almost always single-digit percentages; anything above 25% is
  filtered out as a near-certain sign of a data glitch rather than real
  free money — whether it came from SharpAPI's pre-computed endpoint or the
  free-tier fallback scan below.
- **Free-tier fallback only scans complete markets.** If your API key isn't
  on a tier where the arbitrage endpoint is available, the backend instead
  pulls full odds for each event (every book, every selection for that
  market) and computes arbs itself — never from a partial/paginated slice,
  since missing outcomes can make a market look falsely profitable.
- **A short server-side cache (8s)** prevents rapid toggle-clicking or
  multiple open tabs from burning through the API's rate limit.

## A note on the SharpAPI integration
The endpoint paths in `app.py` (`/api/v1/opportunities/arbitrage`,
`/api/v1/sportsbooks`, `/api/v1/events`, `/api/v1/events/{id}/odds`) and the
response field names match SharpAPI's publicly documented Python SDK and
marketing site. If your key returns a 404 on `/api/arbs`, hit
`/api/test-odds` first to confirm the key itself works against the simpler
`/api/v1/odds` endpoint, then send Claude the exact response body — the
`fetch_arbs_paid`/`fetch_events`/`fetch_event_odds` functions are the only
places that need adjusting if a path is off.

## Notes
- The stake calculator uses the `stake_percent` SharpAPI provides for each leg
  of an arb — that percentage split stays the same no matter your total stake,
  so changing the "Total stake" box just recalculates dollar amounts instantly.
- Auto-refresh (every 30s) can be toggled off from the header, and pauses
  automatically while the browser tab isn't visible so it doesn't burn API
  calls in the background.
