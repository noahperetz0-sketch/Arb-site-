# Arb Screener — The Odds API build

This branch (`claude/odds-api-integration`) is a parallel build of the Arb
Screener using **[The Odds API](https://the-odds-api.com)** instead of
SharpAPI, kept on its own branch specifically so it can be compared
side-by-side against the SharpAPI build on
`claude/betting-arbitrage-site-zex0k3` — same UI, same arb-matching
philosophy, different data provider.

## Why this exists

The SharpAPI build kept returning wrong prices for a handful of books
(Caesars, specifically) once real Ontario betting was compared against it.
Root cause: SharpAPI's docs only ever showed US state codes for its
region-scoping param, with no confirmed Ontario support — so books that
vary by jurisdiction were silently defaulting to a Pennsylvania price. The
Odds API, by contrast, has a documented `ca` region with genuine
Ontario-specific bookmaker keys (the `_ca_on` suffix), confirmed straight
from their own docs. This build tests whether that actually produces
accurate arbs in practice.

## What's different from the SharpAPI build

- **No pre-built arbitrage finder.** The Odds API only returns raw
  per-bookmaker odds — there's no `/opportunities/arbitrage` equivalent.
  All arb detection here (`compute_arbs_from_events` in `app.py`) is done
  by this app, adapting the same "best price per complementary side,
  grouped by a line-agnostic canonical key" approach proven in the SharpAPI
  build's `compute_arbs_from_odds`.
- **Player props ARE scanned, unlike the SharpAPI build** — see "Player
  props" below. The SharpAPI build excluded them entirely because matching
  two different players sharing a market type reliably would have meant
  parsing free-text names, judged too fragile to trust with real money.
  This API gives a structured player name per outcome instead, which
  closes that gap.
- **Usage is a monthly credit quota, not a requests-per-minute limit.**
  Cost per call = `markets × regions` (or `markets × ⌈bookmakers/10⌉` when
  using an explicit bookmaker list). The exact quota for this account's
  plan isn't confirmed yet — the auto-refresh pacing in `static/script.js`
  (`ASSUMED_REQUESTS_PER_MINUTE_BUDGET`) is a clearly-flagged placeholder
  until that's known, not a real budget calculation.
- **Sport + league is one flat key** (e.g. `americanfootball_nfl`), not
  SharpAPI's separate sport/league pair. This app maps The Odds API's
  `group` field (e.g. "American Football") onto the existing Sport filter
  concept, and each `key` within a group onto the existing League dropdown
  — so the UI itself didn't need to change.
- **No staleness flags at all.** SharpAPI had `is_stale_pregame_price` and
  a documented set of `warnings`/`possibly_stale` codes; The Odds API gives
  only a `last_update` timestamp per bookmaker/market, no derived
  freshness signal. `MAX_SANE_PROFIT_PERCENT` (8%, same value and
  reasoning as the SharpAPI build after its own real-world tightening) is
  the only defense here against a mismatched/stale price producing a fake
  arb.
- **No "Bet ↗" deep links yet.** The Odds API's `includeLinks` param
  reportedly adds bookmaker betslip links "if available", but the exact
  response field name isn't confirmed from the docs pasted into this
  build — `deep_link` is left `null` rather than guessing a field name
  that might not exist. The frontend already hides the Bet button when
  this is null, so nothing breaks — the button's just absent for now.
- **Book catalog is fully static.** SharpAPI had a live `/sportsbooks`
  endpoint; The Odds API has no equivalent, so `/api/books` here is served
  entirely from `BOOKMAKER_CATALOG` in `app.py`, transcribed from their
  "Supported Bookmakers" docs page.

## Default book set

```
fanduel, draftkings, betmgm_ca_on, betrivers_ca_on, betano_ca_on, espnbet (theScore Bet)
```

FanDuel, DraftKings, and theScore Bet are pulled from their US-wide keys —
confirmed accurate for Ontario by direct real-world comparison against the
SharpAPI build, which never caught a bad price on any of these three across
several real incidents that *did* catch Caesars/BetMGM/BetRivers being
wrong. BetMGM, BetRivers, and Betano use their `_ca_on` Ontario-specific
keys instead, since those books are confirmed to have real per-jurisdiction
pricing differences.

**Known gap**: Bally Bet, Betway, and Caesars have no Ontario-specific key
in The Odds API's catalog either (same situation as FanDuel/DraftKings —
only a US or UK key exists) but haven't been validated the way FD/DK/
theScore Bet have. Treat any arb involving those three as unverified until
spot-checked against the real sportsbook.

## Player props

Player props live behind a different, more expensive part of The Odds
API than the bulk `h2h`/`spreads`/`totals` scan: one `GET event markets`
call (1 credit flat, confirmed) to discover what's actually offered for a
specific game, then one `GET event odds` call to fetch those markets'
prices. This build does both automatically, per scan, but **bounded to the
first `MAX_EVENTS_FOR_PROP_SCAN` (6) events** of whatever the core scan
already returned — a broad "All Leagues"/"All Sports" scan does NOT get
every game checked for props, to keep quota cost predictable.

There is deliberately **no hardcoded list of player prop market keys**
anywhere in this app. Confirmed examples in their docs show prop naming
genuinely differs by sport (NFL/NBA use a `player_` prefix, MLB uses
`batter_`/`pitcher_`), and this build never had their full market-key
reference page to transcribe a complete, trustworthy list from. Instead,
`discover_prop_markets_for_event()` asks the API live, per event, what's
actually being offered right now, and scans whatever comes back — correct
for any sport without needing to know its specific market-key vocabulary
in advance.

**To see the real, live list yourself** for any sport: hit
`/api/test-events?sport=<sport_key>` to grab a real event id (free, no
quota cost), then `/api/test-event-markets?sport=<sport_key>&event_id=<id>`
to see every market key actually available for that game right now (1
credit). This is the authoritative source — more accurate than any static
list, since it reflects exactly what's live at that moment.

Matching logic (`compute_arbs_from_events` in `app.py`): a prop outcome's
`description` field (confirmed real — e.g. `{"name": "Over",
"description": "David Blough", "price": -205, "point": 0.5}`) is the
player's name, and becomes part of the grouping key alongside the market
key and point. This is what lets two different players' prop lines in
the same game share a market_type without ever being matched against each
other — exactly the risk that made the SharpAPI build exclude props
entirely, solved here instead of avoided, because this API's structured
player field doesn't require fragile free-text parsing the way SharpAPI's
did.

## Setup

1. Copy `.env.example` to `.env` (or set the same variables in Railway's
   Variables tab).
2. Set `ODDS_API_KEY` to your key from
   [the-odds-api.com](https://the-odds-api.com).
3. `ODDS_API_BOOKS` defaults to the set above — override if you want a
   different starting selection (the Sportsbooks panel's toggles and "My
   List" work the same as the SharpAPI build regardless).
4. `python app.py` to run locally, or let Railway auto-deploy from this
   branch.

Without a key set, the app serves mock/sample data (same fallback pattern
as the SharpAPI build) rather than failing outright.

## Testing

```
python tests.py
```

17 tests covering: same-book rejection, cross-book moneyline/spread/totals
matching, true-complement vs. conflicting-favorite spread detection,
mismatched total-line rejection, player-prop matching by player identity
(including two different players sharing a market never being matched,
and an outcome with no player name being skipped rather than guessed at),
outright/futures-league exclusion, the profit sanity cap, live-event
detection, and the core schema-adaptation helpers
(`_selection_side`, `_canonical_point`, `_american_to_decimal`,
`_format_selection`). All fixtures use realistic, single-digit profit
percentages — matching the same lesson learned in the SharpAPI build,
where an earlier test fixture's unrealistic odds accidentally validated
the wrong thing once the sanity cap was tightened.

## Open questions

- **Actual quota for this account's plan** — needed to design real
  auto-refresh pacing instead of the placeholder currently in place, and
  to know how aggressively `MAX_EVENTS_FOR_PROP_SCAN` can safely scale up.
- **`includeLinks` response shape** — needed to wire up the "Bet ↗"
  button; not yet confirmed from docs.
- **Whether Bally Bet/Betway/Caesars are trustworthy without a
  jurisdiction-specific key** — only FanDuel/DraftKings/theScore Bet have
  been validated so far.
- **Whether player props require a paid plan** — the current (non-
  historical) event-odds endpoint had no "paid plans only" note in
  anything pasted into this build (only the historical endpoints did), so
  this was built assuming it's free-tier accessible. Confirm by checking
  whether `/api/test-event-markets` (see "Player props" above) returns
  real data or a plan-restriction error on the live account.
