import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SHARPAPI_KEY = os.environ.get("SHARPAPI_KEY", "")
SHARPAPI_BASE_URL = os.environ.get("SHARPAPI_BASE_URL", "https://api.sharpapi.io")

# The 5 sportsbooks your Hobby plan is scoped to, comma-separated ids
# (lowercase, no spaces — e.g. "draftkings", confirmed against a real
# response from SharpAPI's own playground).
SHARPAPI_BOOKS = os.environ.get(
    "SHARPAPI_BOOKS",
    "betrivers,fanduel,betmgm,draftkings,betano",
)

# SharpAPI's /odds endpoint requires a sport+league pair. Override per
# request with ?sport=&league= on /api/arbs, or change these env vars, if
# you want something other than NFL.
DEFAULT_SPORT = os.environ.get("SHARPAPI_SPORT", "football")
DEFAULT_LEAGUE = os.environ.get("SHARPAPI_LEAGUE", "nfl")

# Confirmed per SharpAPI's own /odds docs: BetMGM, Caesars, and BetRivers
# have state-dependent deep link URLs - without a ?state= param on the
# /odds request, their deep_link may point at the wrong state's domain
# (or a generic one that doesn't resolve). BetRivers is one of this site's
# 5 plan books and is exactly the book that produced a "Deep link ID not
# found" error before the live-staleness fix - the missing state param may
# be a second, independent contributor to the same symptom. Set this to
# your two-letter state code (e.g. "nj", "ny", "il") if you bet from a
# state where any of those books operates; leave empty otherwise, and the
# param is simply omitted.
SHARPAPI_STATE = os.environ.get("SHARPAPI_STATE", "")

# Real cross-book arbs are almost always single digits. Anything above this
# is far more likely to be stale or mismatched data than free money, so
# it's dropped rather than shown.
#
# Was 25.0 - lowered after two confirmed real-world incidents (both WNBA
# pregame moneylines, both implicating Caesars specifically) that this
# threshold let straight through: a 12.15% "arb" where Caesars showed
# Dallas Wings at -159 when the real price was -325, and a 19.68% one
# where Caesars showed Minnesota Lynx at -118 against a since-confirmed-bad
# price. Neither arb's `possibly_stale`/`warnings` fields caught it on
# SharpAPI's own side (fetch_arbs_paid()'s only other line of defense -
# that endpoint exposes no per-leg timestamp to cross-check independently,
# unlike /odds). 8.0 comfortably clears legitimate soft-book arbs (which
# can run a bit hotter than major-book-only pairs, especially in thinner
# markets like WNBA) while catching both of these specific incidents,
# which were nowhere near single digits.
MAX_SANE_PROFIT_PERCENT = 8.0

# SharpAPI's is_stale_pregame_price flag only covers PREGAME prices - it
# says nothing about a LIVE row having gone stale (confirmed: a real live
# soccer "Total Goals" row sat at is_stale_pregame_price=False while showing
# -155 well after the actual BetRivers line had moved to -560, almost
# certainly right after a goal - a swing that size only happens in-play).
# Live odds should refresh within seconds of a game event, so any live row
# older than this is treated as stale and dropped, using the row's own
# "timestamp" field since no equivalent live-staleness flag exists.
#
# Honest limitation, confirmed by SharpAPI's own docs (Live vs Pre-Match /
# The timestamp field): this catches a row SharpAPI's pipeline hasn't
# recently re-touched (the BetRivers bug above), but it CANNOT catch the
# separate risk that a poll-based book's own collection lag has it behind
# real-world reality even on a "fresh" row - timestamp advances every
# ingest cycle whether or not the underlying price actually changed, so a
# 3-second-old timestamp only proves SharpAPI re-confirmed the price 3
# seconds ago, not that the book's own price reflects the current game
# state. Confirmed real numbers: DraftKings' book-to-SharpAPI collection
# latency alone is ~8s p50 / ~21s p95 (HTTP polling); BetMGM/Caesars/
# BetRivers/Betano are the same poll-based pattern. Only Pinnacle-tier
# (MQTT push, p50 ~0.8s) fully closes this gap, and that needs Sharp tier
# ($399/mo) - well above this site's Hobby plan. No threshold value here
# fixes this; it's a real, currently-irreducible risk on the softbook-only
# side of arb detection, not a bug to chase further.
MAX_LIVE_ROW_AGE_SECONDS = 10

# Confirmed per SharpAPI's own docs (Tier Comparison / Subscription Tiers
# tables): Free 12 req/min, Hobby 120, Pro 300, Sharp 1,000, Enterprise
# custom. This site's plan is Hobby, hence 120 - previously set to a
# guessed 150 before these docs were available. A single-sport scan (one
# request per selected book) stays cheap; "all sports"/"all leagues" scans
# multiply that by however many sports get looped over and can burn a big
# chunk of the budget in one call (the frontend disables auto-refresh in
# that mode for exactly this reason). The real per-response rate-limit
# headers (X-RateLimit-Limit/-Remaining/-Reset) aren't read by this app -
# this constant is just used for the auto-refresh cadence comment/UI math
# below, not for any actual backoff logic.
SHARPAPI_RATE_LIMIT_PER_MINUTE = 120

# Rows returned per sportsbook per PAGE (200 is SharpAPI's documented max
# for `limit`). A single 200-row page is not enough on a busy slate: a
# dozen NBA games at ~40+ non-prop rows each (moneyline/spread/total ×
# full-game + 4 quarters + 2 halves × 2 sides) already exceeds 200 before
# player-prop rows - which we fetch but discard - even enter the budget.
# Truncating silently would mean whole games/markets never get scanned for
# arbs on exactly the nights (NBA season) this matters most. See
# MAX_ODDS_PAGES_PER_BOOK below for how far we page to cover that.
ODDS_PAGE_LIMIT = 200

# fetch_odds_for_book() follows cursor-based pagination (confirmed real:
# pagination.next_cursor / has_more) up to this many pages per book per
# scan, instead of the previous single-page-only behavior. Bounded rather
# than unbounded so one heavy book/sport/league combo can't runaway a
# scan's request budget - 3 pages = up to 600 rows/book/scan, comfortably
# covering a full NBA slate's main markets while staying a small multiple
# of the Hobby-tier 120 req/min limit even with all 5 plan books selected.
MAX_ODDS_PAGES_PER_BOOK = 3

# Short in-memory cache so rapid book-toggle clicks or multiple open tabs
# don't burn through the API's request budget. Was 8s; reduced to 3s since
# it directly compounds with live-price staleness - the total worst-case
# gap between a real-world price change and what's on screen is roughly
# (SharpAPI's own book-collection lag, confirmed ~8-21s for poll-based
# books like DraftKings) + (this cache) + (the auto-refresh interval,
# now dynamic - see MIN_AUTO_REFRESH_INTERVAL_MS in script.js). An 8s
# cache stacked on top of an 8s refresh interval was needlessly doubling
# that middle term; 3s still gives real click/multi-tab protection
# without meaningfully adding to the total.
ARBS_CACHE_TTL_SECONDS = 3
_arbs_cache = {}  # cache key -> (timestamp, response_dict)

# Sports/leagues change rarely, so this cache lives much longer.
SPORTS_CACHE_TTL_SECONDS = 3600
_sports_cache = None  # (timestamp, [{"id","name"}])
_leagues_cache = {}  # sport -> (timestamp, [{"id","name"}])

# Only used if SharpAPI's /sports or /leagues endpoints don't exist or fail -
# transcribed from SharpAPI's own "Sports" docs page ("Available Sports"
# table - confirmed real sport ids, not guessed). Previously a short list
# of 6 that was missing baseball entirely despite this app having dedicated,
# tested MLB matching logic (see tests.py's mlb_run_line tests).
FALLBACK_SPORTS = [
    {"id": "basketball", "name": "Basketball"},
    {"id": "football", "name": "Football"},
    {"id": "hockey", "name": "Hockey"},
    {"id": "baseball", "name": "Baseball"},
    {"id": "soccer", "name": "Soccer"},
    {"id": "tennis", "name": "Tennis"},
    {"id": "mma", "name": "MMA"},
    {"id": "golf", "name": "Golf"},
    {"id": "boxing", "name": "Boxing"},
    {"id": "cricket", "name": "Cricket"},
    {"id": "rugby_union", "name": "Rugby Union"},
    {"id": "rugby_league", "name": "Rugby League"},
    {"id": "aussie_rules", "name": "Aussie Rules"},
    {"id": "lacrosse", "name": "Lacrosse"},
    {"id": "motorsports", "name": "Motorsports"},
    {"id": "darts", "name": "Darts"},
    {"id": "snooker", "name": "Snooker"},
    {"id": "table_tennis", "name": "Table Tennis"},
    {"id": "volleyball", "name": "Volleyball"},
    {"id": "handball", "name": "Handball"},
    {"id": "water_polo", "name": "Water Polo"},
    {"id": "horse_racing", "name": "Horse Racing"},
    {"id": "cycling", "name": "Cycling"},
    {"id": "olympics", "name": "Olympics"},
    {"id": "politics", "name": "Politics"},
    {"id": "entertainment", "name": "Entertainment"},
    {"id": "esports", "name": "Esports"},
]

# The sports this site's owner actually watches day to day - preselected
# by default wherever the real sports list includes them. Baseball added
# alongside this round of doc fixes - this app has dedicated, tested MLB
# matching logic (tests.py's mlb_run_line tests reference "the actual
# reported case"), so its absence here looks like an oversight rather than
# a deliberate exclusion; easy to toggle back off if that's wrong.
PREFERRED_SPORTS = ["basketball", "football", "hockey", "baseball", "soccer", "tennis"]

# Confirmed real league ids - from SharpAPI's own "Leagues" docs page (the
# "Common Leagues" reference table) and real response examples embedded in
# the "Sports" docs page (tennis/boxing/mma/horse_racing's leagues arrays).
# Not an exhaustive list (soccer alone has 300+ real leagues) - just enough
# for the "All Leagues" dropdown to have real options if the live call
# fails, matching the same fallback philosophy as FALLBACK_SPORTS.
FALLBACK_LEAGUES = {
    "football": [{"id": "nfl", "name": "NFL"}, {"id": "ncaaf", "name": "NCAAF"}],
    "basketball": [
        {"id": "nba", "name": "NBA"},
        {"id": "ncaab", "name": "NCAAB"},
        {"id": "wnba", "name": "WNBA"},
    ],
    "baseball": [{"id": "mlb", "name": "MLB"}],
    "hockey": [{"id": "nhl", "name": "NHL"}],
    "soccer": [
        {"id": "england_-_premier_league", "name": "England - Premier League"},
        {"id": "spain_-_la_liga", "name": "Spain - La Liga"},
        {"id": "italy_-_serie_a", "name": "Italy - Serie A"},
        {"id": "germany_-_bundesliga", "name": "Germany - Bundesliga"},
        {"id": "france_-_ligue_1", "name": "France - Ligue 1"},
        {"id": "uefa_-_champions_league", "name": "UEFA - Champions League"},
        {"id": "usa_-_major_league_soccer", "name": "USA - Major League Soccer"},
    ],
    "tennis": [
        {"id": "atp", "name": "ATP"},
        {"id": "wta", "name": "WTA"},
        {"id": "atp_challenger", "name": "ATP Challenger"},
        {"id": "itf_men", "name": "ITF Men"},
        {"id": "itf_women", "name": "ITF Women"},
        {"id": "utr", "name": "UTR"},
    ],
    "mma": [{"id": "ufc", "name": "UFC"}],
    "golf": [
        {"id": "pga", "name": "PGA"},
        {"id": "dp_world_tour", "name": "DP World Tour"},
        {"id": "world_tour", "name": "World Tour"},
    ],
    "boxing": [{"id": "boxing_matches", "name": "Boxing Matches"}],
    "horse_racing": [
        {"id": "horse", "name": "Horse"},
        {"id": "horses_daily", "name": "Horses Daily"},
    ],
}

# Every sportsbook SharpAPI supports, transcribed directly from SharpAPI's
# own "Supported Sportsbooks" docs page (confirmed, not guessed) - Major US,
# Sharp, International, Exchange, and Prediction Market books. `tier` is the
# minimum SharpAPI plan tier required to actually pull odds from that book
# ("free" < "hobby" < "pro" < "sharp") - toggling on a book above your
# current plan's tier returns nothing, same as a book your plan just
# doesn't cover, not a site bug. Ids are stored exactly as documented and
# must never be reformatted - most are clean lowercase, but "Bet365 US" is
# genuinely a space + capital letters.
SPORTSBOOK_CATALOG = [
    # Major US
    {"id": "draftkings", "display_name": "DraftKings", "tier": "free"},
    {"id": "fanduel", "display_name": "FanDuel", "tier": "free"},
    {"id": "ballybet", "display_name": "Bally Bet", "tier": "hobby"},
    {"id": "Bet365 US", "display_name": "Bet365 US", "tier": "hobby"},
    {"id": "betmgm", "display_name": "BetMGM", "tier": "hobby"},
    {"id": "betonline", "display_name": "BetOnline", "tier": "hobby"},
    {"id": "betparx", "display_name": "betPARX", "tier": "hobby"},
    {"id": "betrivers", "display_name": "BetRivers", "tier": "hobby"},
    {"id": "bovada", "display_name": "Bovada", "tier": "hobby"},
    {"id": "caesars", "display_name": "Caesars", "tier": "hobby"},
    {"id": "fanatics", "display_name": "Fanatics", "tier": "hobby"},
    {"id": "fanatics_markets", "display_name": "Fanatics Markets", "tier": "hobby"},
    {"id": "fliff", "display_name": "Fliff", "tier": "hobby"},
    {"id": "gemini", "display_name": "Gemini", "tier": "hobby"},
    {"id": "novig", "display_name": "Novig", "tier": "hobby"},
    {"id": "rebet", "display_name": "Rebet", "tier": "hobby"},
    {"id": "robinhood", "display_name": "Robinhood", "tier": "hobby"},
    {"id": "sportzino", "display_name": "Sportzino", "tier": "hobby"},
    {"id": "thescorebet", "display_name": "theScore Bet", "tier": "hobby"},
    {"id": "thrillzz", "display_name": "Thrillzz", "tier": "hobby"},
    {"id": "underdog", "display_name": "Underdog Fantasy", "tier": "hobby"},
    # Sharp
    {"id": "onexbet", "display_name": "1xBet", "tier": "sharp"},
    {"id": "circa", "display_name": "Circa Sports", "tier": "sharp"},
    {"id": "pinnacle", "display_name": "Pinnacle", "tier": "sharp"},
    {"id": "sbobet", "display_name": "SBOBET", "tier": "sharp"},
    # International
    {"id": "betano", "display_name": "Betano", "tier": "hobby"},
    {"id": "betway", "display_name": "Betway", "tier": "hobby"},
    {"id": "bwin", "display_name": "bwin", "tier": "hobby"},
    {"id": "coral", "display_name": "Coral", "tier": "hobby"},
    {"id": "galera", "display_name": "Galera.bet", "tier": "hobby"},
    {"id": "goldrush", "display_name": "Goldrush", "tier": "hobby"},
    {"id": "ladbrokes", "display_name": "Ladbrokes", "tier": "hobby"},
    {"id": "matchbook", "display_name": "Matchbook", "tier": "hobby"},
    {"id": "paddypower", "display_name": "Paddy Power", "tier": "hobby"},
    {"id": "skybet", "display_name": "Sky Bet", "tier": "hobby"},
    {"id": "smarkets", "display_name": "Smarkets", "tier": "hobby"},
    {"id": "stake", "display_name": "Stake", "tier": "hobby"},
    {"id": "sx_bet", "display_name": "SX Bet", "tier": "hobby"},
    {"id": "unibet", "display_name": "Unibet", "tier": "hobby"},
    {"id": "saba", "display_name": "SABA", "tier": "pro"},
    # Exchanges
    {"id": "betfair", "display_name": "Betfair", "tier": "sharp"},
    {"id": "prophetx", "display_name": "ProphetX", "tier": "sharp"},
    # Prediction Markets
    {"id": "kalshi", "display_name": "Kalshi", "tier": "hobby"},
    {"id": "polymarket", "display_name": "Polymarket", "tier": "hobby"},
]


def _normalize_book(name):
    """Collapses a book id/display name to a bare-lowercase key
    ('DraftKings' / 'draft-kings' / 'draftkings' all -> 'draftkings') so we
    can compare the toggle selection against whatever casing a leg's
    sportsbook field happens to use."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_CATALOG_BY_NORMALIZED_ID = {_normalize_book(b["id"]): b for b in SPORTSBOOK_CATALOG}


def _book_display_name(book_id):
    entry = _CATALOG_BY_NORMALIZED_ID.get(_normalize_book(book_id))
    return entry["display_name"] if entry else book_id.title()


_sportsbooks_cache = None  # (timestamp, [{"id","display_name","tier"}])


def fetch_sportsbooks():
    """Calls SharpAPI's sportsbooks-list endpoint - confirmed real per
    SharpAPI's own docs (GET /api/v1/sportsbooks, Free tier, also a public
    reference endpoint reachable unauthenticated at 10 req/min) and against
    a real response (fields: id, display_name, short_name, category,
    requires_tier, status, coming_soon, event_count, ...). Used so a
    brand-new book SharpAPI adds shows up as a toggle automatically,
    without SPORTSBOOK_CATALOG (the static fallback below) needing to be
    hand-updated and redeployed."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/sportsbooks", headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    books = []
    for b in data:
        book_id = b.get("id")
        if not book_id or b.get("coming_soon"):
            continue
        if b.get("status") and b.get("status") != "active":
            continue
        books.append({
            "id": book_id,
            "display_name": b.get("display_name") or b.get("short_name") or book_id,
            "tier": b.get("requires_tier"),
        })
    return books


def get_sportsbook_list():
    """Live-fetched, cached list of every sportsbook SharpAPI supports -
    falls back to SPORTSBOOK_CATALOG (the static, hand-transcribed-from-docs
    list) if the live call fails or no key is set, same graceful-degradation
    pattern as get_cached_sport_ids(). Cached rather than fetched at import
    time so a flaky network at startup can't block the app from serving."""
    global _sportsbooks_cache
    if not SHARPAPI_KEY:
        return SPORTSBOOK_CATALOG
    if _sportsbooks_cache and time.time() - _sportsbooks_cache[0] < SPORTS_CACHE_TTL_SECONDS:
        return _sportsbooks_cache[1]
    try:
        books = fetch_sportsbooks()
        _sportsbooks_cache = (time.time(), books)
        return books
    except Exception:
        return SPORTSBOOK_CATALOG


def _book_catalog():
    """Every sportsbook toggle offered in the UI: the plan-configured books
    from SHARPAPI_BOOKS first (confirmed real, checked by default), then
    every other book from get_sportsbook_list() - SharpAPI's live
    sportsbooks list when reachable, else the static SPORTSBOOK_CATALOG
    fallback - unchecked by default. A catalog id already covered by
    SHARPAPI_BOOKS is skipped to avoid listing it twice."""
    plan_books = [b.strip() for b in SHARPAPI_BOOKS.split(",") if b.strip()]
    plan_ids = {_normalize_book(b) for b in plan_books}

    catalog = [
        {"id": b, "display_name": _book_display_name(b), "tier": None, "preselected": True}
        for b in plan_books
    ]
    for b in get_sportsbook_list():
        if _normalize_book(b["id"]) in plan_ids:
            continue
        catalog.append({
            "id": b["id"],
            "display_name": b.get("display_name") or _book_display_name(b["id"]),
            "tier": b.get("tier"),
            "preselected": False,
        })
    return catalog

# Mock data used only when no API key is set, so the site is viewable
# immediately without any setup. Once SHARPAPI_KEY is set, real data is used.
MOCK_ARBS = [
    {
        "event_name": "Lakers vs Celtics",
        "league": "NBA",
        "market": "Moneyline",
        "profit_percent": 3.4,
        "event_start_time": "2026-10-22T23:30:00Z",
        "is_live": False,
        "legs": [
            {"sportsbook": "DraftKings", "selection": "Lakers ML", "odds_american": "+150", "stake_percent": 42.0},
            {"sportsbook": "FanDuel", "selection": "Celtics ML", "odds_american": "-120", "stake_percent": 58.0},
        ],
    },
    {
        "event_name": "Chiefs vs Broncos",
        "league": "NFL",
        "market": "Point Spread (-2.5)",
        "profit_percent": 1.8,
        "event_start_time": "2026-09-15T00:15:00Z",
        "is_live": True,
        "legs": [
            {"sportsbook": "BetMGM", "selection": "Chiefs -2.5", "odds_american": "+105", "stake_percent": 49.0},
            {"sportsbook": "BetRivers", "selection": "Broncos +2.5", "odds_american": "-105", "stake_percent": 51.0},
        ],
    },
]


def _format_american_odds(value):
    if isinstance(value, (int, float)) and value > 0:
        return f"+{value}"
    return str(value)


# Matches "Total <Noun>" inside a market label (e.g. "Total Sets (2.5)" ->
# "Sets", "3Rd Set Total Games (12.5)" -> "Games") to recover a display
# unit for over/under legs - see _format_leg_selection.
_TOTAL_UNIT_RE = re.compile(r"total\s+([a-z]+)", re.IGNORECASE)


def _format_leg_selection(selection, selection_type, line, market_label):
    """Builds a leg's display selection to include its line, not just the
    bare word SharpAPI sends in "selection" - confirmed against real rows
    that "selection" is literally just "Over"/"Under" for totals, or just
    the bare team name ("KC Chiefs") for spreads, with the line always a
    separate field. A table full of legs all reading "Over"/"Under" with
    no indication of the line or what market it's on isn't usable once
    there's more than one total market on the same slate (e.g. a tennis
    card mixing "Total Sets" and "3rd Set Total Games" - both would show
    identical bare "Over"/"Under" chips with nothing to tell them apart).

    Over/under-style legs (by selection_type or the bare selection text,
    since SharpAPI's Arbitrage leg schema doesn't confirm selection_type
    is always present) get the line plus a unit word scraped from the
    market label when the label fits the "Total <noun>" pattern - "Under"
    + line 2.5 + market_label "Total Sets (2.5)" -> "Under 2.5 Sets".
    Falls back to just the line with no unit word if no such pattern is
    found in the label, rather than guessing at one.

    Spread-style legs (a team name paired with a signed line) get the
    signed line appended instead - "KC Chiefs" + line -0.5 -> "KC Chiefs
    -0.5" - no unit word attempted there, teams aren't measured in units.

    Anything without a usable numeric line (moneyline, draw, outright)
    passes through unchanged."""
    if not isinstance(line, (int, float)):
        return selection

    sel_lower = (selection or "").strip().lower()
    if selection_type in ("over", "under") or sel_lower in ("over", "under"):
        unit_match = _TOTAL_UNIT_RE.search(market_label or "")
        unit = f" {unit_match.group(1).capitalize()}" if unit_match else ""
        return f"{selection} {line:g}{unit}"

    return f"{selection} {line:+g}"


def _with_deep_link_fallback(deep_link, sportsbook_display_name):
    """Appends SharpAPI's confirmed-real ?fallback= param to a deep_link,
    so a link that's gone stale/expired by click time (confirmed real
    scenario - the BetRivers "Deep link ID not found" bug, see
    MAX_LIVE_ROW_AGE_SECONDS above) takes the user somewhere useful
    instead of SharpAPI's raw {"error": {"code": "not_found", ...}} JSON.
    Points at a search for the book's name rather than a guessed homepage
    URL, since this project avoids hand-guessing real-world URLs.

    deep_link may already carry a query string (e.g. "?state=nj", either
    baked in server-side from our own SHARPAPI_STATE on the /odds request,
    or appended fresh for arb legs) - detect that to use "&" vs "?"."""
    if not deep_link:
        return deep_link
    fallback_url = f"https://www.google.com/search?q={quote((sportsbook_display_name or '') + ' sportsbook')}"
    separator = "&" if "?" in deep_link else "?"
    return f"{deep_link}{separator}fallback={quote(fallback_url, safe='')}"


# Confirmed real warning flags for GET /api/v1/opportunities/arbitrage (per
# SharpAPI's own docs). Rejecting an explicit set instead of a substring
# guess ("SUSPICIOUS"/"STALE" in w) because the substring guess would have
# missed LOW_IMPLIED_TOTAL (reserved, not currently emitted, but a real
# data-quality flag when it is: "verify the prices before betting" - no
# "SUSPICIOUS" or "STALE" substring at all). LIVE_GAME alone is purely
# informational and must NOT cause rejection - it's not in this set.
_ARB_REJECT_WARNINGS = {
    "LIVE_HIGH_PROFIT_SUSPICIOUS",
    "HIGH_PROFIT_SUSPICIOUS",
    "LIVE_STALE_ODDS",
    "POTENTIALLY_STALE_ODDS",
    "VERY_STALE_ODDS",
    "LOW_IMPLIED_TOTAL",
}


def fetch_arbs_paid(min_profit=0.0, books=None, sport=None, league=None):
    """Calls SharpAPI's pre-computed arbitrage endpoint - confirmed real,
    field-checked against SharpAPI's own docs and a real response (GET
    /api/v1/opportunities/arbitrage, Hobby tier+, which this site's plan
    meets). min_profit/sportsbook/sport/league/state are all confirmed
    real query params for this specific endpoint (previously some were
    guessed). Un-namespaced paths like /api/v1/arbitrage are a documented
    410 Gone with a correct_endpoint pointer, which is why we've always
    used the /opportunities/ prefix here.

    min_profit defaults to 0.0 (not SharpAPI's own suggested 0.5) to match
    compute_arbs_from_odds()'s default - the same "show every genuine arb,
    let the user's own judgment size it" philosophy on both paths, rather
    than the paid endpoint silently applying a stricter floor than the
    odds-scan fallback would for the same request.

    api_arbs() below falls back to compute_arbs_from_odds() (built from
    the confirmed-real /odds endpoint) if this fails for any reason."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    params = {"min_profit": min_profit, "sportsbook": books or SHARPAPI_BOOKS}
    if sport:
        params["sport"] = sport
    if league:
        params["league"] = league
    if SHARPAPI_STATE:
        params["state"] = SHARPAPI_STATE
    resp = requests.get(
        f"{SHARPAPI_BASE_URL}/api/v1/opportunities/arbitrage",
        headers=headers,
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])

    allowed_books = {_normalize_book(b) for b in (books or SHARPAPI_BOOKS).split(",")}

    arbs = []
    for arb in data:
        if arb.get("possibly_stale"):
            continue
        if _ARB_REJECT_WARNINGS.intersection(arb.get("warnings", [])):
            continue
        # Same exclusion as compute_arbs_from_odds() - player props are
        # excluded site-wide, and this endpoint has its own is_player_prop
        # flag we weren't previously checking at all.
        if arb.get("is_player_prop"):
            continue

        profit_percent = arb.get("profit_percent", 0)
        if profit_percent < min_profit or profit_percent > MAX_SANE_PROFIT_PERCENT:
            continue

        raw_legs = arb.get("legs", [])
        if len(raw_legs) < 2:
            continue
        if not all(_normalize_book(leg.get("sportsbook")) in allowed_books for leg in raw_legs):
            continue
        if not _legs_form_valid_arb(raw_legs):
            continue

        league_label = arb.get("league_label") or arb.get("league", "")
        market_label = arb.get("market_label", "")

        arbs.append({
            "event_name": arb.get("event_name", ""),
            "league": league_label,
            "market": market_label,
            "profit_percent": profit_percent,
            # Confirmed field is "start_time", not "event_start_time" (the
            # odds-scan path's field name) - reading the wrong key here
            # meant every card from this path showed "Start time unknown".
            "event_start_time": arb.get("start_time"),
            "is_live": bool(arb.get("is_live", False)),
            "legs": [
                {
                    "sportsbook": leg.get("sportsbook", ""),
                    "selection": _format_leg_selection(
                        leg.get("selection", ""), leg.get("selection_type"), leg.get("line"), market_label
                    ),
                    "odds_american": _format_american_odds(leg.get("odds_american")),
                    "stake_percent": leg.get("stake_percent", 0),
                    # The server appends ?state= to this itself based on
                    # our request's own state param (see SHARPAPI_STATE) -
                    # explicit null (e.g. exchange legs with no resolver)
                    # passes through unchanged, otherwise gets ?fallback=
                    # appended (see _with_deep_link_fallback).
                    "deep_link": _with_deep_link_fallback(
                        leg.get("deep_link"), _book_display_name(leg.get("sportsbook", ""))
                    ),
                }
                for leg in raw_legs
            ],
        })
    return arbs


def fetch_odds_for_book(sportsbook, sport, league=None, limit=ODDS_PAGE_LIMIT):
    """Calls SharpAPI's confirmed-real /odds endpoint for one sportsbook,
    following cursor-based pagination up to MAX_ODDS_PAGES_PER_BOOK pages
    when a slate has more rows than fit on one page (offset-based paging
    is capped at 500 by SharpAPI and isn't used here - cursor is what
    their own docs recommend for "any multi-page scan", and is immune to
    the row-drift offset pagination can suffer against live data).

    Response shape per SharpAPI's own "Response Conventions" docs (the
    paginated-list shape): {"data": [{...row}], "pagination": {...},
    "updated_at": "..."} - "pagination" is a top-level sibling of "data",
    not nested under a "meta" key. Each row has event_id, sportsbook,
    market_type, selection, selection_type, line, odds_decimal,
    odds_american, is_active, is_player_prop, timestamp, home_team,
    away_team, league, etc.

    Only the FIRST page's failure propagates (a book that's tier-restricted
    or not in your SharpAPI dashboard selection fails on page 1, exactly
    as before - see book_issues upstream). A failure on page 2+ just stops
    pagination and returns whatever was already collected, rather than
    discarding a partially-successful fetch.

    league=None omits the league filter entirely (all leagues for this
    sport) rather than falling back to a default - callers that want a
    specific league must pass one explicitly.

    Sends ?state=SHARPAPI_STATE when set - confirmed required for correct
    deep_link resolution on BetMGM/Caesars/BetRivers (state-dependent
    sportsbook domains); harmless no-op for every other book."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    base_params = {"sport": sport, "sportsbook": sportsbook, "limit": limit}
    if league:
        base_params["league"] = league
    if SHARPAPI_STATE:
        base_params["state"] = SHARPAPI_STATE

    resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/odds", headers=headers, params=base_params, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    all_rows = list(body.get("data", []))
    pagination = body.get("pagination", {})
    cursor = pagination.get("next_cursor")

    page = 1
    while pagination.get("has_more") and cursor and page < MAX_ODDS_PAGES_PER_BOOK:
        try:
            next_resp = requests.get(
                f"{SHARPAPI_BASE_URL}/api/v1/odds",
                headers=headers,
                params={**base_params, "cursor": cursor},
                timeout=10,
            )
            next_resp.raise_for_status()
            body = next_resp.json()
        except requests.RequestException:
            break
        all_rows.extend(body.get("data", []))
        pagination = body.get("pagination", {})
        cursor = pagination.get("next_cursor")
        page += 1

    return all_rows


def _error_code_from_response(exc):
    """Pulls SharpAPI's machine-readable error.code out of a failed
    request's response body, e.g. "tier_restricted" or "book_not_selected"
    (confirmed error codes per SharpAPI's docs - the latter is distinct
    from tier_restricted: a book your plan tier allows but that isn't
    enabled in your SharpAPI dashboard's own book selection). Falls back to
    a generic label when the body isn't the documented error envelope."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return "request_failed"
    try:
        code = resp.json().get("error", {}).get("code")
    except ValueError:
        code = None
    return code or f"http_{resp.status_code}"


def fetch_all_odds(books, sport, league=None):
    """Pulls one page of odds per sportsbook (for one sport, optionally one
    league) and combines them. A book that errors out (bad id, temporary
    outage, tier/dashboard restriction) is skipped rather than failing the
    whole scan - but its reason is captured in the returned book_issues
    dict (book id -> SharpAPI error code) so the caller can tell the user
    WHY a toggled book contributed nothing, rather than silent emptiness.
    Returns (rows, book_issues)."""
    all_rows = []
    book_issues = {}
    for book in books:
        try:
            all_rows.extend(fetch_odds_for_book(book, sport=sport, league=league))
        except requests.RequestException as e:
            book_issues[book] = _error_code_from_response(e)
    return all_rows, book_issues


def fetch_all_odds_for_sports(books, sports, league=None):
    """Loops fetch_all_odds() across every given sport, merging everything
    into one row list. This is what powers "scan everything" - it multiplies
    request count by len(sports), so it's meaningfully heavier than a
    single-sport scan and shouldn't be run on a short auto-refresh timer.
    Returns (rows, book_issues) - a book only ends up in book_issues if it
    contributed zero rows across EVERY sport scanned, so a book that fails
    on one sport (e.g. no coverage) but works on another isn't flagged as
    broken."""
    all_rows = []
    contributed = set()
    book_issues = {}
    for sport in sports:
        rows, issues = fetch_all_odds(books, sport, league=league)
        all_rows.extend(rows)
        contributed.update(_normalize_book(r.get("sportsbook")) for r in rows)
        book_issues.update(issues)
    for book in list(book_issues):
        if _normalize_book(book) in contributed:
            del book_issues[book]
    return all_rows, book_issues


def _canonical_event_id(event_id):
    """Strips a trailing doubleheader suffix (_g{N}) from a canonical
    event_id - confirmed by SharpAPI's own Event Matching docs as the safe
    "same-fixture collapse": event_id is built from
    {league}_{teamA}_{teamB}_{date}_b{N} (a 6-hour start-time bucket), with
    an optional trailing _g{N} added only when a book reports both games
    of a same-day doubleheader in one update. A book that only sees one of
    the two games emits the bare bucketed id - so the SAME physical game
    can carry two different event_id strings across books, and grouping
    strictly by raw event_id (the previous behavior here) could silently
    miss a real cross-book arb whenever that split happens to occur.

    Only strips _g{N}, never the _b{N} bucket - SharpAPI's own docs
    explicitly warn that also stripping _b{N} ("loose matchup grouping")
    can collapse a genuine same-day doubleheader into one group, which
    would be actively dangerous here: a fake arb pairing legs from two
    different games. This narrower stripping is what SharpAPI says
    "mirrors the server-side same-event predicate" - i.e. the same
    normalization their own cross-book endpoints use internally."""
    return re.sub(r"_g\d+$", "", event_id or "")


def _canonical_line(row):
    """Normalizes a spread row's line to be relative to the HOME team,
    regardless of which side (home or away) this particular row represents.

    Why this matters: two books can each list their own team as the
    favorite for the same real-world market segment (e.g. DraftKings has
    the home team -0.5, BetMGM independently has the away team -0.5 for
    the same 1st-quarter spread) - both are "my team wins outright" bets,
    not true complements of each other. If the segment ties, NEITHER
    "-0.5, must win outright" bet cashes, so pairing them as if they hedge
    each other is wrong and can show a large fake "arb" on exactly the kind
    of low-sample, high-disagreement market (1st quarter/half segments)
    where books are most likely to differ on who's favored.

    Within one self-consistent single-book market, home_line == -away_line
    always holds, so negating an away-side row's line converts it to what
    the home team's line would be if this book agreed with itself the
    normal way. Two rows can only be true complements of the same
    underlying proposition if this canonical value matches exactly -
    matching on raw magnitude alone (the old approach) can't tell two
    books' conflicting "who's favored" opinions apart.

    Uses selection_type, not team_side, to detect the away side - team_side
    turned out to be missing entirely from real MLB run_line rows (present
    for NFL spreads, absent here), which silently defeated this whole check
    for baseball and let the exact same bug back in. selection_type is
    "home"/"away" for every 2-way team spread market seen across every
    sport pulled so far (NFL, soccer, MLB) and is never missing, since it's
    also the field everything else in this function already keys off of.

    Rows with no numeric line (moneylines) or no team framing (totals,
    over/under share the same line already) pass through unchanged."""
    line = row.get("line")
    if not isinstance(line, (int, float)):
        return line
    return -line if row.get("selection_type") == "away" else line


def _is_stale_live_row(row, now=None):
    """True if a LIVE row's own price is too old to trust, since
    is_stale_pregame_price doesn't apply to live rows at all (see
    MAX_LIVE_ROW_AGE_SECONDS above). Non-live rows always return False here;
    their staleness is covered separately by is_stale_pregame_price.

    Uses "timestamp" - confirmed the ONLY freshness field on an odds row as
    of SharpAPI's current API version (their own Odds Delta changelog: "the
    odds response now carries a single timestamp field... former
    odds_changed_at, last_seen_at, and wire_received_at fields have been
    removed"; every detailed schema table since - /odds, /odds/delta,
    /odds/best, /odds/comparison, /odds/batch, /odds/closing - lists only
    this field, no "fetched_at" despite an earlier, more general docs page
    mentioning one). Per SharpAPI's own description it's "the time SharpAPI
    last refreshed that row through its pipeline... not when the price last
    moved" - imperfect, but the only signal that exists, and still able to
    catch the real bug this check was built for (see above).

    A live row with a missing or unparseable timestamp is treated as stale
    rather than assumed fresh - we can't verify it's current, and showing a
    fake arb is worse than hiding a real one."""
    if not row.get("is_live"):
        return False

    raw_ts = row.get("timestamp")
    if not raw_ts:
        return True

    try:
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True

    now = now or datetime.now(timezone.utc)
    age_seconds = (now - ts).total_seconds()
    return age_seconds > MAX_LIVE_ROW_AGE_SECONDS


def _legs_form_valid_arb(legs):
    """The non-negotiable rules for what is allowed to be shown as an
    arbitrage opportunity on this site. This tool is arbitrage-only - every
    leg must be a real, independently-placeable bet on the SAME real-world
    outcome-set, priced by DIFFERENT books, or it isn't arbitrage and must
    never be shown:

    1. Every leg's sportsbook must be different from every other leg's.
       Two prices from the same book are not a hedge (the book will notice
       and can void/limit it), and shouldn't even be possible to observe as
       profitable under normal pricing.
    2. Every leg must be the same event, same market, same players/teams,
       and truly complementary sides of the SAME line (not just matching
       magnitude - see _canonical_line) - all enforced upstream by the
       (event_id, market_type, canonical_line) grouping key this is only
       ever called against, never by this function itself.
       (A market with mixed players, e.g. two different WNBA players'
       point props sharing a market_type, must be excluded from the row
       data before it ever reaches grouping - see the "player" checks in
       compute_arbs_from_odds - not handled here.)

    Called once, right before accepting a candidate arb, so a future change
    to the matching logic above can't silently drop this check.
    """
    leg_books = [_normalize_book(leg.get("sportsbook", "")) for leg in legs]
    return len(set(leg_books)) == len(legs)


def compute_arbs_from_odds(rows, min_profit=0.0, books=None):
    """Groups odds rows into markets and flags any where the best price per
    outcome, taken across whichever books cover it, has a combined implied
    probability under 100% (an arbitrage).

    Grouping key is (event_id, market_type, canonical_line) — NOT market_id,
    which is sportsbook-specific (DraftKings and FanDuel each mint their own
    market_id for the identical real-world bet, so grouping by it would
    never find a cross-book match). event_id/market_type are the fields
    that stay consistent across books for the same bet; the line is run
    through _canonical_line() rather than compared directly or by raw
    magnitude, because two books can each favor a DIFFERENT team for the
    same market (DraftKings has the home team -0.5, BetMGM independently
    has the away team -0.5) - matching by magnitude alone would pair two
    books' conflicting "my team is favored" bets as if they were opposite,
    complementary sides of one market, when they aren't (if the segment
    ties, neither actually wins). See _canonical_line()'s docstring.

    Player-prop markets are skipped entirely: market_type alone doesn't
    say WHICH player a row is about (two different players' passing-yards
    props share the same market_type), and matching that reliably would
    mean parsing player names out of free-text selection strings — too
    fragile to trust with real money. Checked two ways: SharpAPI's own
    is_player_prop flag, AND the literal word "player" in market_type -
    the flag alone let a real case through (a WNBA "1st quarter player
    points" market where is_player_prop was apparently not set), matching
    two different players' point totals against each other as if they were
    one 2-way market. Every player-prop market_type actually seen so far
    (NFL passing/rushing/receiving yards, WNBA player points, etc.) has
    literally contained "player" in its name, so this substring check is a
    reliable backstop independent of whether the flag is trustworthy.

    Rows SharpAPI itself flags as is_stale_pregame_price are dropped before
    ever entering the "best price" comparison - an old, unrefreshed price
    can otherwise get picked as the "best" price for a side purely because
    it happens to be higher, producing an arb against a number that isn't
    actually live/bettable anymore. That flag only covers PREGAME prices
    though - a live row is separately checked via _is_stale_live_row(),
    since a live price can go stale (e.g. right after a goal) with no flag
    at all marking it as such - see MAX_LIVE_ROW_AGE_SECONDS.
    """
    allowed_books = {_normalize_book(b) for b in (books or SHARPAPI_BOOKS).split(",")}

    markets = {}
    for row in rows:
        if not row.get("is_active", True):
            continue
        if row.get("is_player_prop"):
            continue
        if "player" in (row.get("market_type") or "").lower():
            continue
        if row.get("is_stale_pregame_price"):
            continue
        if _is_stale_live_row(row):
            continue
        if not isinstance(row.get("odds_decimal"), (int, float)) or row["odds_decimal"] <= 1:
            continue
        if allowed_books and _normalize_book(row.get("sportsbook")) not in allowed_books:
            continue

        event_id = row.get("event_id")
        market_type = row.get("market_type")
        if not event_id or not market_type:
            continue
        event_id = _canonical_event_id(event_id)

        line_key = _canonical_line(row)

        markets.setdefault((event_id, market_type, line_key), []).append(row)

    arbs = []
    for (event_id, market_type, line_key), entries in markets.items():
        unique_selection_types = {e.get("selection_type") for e in entries}
        if len(unique_selection_types) not in (2, 3):
            continue  # need a clean 2- or 3-way market to form a full hedge

        best_by_selection = {}
        for e in entries:
            sel = e.get("selection_type")
            if sel not in best_by_selection or e["odds_decimal"] > best_by_selection[sel]["odds_decimal"]:
                best_by_selection[sel] = e

        legs = list(best_by_selection.values())
        if len(legs) < 2 or len(legs) != len(unique_selection_types):
            continue  # a selection with no usable price means the market isn't fully covered

        if not _legs_form_valid_arb(legs):
            continue

        implied_sum = sum(1.0 / leg["odds_decimal"] for leg in legs)
        if implied_sum >= 1.0:
            continue

        profit_percent = (1.0 / implied_sum - 1.0) * 100
        if profit_percent < min_profit or profit_percent > MAX_SANE_PROFIT_PERCENT:
            continue

        sample = legs[0]
        away = sample.get("away_team", "")
        home = sample.get("home_team", "")
        league = sample.get("league", "")
        market_label = market_type.replace("_", " ").title()
        if isinstance(line_key, (int, float)):
            market_label += f" ({line_key:g})"

        arb_legs = []
        for leg in legs:
            stake_percent = (1.0 / leg["odds_decimal"]) / implied_sum * 100
            display_name = _book_display_name(_normalize_book(leg.get("sportsbook", "")))
            arb_legs.append({
                "sportsbook": display_name,
                "selection": _format_leg_selection(
                    leg.get("selection", ""), leg.get("selection_type"), leg.get("line"), market_label
                ),
                "odds_american": _format_american_odds(leg.get("odds_american")),
                "stake_percent": round(stake_percent, 2),
                "deep_link": _with_deep_link_fallback(leg.get("deep_link"), display_name),
            })

        arbs.append({
            "event_name": f"{away} @ {home}" if away and home else event_id,
            "league": league.upper() if league else "",
            "market": market_label,
            "profit_percent": round(profit_percent, 2),
            "event_start_time": sample.get("event_start_time"),
            # Any leg reporting live also means the market itself is live -
            # the legs are the same real-world event, so True from any one
            # of them is enough (a stale/lagging leg reporting False
            # shouldn't hide that the event has actually started).
            "is_live": any(bool(leg.get("is_live")) for leg in legs),
            "legs": arb_legs,
        })

    arbs.sort(key=lambda a: a["profit_percent"], reverse=True)
    return arbs


def fetch_sports():
    """Calls SharpAPI's sports-list endpoint - confirmed real and field-
    checked against SharpAPI's own docs (GET /api/v1/sports, Free tier).
    Each row's display field is "name" (confirmed, e.g. {"id": "tennis",
    "name": "Tennis", ...}) - "label" kept only as a defensive fallback,
    never actually seen on this endpoint. Note: SharpAPI's own Sports docs
    page says unauthenticated requests get 401, directly contradicting an
    earlier Authentication docs page that listed this as a public endpoint
    reachable without a key - a real inconsistency in their docs, but moot
    for us either way since we always send the API key."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/sports", headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return [
        {"id": s.get("id"), "name": s.get("name") or s.get("label") or s.get("id")}
        for s in data
        if s.get("id")
    ]


def fetch_leagues(sport):
    """Calls SharpAPI's leagues-list endpoint for one sport - confirmed
    real and field-checked (GET /api/v1/leagues, Free tier). Each row's
    display field is "display_name" (confirmed, e.g. {"id": "nba",
    "display_name": "NBA", ...}) - NOT "label", despite sports using a
    different field ("name") for the same purpose on its own endpoint.
    This previously checked "label" before "display_name", which doesn't
    exist on real league rows at all - every league dropdown entry was
    silently falling through to the raw id slug (e.g.
    "england_-_premier_league" instead of "England - Premier League")."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(
        f"{SHARPAPI_BASE_URL}/api/v1/leagues", headers=headers, params={"sport": sport}, timeout=10
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return [
        {"id": l.get("id"), "name": l.get("display_name") or l.get("label") or l.get("name") or l.get("id")}
        for l in data
        if l.get("id")
    ]


def get_cached_sport_ids():
    """Best-effort sport id list for 'scan everything' mode - shares the
    same cache /api/sports populates, but never raises; falls back to the
    fixed list if SharpAPI's sports endpoint isn't reachable, so a "scan
    everything" request always has something to loop over."""
    global _sports_cache
    if _sports_cache and time.time() - _sports_cache[0] < SPORTS_CACHE_TTL_SECONDS:
        return [s["id"] for s in _sports_cache[1]]
    try:
        sports = fetch_sports()
        _sports_cache = (time.time(), sports)
        return [s["id"] for s in sports]
    except Exception:
        return [s["id"] for s in FALLBACK_SPORTS]


@app.route("/api/sports")
def api_sports():
    global _sports_cache
    if not SHARPAPI_KEY:
        return jsonify({"source": "mock", "sports": FALLBACK_SPORTS, "preferred": PREFERRED_SPORTS})

    if _sports_cache and time.time() - _sports_cache[0] < SPORTS_CACHE_TTL_SECONDS:
        return jsonify({"source": "live", "sports": _sports_cache[1], "preferred": PREFERRED_SPORTS})

    try:
        sports = fetch_sports()
        _sports_cache = (time.time(), sports)
        return jsonify({"source": "live", "sports": sports, "preferred": PREFERRED_SPORTS})
    except Exception as e:
        return jsonify({"source": "error", "error": str(e), "sports": FALLBACK_SPORTS, "preferred": PREFERRED_SPORTS}), 200


@app.route("/api/leagues")
def api_leagues():
    sport = request.args.get("sport", "")
    if not sport:
        return jsonify({"source": "error", "error": "missing sport param", "leagues": []}), 200

    if not SHARPAPI_KEY:
        return jsonify({"source": "mock", "leagues": FALLBACK_LEAGUES.get(sport, [])})

    cached = _leagues_cache.get(sport)
    if cached and time.time() - cached[0] < SPORTS_CACHE_TTL_SECONDS:
        return jsonify({"source": "live", "leagues": cached[1]})

    try:
        leagues = fetch_leagues(sport)
        _leagues_cache[sport] = (time.time(), leagues)
        return jsonify({"source": "live", "leagues": leagues})
    except Exception as e:
        return jsonify({"source": "error", "error": str(e), "leagues": FALLBACK_LEAGUES.get(sport, [])}), 200


@app.route("/api/books")
def api_books():
    return jsonify({"source": "live" if SHARPAPI_KEY else "mock", "books": _book_catalog()})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/test-odds")
def api_test_odds():
    """Diagnostic route to confirm the API key + sport/league params work
    at all against the /odds endpoint."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    params = {
        "sport": request.args.get("sport", DEFAULT_SPORT),
        "league": request.args.get("league", DEFAULT_LEAGUE),
        "sportsbook": request.args.get("sportsbook", "draftkings"),
    }
    resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/odds", headers=headers, params=params, timeout=10)
    return jsonify({"status_code": resp.status_code, "body": resp.text[:800]})


@app.route("/api/arbs")
def api_arbs():
    if not SHARPAPI_KEY:
        return jsonify({"source": "mock", "arbs": MOCK_ARBS})

    selected_books = request.args.get("books")  # comma-separated, from the toggles
    sport_param = request.args.get("sport", DEFAULT_SPORT)  # comma-separated sport ids, or "all"
    league_param = request.args.get("league", DEFAULT_LEAGUE)

    scan_all_sports = sport_param == "all"
    sport_ids = [] if scan_all_sports else [s.strip() for s in sport_param.split(",") if s.strip()]
    # More than one sport means "scan every league within each of them" -
    # a single league selection can't apply across different sports, so
    # multi-sport mode always omits the league filter, same as "all leagues".
    multi_sport = len(sport_ids) > 1
    scan_all_leagues = league_param == "all" or multi_sport

    cache_key = (selected_books or SHARPAPI_BOOKS, sport_param, league_param)
    cached = _arbs_cache.get(cache_key)
    if cached and time.time() - cached[0] < ARBS_CACHE_TTL_SECONDS:
        return jsonify(cached[1])

    resolved_books = (selected_books or SHARPAPI_BOOKS).split(",")

    arbs = None
    mode = None
    rows_scanned = None
    # Per-book reason a toggled-on book contributed nothing (SharpAPI error
    # code, e.g. "tier_restricted" or "book_not_selected" - confirmed
    # distinct codes: the former means your plan tier doesn't cover this
    # book at all, the latter means your plan tier DOES cover it but it
    # isn't enabled in your SharpAPI dashboard's own book selection). Only
    # populated on the odds-scan path, which queries one book at a time -
    # the pre-computed opportunities endpoint doesn't expose this per book.
    book_issues = {}

    # The pre-computed arbitrage endpoint only makes sense for one
    # sport/league at a time, so "scan everything" always goes straight to
    # the odds-scan path below. This branch guarantees exactly one sport
    # and one specific league are selected, so both are always safe to pass.
    if not scan_all_sports and not multi_sport and not scan_all_leagues:
        try:
            one_sport = sport_ids[0] if sport_ids else DEFAULT_SPORT
            arbs = fetch_arbs_paid(books=selected_books, sport=one_sport, league=league_param)
            mode = "paid_endpoint"
        except Exception:
            pass  # falls back to the odds-scan path below on any failure

    if arbs is None:
        try:
            if scan_all_sports:
                sports_to_scan = get_cached_sport_ids()
                rows, book_issues = fetch_all_odds_for_sports(resolved_books, sports_to_scan, league=None)
                mode = "odds_scan_all_sports"
            elif multi_sport:
                rows, book_issues = fetch_all_odds_for_sports(resolved_books, sport_ids, league=None)
                mode = "odds_scan_multi_sport"
            else:
                one_sport = sport_ids[0] if sport_ids else DEFAULT_SPORT
                rows, book_issues = fetch_all_odds(
                    resolved_books, one_sport, league=None if scan_all_leagues else league_param
                )
                mode = "odds_scan"
            rows_scanned = len(rows)
            arbs = compute_arbs_from_odds(rows, min_profit=0.0, books=",".join(resolved_books))
        except Exception as e:
            return jsonify({"source": "error", "error": str(e), "arbs": MOCK_ARBS}), 200

    # rows_scanned=0 on an odds_scan means SharpAPI returned nothing at all
    # for this sport/league/book combination (worth investigating) - as
    # opposed to rows_scanned>0 with zero arbs, which just means real prices
    # were found but none of them crossed into arbitrage territory (normal).
    payload = {
        "source": "live",
        "mode": mode,
        "rows_scanned": rows_scanned,
        "book_issues": book_issues,
        "arbs": arbs,
    }
    _arbs_cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
