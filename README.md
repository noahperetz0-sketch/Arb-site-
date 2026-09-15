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
- **Arb results render as a dense data table** (League / Market / Game /
  Profit / Legs columns), not stacked cards — deliberately modeled after
  professional scanner tools, since a wide scannable table reads much
  faster than a tall list once a busy slate (NBA season) has many
  simultaneous arbs to get through. Each leg shows a colored sportsbook
  badge, selection, odds, stake $, and a compact "Bet ↗" link. Scrolls
  horizontally on narrow viewports rather than reflowing to cards.
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
  even if SharpAPI's own filter is ignored or misparsed. Beyond the 5 books
  in `SHARPAPI_BOOKS`, every other sportsbook is offered as an unchecked
  toggle, tagged with the SharpAPI plan tier it requires (Free/Hobby/Pro/
  Sharp) — live-fetched from SharpAPI's own confirmed-real
  `/api/v1/sportsbooks` endpoint (`get_sportsbook_list()` in `app.py`), so
  a brand-new book shows up automatically without a code change; falls
  back to `SPORTSBOOK_CATALOG`, a static list hand-transcribed from
  SharpAPI's "Supported Sportsbooks" docs page, if that live call fails.
  Toggling one on only pulls data once it's actually active for your
  account — and that's gated *two* independent ways on SharpAPI's side, not
  just plan tier: a book can be within your tier but still return nothing
  if it isn't turned on in your SharpAPI dashboard's own book selection.
  The status line tells the two apart (see `book_issues` above) instead of
  both just looking like silence. Worth knowing: SharpAPI also caps how
  many books can be *simultaneously selected* per tier, separate from
  which books your tier can reach at all — Free 2, **Hobby 5** (confirmed
  — this is exactly why this site's plan has 5 books configured, not an
  arbitrary choice), Pro 15, Sharp 25, Enterprise unlimited. So no matter
  how many toggles this site shows, only that many can be active on
  SharpAPI's side at once — switching to a new book means deselecting one
  of your current ones in the SharpAPI dashboard first. For anything missing entirely (e.g. a
  brand-new addition before either list picks it up), there's also an "Add
  a sportsbook" box in the Sportsbooks panel — type the exact id from your
  SharpAPI dashboard to add a toggle for it immediately. Custom-added books
  and your current toggle selections are both remembered in the browser
  (localStorage) across visits.
- **Sports and Sportsbooks panels collapse independently** of each other
  and of the outer Filters panel, so you can close one while working on
  the other instead of everything competing for space at once.
- **Live/Pre-match filter, minimum profit filter, and sort order**: all
  filter/reorder the currently-loaded results client-side, no extra API
  call. The minimum-profit filter matters once a busier slate (NBA season,
  multiple simultaneous games) is producing more arbs at once than a
  quick glance can parse — set it to e.g. 1% to cut the noise. Sort
  defaults to highest profit first (already the order the backend returns
  them in); "Starting soonest" re-sorts by event start time instead, for
  prioritizing what needs action first.
- **Place Bet button**: opens SharpAPI's deep link for that leg on that
  sportsbook in a new tab, when one is available. BetMGM, Caesars, and
  BetRivers have state-dependent deep link domains (confirmed in
  SharpAPI's docs) — set `SHARPAPI_STATE` to your two-letter state code so
  those resolve correctly. If unset, SharpAPI defaults to `pa`
  (Pennsylvania) server-side, which is almost certainly wrong for you.
  Every deep link also carries a `?fallback=` param pointing at a search
  for the sportsbook's name, so a link that's gone stale by click time
  (the original BetRivers bug) lands somewhere useful instead of
  SharpAPI's raw `{"error": {"code": "not_found", ...}}` JSON.
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
  (SharpAPI's own `is_player_prop` flag, and independently via the word
  "player" in the market type) — market type alone doesn't say which
  player a row is about. The flag alone isn't enough: SharpAPI computes it
  by checking whether `market_type` *starts with* `player_` (confirmed in
  their own docs), but period/segment-scoped player props are named like
  `1st_half_player_passing_yards` — "player" appears mid-string after the
  segment prefix, so the flag reads `false` on every one of them. The
  substring check is what actually catches those.
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
  Confirmed honest limitation (SharpAPI's own docs): this catches a row
  the pipeline hasn't recently re-touched, but can't catch a poll-based
  book (DraftKings, BetMGM, Caesars, BetRivers, Betano — all this site's
  plan books) being behind real-world reality even on a "fresh" row —
  `timestamp` advances every ingest cycle regardless of whether the
  underlying price changed. DraftKings' own book-to-SharpAPI collection
  lag alone is ~8s p50 / ~21s p95. Only Pinnacle (push-based, Sharp tier,
  $399/mo) closes this gap, which is above this site's plan — a real,
  currently-irreducible risk on the softbook-only side of live detection.
- **Cross-book event matching handles a doubleheader-suffix split.**
  SharpAPI's own canonical `event_id` can differ for the *same* physical
  game across books — one book reports both games of a same-day
  doubleheader in one update (getting a `_g{N}`-suffixed id), another
  sees only one game (getting the bare id). Grouping strictly by raw
  `event_id` would silently miss a real arb whenever that split occurs.
  Fixed by stripping only the trailing `_g{N}` suffix before grouping —
  confirmed by SharpAPI's own docs as safe ("mirrors the server-side
  same-event predicate"), unlike also stripping the `_b{N}` start-time
  bucket, which they explicitly warn can merge two genuinely different
  same-day games into one — actively dangerous for arb detection.
- **A sanity cap on profit** (25%) is applied regardless of source — real
  cross-book arbs are almost always single-digit percentages, so anything
  wildly above that is treated as more likely a data glitch than free
  money and dropped rather than shown.
- **Book selection and sport/league scope are enforced server-side**,
  independent of whatever SharpAPI's own query filters actually do.
- **A short server-side cache (8s)** prevents rapid toggle-clicking or
  multiple open tabs from burning through the API's rate limit (Hobby plan:
  120 requests/minute, confirmed against SharpAPI's own docs).
- **Odds requests follow pagination** (up to 3 pages, 600 rows, per book
  per scan) instead of reading only the first 200-row page. A busy NBA
  slate alone — a dozen games × ~40+ non-prop rows each across full-game
  and quarter/half markets — already exceeds 200 rows before a single
  player-prop row (fetched but discarded) enters the budget. Reading only
  page 1 would have silently truncated some games out of the scan
  entirely on exactly the nights with the most real arbs to find.

## A note on the SharpAPI integration
The confirmed-real endpoint `/api/v1/odds` (sport + optional league +
sportsbook) is what all arb-matching is actually built on. A pre-computed
`/api/v1/opportunities/arbitrage` endpoint is attempted first as a bonus -
fully confirmed and field-checked against SharpAPI's own docs (Hobby tier
or higher, which this site's plan is): `sport`/`league`/`market`/
`min_profit`/`state` are all real params now passed through correctly,
its warning flags (`LIVE_HIGH_PROFIT_SUSPICIOUS`, `HIGH_PROFIT_SUSPICIOUS`,
`LIVE_STALE_ODDS`, `POTENTIALLY_STALE_ODDS`, `VERY_STALE_ODDS`, and the
reserved `LOW_IMPLIED_TOTAL`) are matched exactly rather than
substring-guessed, and player props are excluded on this path too (they
weren't before). Still falls back to the `/odds`-based matching on any
failure. `/api/v1/sports`, `/api/v1/leagues`, and `/api/v1/sportsbooks`
(for the sport/league dropdowns and the sportsbook toggle catalog) are all
confirmed-real too, each with a small hardcoded fallback if the live call
fails.

**Two separate ways a book can return nothing**, per SharpAPI's documented
error codes: `tier_restricted` (your plan tier doesn't cover this book at
all) vs `book_not_selected` (your tier does cover it, but it isn't turned
on in your SharpAPI dashboard's own book selection - a second, independent
gate beyond this site's own toggles). `/api/arbs` surfaces which books hit
which reason as `book_issues` in its response, shown in the status line as
"Skipped: <book> (<reason>)" instead of silent emptiness.

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
