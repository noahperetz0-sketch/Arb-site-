# Arb Screener

A personal website that scans live sportsbook odds via SharpAPI and shows
you cross-book arbitrage opportunities, with a stake calculator and a
one-click link to place each leg.

Right now it's running on **sample (fake) data** so you can see how it looks
and works before connecting your real SharpAPI key.

## What's in this project
- `app.py` — the backend. Calls SharpAPI, matches odds across books into
  arbitrage opportunities, and serves the page.
- `tests.py` — regression tests for the arb-matching logic. **Run this
  (`python tests.py`) before shipping any change to how arbs are matched.**
  Every test in it is a real bug that was found and fixed — it exists so
  none of them can quietly come back.
- `templates/index.html` — the page you see in your browser.
- `static/style.css` / `static/script.js` — styling and all the client-side
  logic (sport/book toggles, auto-refresh, filters, rendering).
- `Procfile` — tells Railway (or any Heroku-style host) to run the app with
  `gunicorn` instead of Flask's dev server.
- `.env.example` — the environment variables the app reads. Copy to `.env`
  for local testing; never commit a real `.env`.

## Running it yourself
1. Install Python if you don't have it.
2. In a terminal, inside this folder, run:
   ```
   pip install -r requirements.txt
   python app.py
   ```
3. Open `http://localhost:5000` in your browser.

To run the regression tests instead of the site: `python tests.py`.

## Connecting your real SharpAPI key
1. Set an environment variable called `SHARPAPI_KEY` to your key. (Never
   paste it directly into the code or share it in chat — if it's ever been
   exposed, e.g. in a screenshot, regenerate it in your SharpAPI dashboard.)
2. Set `SHARPAPI_BOOKS` to the exact sportsbook ids you picked in your
   SharpAPI dashboard (comma-separated, no spaces) if they ever change —
   the default in `.env.example` is betrivers, fanduel, betmgm, draftkings,
   betano.
3. Restart the site (or, on Railway, redeploy). It automatically switches
   from sample data to live data once a key is present.

## What the site actually does
- **Sport/league selection**: toggle any combination of sports on the
  homepage. Checking more than one sport scans every league within each of
  them (a single league picker doesn't make sense across different
  sports); checking exactly one sport reveals a League dropdown, including
  an "All Leagues" option. Checking 2+ sports multiplies the number of API
  requests per scan, so auto-refresh automatically disables itself in that
  mode with an explanation — use the Refresh button manually instead.
- **Sportsbook toggles**: pick which of your books to scan. Enforced twice
  — sent to SharpAPI's filter, and independently re-checked on every
  returned leg, so a book you didn't select can never appear in a result
  even if SharpAPI's own filter is ignored or misparsed.
- **Live/Pre-match filter**: filters the currently-loaded results
  client-side, no extra API call.
- **Place Bet button**: opens SharpAPI's deep link for that leg on that
  sportsbook in a new tab, when one is available.
- **$ profit + event start time**: shown on every card alongside the
  percentage, recalculating live as you change your total stake.

## How data accuracy is enforced
This is the part that's mattered most in practice — SharpAPI's raw odds
data has real inconsistencies between sports and books, and several bugs
were found (via real prices caught mismatched against the actual
sportsbook apps) before these checks existed. All are covered by
`tests.py`.

- **Every leg of an arb must be a different sportsbook.** If only one book
  has data for a market, "best price per side" would trivially come from
  that same book on both sides — not a real hedge, and not something you
  could actually place (the book would notice and void/limit it).
- **Spread-market legs must be true complements, not just matching
  magnitude.** Two books can each list their own team as favored by the
  same amount for the same market (e.g. one book has the home team -0.5,
  another independently has the away team -0.5) — both are "my team wins
  outright" bets, not opposite sides of one proposition. If the segment
  ties, neither actually wins. Every row's line is normalized relative to
  the home team before matching, so only genuine complements pair up.
- **Player-prop markets are excluded entirely**, checked two ways
  (SharpAPI's own flag, and independently via the word "player" in the
  market type) — market type alone doesn't say which player a row is
  about, and one of the two checks has been seen to fail silently on its
  own.
- **Stale prices are dropped** before ever being compared, using
  SharpAPI's own staleness flag — an old, unrefreshed price can otherwise
  look attractive purely because it hasn't caught up to the real number.
- **Live prices are separately checked for staleness by age.** SharpAPI's
  staleness flag only covers pregame prices — a real live soccer "Total
  Goals" market once showed a BetRivers leg at -155 (flag said not stale)
  when the actual live line had already moved to -560, almost certainly
  right after a goal, producing a fake ~20% "arb" that wasn't real (the
  Place Bet deep link 404'd — the book had already invalidated that quote).
  Any live row older than 10 seconds is now dropped, using its own
  timestamp field, since no equivalent "stale live price" flag exists.
- **A sanity cap on profit** (25%) is applied regardless of source — real
  cross-book arbs are almost always single-digit percentages, so anything
  wildly above that is treated as more likely a data glitch than free
  money and dropped rather than shown.
- **Book selection and sport/league scope are enforced server-side**,
  independent of whatever SharpAPI's own query filters actually do.
- **A short server-side cache (8s)** prevents rapid toggle-clicking or
  multiple open tabs from burning through the API's rate limit
  (confirmed: 150 requests/minute).

## A note on the SharpAPI integration
The confirmed-real endpoint is `/api/v1/odds` (sport + optional league +
sportsbook), which is what all arb-matching is actually built on. A
pre-computed `/api/v1/opportunities/arbitrage` endpoint is attempted first
as a bonus (documented in SharpAPI's marketing material, but never
confirmed to actually exist — no "Opportunities" tab has ever shown up in
SharpAPI's own playground) and falls back to the confirmed `/odds`-based
matching on any failure. `/api/v1/sports` and `/api/v1/leagues` (for the
sport/league dropdowns) are similarly unconfirmed and fall back to a small
hardcoded list on failure.

If something looks wrong, `/api/test-odds` is a diagnostic route that
confirms whether the API key and sport/league params work at all against
the confirmed `/odds` endpoint.

## Deploying (Railway example)
1. Create a free account at railway.app
2. "New Project" → "Deploy from GitHub repo", pointing at this repo's
   branch
3. In the project's "Variables" tab, add `SHARPAPI_KEY` (and `SHARPAPI_BOOKS`
   if needed)
4. Railway detects the `Procfile` and runs the app with gunicorn
   automatically
5. Under Settings → Networking, "Generate Domain" for a public URL
6. Enable auto-deploy on the branch so future pushes redeploy automatically
