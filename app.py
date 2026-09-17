import os
import re
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE_URL = os.environ.get("ODDS_API_BASE_URL", "https://api.the-odds-api.com")

# Confirmed real bookmaker keys from The Odds API's own "bookmaker-apis.html"
# docs page (pasted by the site's owner). Mixing keys from different regions
# in one request is explicitly documented as supported ("bookmakers can be
# from any region"), which is what lets this default set combine
# confirmed-accurate US-wide books (FanDuel, DraftKings, theScore Bet -
# confirmed accurate for Ontario by direct real-world comparison against
# this site's SharpAPI build, which never caught a bad price on any of
# these three) with the Ontario-specific book variants (BetMGM, BetRivers,
# Betano) that DO need the region-specific key to get the right price -
# exactly the class of bug (Caesars showing a US price in Ontario) that
# motivated testing this provider in the first place.
ODDS_API_BOOKS = os.environ.get(
    "ODDS_API_BOOKS",
    "fanduel,draftkings,betmgm_ca_on,betrivers_ca_on,betano_ca_on,espnbet",
)

# Only h2h (moneyline), spreads and totals are ever requested. Player props
# are technically available on this API but only through a much more
# expensive per-event call (one request per game, not one request per
# sport) - and this site excludes player props from arb detection entirely
# regardless of provider (matching two different players' prop lines
# reliably requires parsing free-text names, judged too fragile to trust
# with real money in the SharpAPI build this one is being compared against).
MARKETS = "h2h,spreads,totals"

# Real cross-book arbs are almost always single-digit percentages - anything
# wildly above that is far more likely to be a data/matching glitch than
# free money. Same philosophy and same starting value as the SharpAPI
# build's MAX_SANE_PROFIT_PERCENT (tightened there after two confirmed real
# incidents let a 25% cap through) - starting conservative here too rather
# than re-learning that lesson from scratch on a second provider.
MAX_SANE_PROFIT_PERCENT = 8.0

# Short in-memory cache so rapid toggle-clicking or multiple open tabs don't
# burn through the API's usage quota. The Odds API bills per-request
# (quota credits = markets x regions per call, not a requests-per-minute
# limit like SharpAPI's Hobby tier), so the exact safe auto-refresh cadence
# for this provider is still unconfirmed - this cache is a cheap first
# layer of protection independent of that open question.
ARBS_CACHE_TTL_SECONDS = 3
_arbs_cache = {}  # cache key -> (timestamp, response_dict)

# /v4/sports changes rarely and costs nothing to call (confirmed: "This
# endpoint does not count against the usage quota"), but it's still cached
# to avoid a redundant network round-trip on every page load.
SPORTS_CACHE_TTL_SECONDS = 3600
_sports_cache = None  # (timestamp, [{"key","group","title","description","active","has_outrights"}, ...])

# Scanning "All Leagues" within a group (or multiple sport groups, or "All
# Sports") means one API call per league key, since The Odds API's /odds
# endpoint only ever accepts a single sport key per request (no combined
# multi-league query, confirmed - "upcoming" is the only special value and
# it spans ALL sports rather than letting you scope to one group). Bounded
# here so a broad selection can't silently burn through an unknown quota in
# one scan - revisit this number once the account's actual plan/quota is
# confirmed (still an open question as of this build).
MAX_LEAGUES_PER_SCAN = 6

# The sport groups this site's owner actually watches day to day -
# preselected by default wherever the real /v4/sports response includes
# them. Mirrors the same default sports as the SharpAPI build for an
# apples-to-apples comparison between the two providers.
PREFERRED_GROUPS = ["American Football", "Basketball", "Ice Hockey", "Baseball", "Soccer", "Tennis"]

# Only used if /v4/sports fails or no key is set - a short, confirmed-real
# subset transcribed from The Odds API's own Quick Start example response,
# not an exhaustive list (soccer alone has dozens of real leagues on this
# API) - just enough for the site to still function without a live call.
FALLBACK_SPORTS_RAW = [
    {"key": "americanfootball_nfl", "group": "American Football", "title": "NFL", "active": True},
    {"key": "americanfootball_ncaaf", "group": "American Football", "title": "NCAAF", "active": True},
    {"key": "basketball_nba", "group": "Basketball", "title": "NBA", "active": True},
    {"key": "icehockey_nhl", "group": "Ice Hockey", "title": "NHL", "active": True},
    {"key": "baseball_mlb", "group": "Baseball", "title": "MLB", "active": True},
    {"key": "soccer_usa_mls", "group": "Soccer", "title": "MLS", "active": True},
    {"key": "mma_mixed_martial_arts", "group": "Mixed Martial Arts", "title": "MMA", "active": True},
]

DEFAULT_GROUP = os.environ.get("ODDS_API_SPORT_GROUP", "American Football")
DEFAULT_LEAGUE_KEY = os.environ.get("ODDS_API_LEAGUE", "americanfootball_nfl")

# Transcribed from The Odds API's own "bookmaker-apis.html" docs page
# (pasted by the site's owner) - every book with a "(Note)" of "Only
# available on paid subscriptions" is marked paid_only=True. This is a
# STATIC catalog (unlike SharpAPI, this API has no live "list every
# bookmaker" endpoint to fall back to/from), used only to populate the
# Sportsbooks toggle panel before any odds have actually been fetched -
# once a real /odds response comes back, its own per-bookmaker "title"
# field is used for display instead of this catalog (see
# compute_arbs_from_events), so a display-name mismatch here never affects
# what's shown on an actual arb card, only the toggle list's label.
BOOKMAKER_CATALOG = [
    # US
    {"id": "betonlineag", "display_name": "BetOnline.ag", "paid_only": False},
    {"id": "betmgm", "display_name": "BetMGM (US)", "paid_only": False},
    {"id": "betrivers", "display_name": "BetRivers (US)", "paid_only": False},
    {"id": "betus", "display_name": "BetUS", "paid_only": False},
    {"id": "bovada", "display_name": "Bovada", "paid_only": False},
    {"id": "williamhill_us", "display_name": "Caesars", "paid_only": True},
    {"id": "draftkings", "display_name": "DraftKings", "paid_only": False},
    {"id": "fanatics", "display_name": "Fanatics", "paid_only": True},
    {"id": "fanduel", "display_name": "FanDuel", "paid_only": False},
    {"id": "lowvig", "display_name": "LowVig.ag", "paid_only": False},
    {"id": "mybookieag", "display_name": "MyBookie.ag", "paid_only": False},
    # US2
    {"id": "ballybet", "display_name": "Bally Bet", "paid_only": False},
    {"id": "betanysports", "display_name": "BetAnySports", "paid_only": False},
    {"id": "betparx", "display_name": "betPARX", "paid_only": False},
    {"id": "courtside", "display_name": "Courtside", "paid_only": True},
    {"id": "espnbet", "display_name": "theScore Bet", "paid_only": False},
    {"id": "fliff", "display_name": "Fliff", "paid_only": False},
    {"id": "hardrockbet", "display_name": "Hard Rock Bet", "paid_only": False},
    {"id": "hardrockbet_az", "display_name": "Hard Rock Bet (AZ)", "paid_only": False},
    {"id": "hardrockbet_fl", "display_name": "Hard Rock Bet (FL)", "paid_only": False},
    {"id": "hardrockbet_oh", "display_name": "Hard Rock Bet (OH)", "paid_only": False},
    {"id": "rebet", "display_name": "ReBet", "paid_only": True},
    # US DFS
    {"id": "dabble_us_dfs", "display_name": "Dabble (DFS)", "paid_only": False},
    {"id": "pick6", "display_name": "DraftKings Pick6", "paid_only": False},
    {"id": "prizepicks", "display_name": "PrizePicks", "paid_only": False},
    {"id": "underdog", "display_name": "Underdog Fantasy", "paid_only": False},
    # US Exchanges
    {"id": "betopenly", "display_name": "BetOpenly", "paid_only": False},
    {"id": "kalshi", "display_name": "Kalshi", "paid_only": False},
    {"id": "novig", "display_name": "Novig", "paid_only": False},
    {"id": "polymarket", "display_name": "Polymarket", "paid_only": False},
    {"id": "prophetx", "display_name": "ProphetX", "paid_only": False},
    # CA (Ontario) - the whole reason this provider is being tested
    {"id": "bet99_ca_on", "display_name": "BET99 (Ontario)", "paid_only": True},
    {"id": "betano_ca_on", "display_name": "Betano (Ontario)", "paid_only": False},
    {"id": "betmgm_ca_on", "display_name": "BetMGM (Ontario)", "paid_only": False},
    {"id": "betrivers_ca_on", "display_name": "BetRivers (Ontario)", "paid_only": False},
    {"id": "playnow_ca", "display_name": "PlayNow (Canada)", "paid_only": False},
    {"id": "pointsbetca", "display_name": "PointsBet (Ontario)", "paid_only": False},
    {"id": "proline_ca_on", "display_name": "PROLINE (Ontario)", "paid_only": False},
    {"id": "sportsinteraction_ca_on", "display_name": "Sports Interaction (Ontario)", "paid_only": False},
    # UK
    {"id": "sport888", "display_name": "888sport", "paid_only": False},
    {"id": "betano_uk", "display_name": "Betano (UK)", "paid_only": False},
    {"id": "betfair_ex_uk", "display_name": "Betfair Exchange (UK)", "paid_only": False},
    {"id": "betfair_sb_uk", "display_name": "Betfair Sportsbook (UK)", "paid_only": False},
    {"id": "betfred_uk", "display_name": "Betfred (UK)", "paid_only": False},
    {"id": "betvictor", "display_name": "Bet Victor", "paid_only": False},
    {"id": "betway", "display_name": "Betway (UK)", "paid_only": False},
    {"id": "boylesports", "display_name": "BoyleSports", "paid_only": False},
    {"id": "coral", "display_name": "Coral", "paid_only": False},
    {"id": "ladbrokes_uk", "display_name": "Ladbrokes (UK)", "paid_only": False},
    {"id": "matchbook", "display_name": "Matchbook", "paid_only": False},
    {"id": "paddypower", "display_name": "Paddy Power", "paid_only": False},
    {"id": "skybet", "display_name": "Sky Bet", "paid_only": False},
    {"id": "smarkets", "display_name": "Smarkets", "paid_only": False},
    {"id": "unibet_uk", "display_name": "Unibet (UK)", "paid_only": False},
    {"id": "williamhill", "display_name": "William Hill (UK)", "paid_only": False},
    # AU
    {"id": "betfair_ex_au", "display_name": "Betfair Exchange (AU)", "paid_only": False},
    {"id": "betr_au", "display_name": "Betr (AU)", "paid_only": False},
    {"id": "bet365_au", "display_name": "Bet365 (AU)", "paid_only": True},
    {"id": "ladbrokes_au", "display_name": "Ladbrokes (AU)", "paid_only": False},
    {"id": "neds", "display_name": "Neds", "paid_only": False},
    {"id": "pointsbetau", "display_name": "PointsBet (AU)", "paid_only": False},
    {"id": "sportsbet", "display_name": "SportsBet (AU)", "paid_only": False},
    {"id": "tab", "display_name": "TAB (AU)", "paid_only": False},
    {"id": "unibet", "display_name": "Unibet (AU)", "paid_only": False},
    # EU
    {"id": "onexbet", "display_name": "1xBet", "paid_only": False},
    {"id": "pinnacle", "display_name": "Pinnacle", "paid_only": False},
]

_CATALOG_BY_ID = {b["id"]: b for b in BOOKMAKER_CATALOG}


def _book_display_name(book_id):
    entry = _CATALOG_BY_ID.get(book_id)
    return entry["display_name"] if entry else book_id


def _normalize_book(book_id):
    return re.sub(r"[^a-z0-9]", "", (book_id or "").lower())


def _book_catalog():
    """Preselected books are ODDS_API_BOOKS first (the confirmed-good
    default set), then every other catalog entry unchecked - same pattern
    as the SharpAPI build's _book_catalog()."""
    plan_books = [b.strip() for b in ODDS_API_BOOKS.split(",") if b.strip()]
    plan_ids = set(plan_books)
    result = [
        {
            "id": b,
            "display_name": _book_display_name(b),
            "tier": "paid" if _CATALOG_BY_ID.get(b, {}).get("paid_only") else None,
            "preselected": True,
        }
        for b in plan_books
    ]
    for entry in BOOKMAKER_CATALOG:
        if entry["id"] in plan_ids:
            continue
        result.append({
            "id": entry["id"],
            "display_name": entry["display_name"],
            "tier": "paid" if entry["paid_only"] else None,
            "preselected": False,
        })
    return result


def fetch_sports_raw():
    """GET /v4/sports - confirmed real, and confirmed free (doesn't count
    against usage quota). Returns the raw list of {key, group, title,
    description, active, has_outrights} dicts."""
    resp = requests.get(
        f"{ODDS_API_BASE_URL}/v4/sports",
        params={"apiKey": ODDS_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_cached_sports_raw():
    global _sports_cache
    if not ODDS_API_KEY:
        return FALLBACK_SPORTS_RAW
    if _sports_cache and time.time() - _sports_cache[0] < SPORTS_CACHE_TTL_SECONDS:
        return _sports_cache[1]
    try:
        raw = fetch_sports_raw()
        _sports_cache = (time.time(), raw)
        return raw
    except Exception:
        return _sports_cache[1] if _sports_cache else FALLBACK_SPORTS_RAW


def get_sport_groups(raw_sports):
    """Our 'sport' filter concept maps to The Odds API's 'group' field
    (e.g. 'American Football') rather than an individual league, since this
    API already combines sport+league into one flat 'key' per league (e.g.
    'americanfootball_nfl') with no separate two-level sport/league
    endpoint the way SharpAPI has. Groups are deduped, preserving first-seen
    order from the API response."""
    seen = []
    seen_ids = set()
    for s in raw_sports:
        if not s.get("active", True):
            continue
        group = s.get("group")
        if not group or group in seen_ids:
            continue
        seen_ids.add(group)
        seen.append(group)
    return [{"id": _slug(g), "name": g} for g in seen]


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def get_leagues_for_group(raw_sports, group_slug):
    """Excludes has_outrights leagues (e.g. 'NFL Super Bowl Winner',
    'NCAAF Championship Winner') entirely - confirmed real bug: those keys
    only support the outrights market (their /odds docs: "the market will
    default to outrights if not specified"), so requesting h2h/spreads/
    totals against one always 422s. This app doesn't do outright/futures
    arb detection anyway (a multi-way futures market has no two-sided
    complementary structure for compute_arbs_from_events to match), so
    there's nothing useful behind that error to fix beyond not asking."""
    return [
        {"id": s["key"], "name": s.get("title", s["key"])}
        for s in raw_sports
        if s.get("active", True) and not s.get("has_outrights") and _slug(s.get("group", "")) == group_slug
    ]


def fetch_odds_for_league(sport_key, bookmakers):
    """GET /v4/sports/{sport}/odds - confirmed real. bookmakers= (a
    comma-separated exact list) takes priority over regions= when both
    would apply, and per the docs "bookmakers can be from any region" -
    that's what lets one call mix a US-wide book (fanduel) with an
    Ontario-specific one (betmgm_ca_on). oddsFormat=american requested so
    "price" is already the display value; decimal is derived from it
    ourselves for the arb math (see _american_to_decimal) rather than
    requesting decimal and converting the other direction, since American
    is this site's display convention (matches the SharpAPI build)."""
    resp = requests.get(
        f"{ODDS_API_BASE_URL}/v4/sports/{sport_key}/odds",
        params={
            "apiKey": ODDS_API_KEY,
            "bookmakers": bookmakers,
            "markets": MARKETS,
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _american_to_decimal(price):
    if not isinstance(price, (int, float)):
        return None
    if price > 0:
        return 1 + price / 100.0
    if price < 0:
        return 1 + 100.0 / abs(price)
    return None


def _format_american_odds(price):
    if isinstance(price, (int, float)):
        price = int(price)
        return f"+{price}" if price > 0 else str(price)
    return str(price)


def _selection_side(outcome_name, home_team, away_team):
    """Maps an outcome's team name to 'home'/'away'/'draw' so complementary
    sides of one market match across books regardless of which team a given
    bookmaker lists first - The Odds API names h2h/spreads outcomes by team
    (or literally 'Draw' for 3-way soccer markets) rather than giving a
    fixed selection_type field the way SharpAPI does."""
    if outcome_name == home_team:
        return "home"
    if outcome_name == away_team:
        return "away"
    if (outcome_name or "").lower() == "draw":
        return "draw"
    return None


def _canonical_point(market_key, outcome, home_team, away_team):
    """Normalizes a spread's point to the home team's perspective, so
    DraftKings listing the away team at +6.5 and FanDuel listing the home
    team at -6.5 for the SAME line are recognized as the same market
    instead of two unrelated ones - same purpose as the SharpAPI build's
    _canonical_line, adapted to this schema's per-outcome 'point' field.
    Totals already share one point across both Over/Under sides, so no
    sign-flip is applied there."""
    point = outcome.get("point")
    if point is None or market_key != "spreads":
        return point
    side = _selection_side(outcome.get("name"), home_team, away_team)
    return -point if side == "away" else point


def _format_selection(market_key, name, point):
    if market_key == "totals" and point is not None:
        return f"{name} {point:g}"
    if market_key == "spreads" and point is not None:
        sign = "+" if point > 0 else ""
        return f"{name} {sign}{point:g}"
    return name


_MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Point Spread", "totals": "Total"}


def compute_arbs_from_events(events, allowed_books, league_title_by_key):
    """Adapts the same 'best price per complementary side, grouped by a
    line-agnostic canonical key' approach the SharpAPI build uses (see that
    project's compute_arbs_from_odds) to this API's per-event/per-bookmaker/
    per-market response shape. Only h2h/spreads/totals are ever present
    here (see MARKETS) - player props are out of scope for arb detection on
    this site regardless of provider."""
    now = datetime.now(timezone.utc)
    arbs = []

    for event in events:
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        commence_time = event.get("commence_time")
        is_live = False
        try:
            ct = datetime.fromisoformat((commence_time or "").replace("Z", "+00:00"))
            is_live = ct < now
        except (ValueError, AttributeError):
            pass

        # (market_key, canonical_point) -> selection_type -> best leg so far
        markets = {}
        for bookmaker in event.get("bookmakers", []):
            book_key = bookmaker.get("key")
            if allowed_books and book_key not in allowed_books:
                continue
            for market in bookmaker.get("markets", []):
                market_key = market.get("key")
                if market_key not in _MARKET_LABELS:
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    price = outcome.get("price")
                    decimal_odds = _american_to_decimal(price)
                    if not decimal_odds or decimal_odds <= 1:
                        continue

                    if market_key == "totals":
                        selection_type = (name or "").lower()  # "over" / "under"
                        canonical_point = outcome.get("point")
                    else:
                        selection_type = _selection_side(name, home_team, away_team)
                        canonical_point = _canonical_point(market_key, outcome, home_team, away_team)
                    if selection_type not in ("home", "away", "draw", "over", "under"):
                        continue

                    group_key = (market_key, canonical_point)
                    by_selection = markets.setdefault(group_key, {})
                    existing = by_selection.get(selection_type)
                    if not existing or decimal_odds > existing["decimal_odds"]:
                        by_selection[selection_type] = {
                            "bookmaker_key": book_key,
                            "bookmaker_title": bookmaker.get("title") or _book_display_name(book_key),
                            "name": name,
                            "point": outcome.get("point"),
                            "price": price,
                            "decimal_odds": decimal_odds,
                        }

        for (market_key, canonical_point), by_selection in markets.items():
            legs = list(by_selection.values())
            if len(legs) < 2:
                continue
            leg_books = [leg["bookmaker_key"] for leg in legs]
            if len(set(leg_books)) != len(legs):
                continue  # same book on both sides isn't a real hedge

            implied_sum = sum(1.0 / leg["decimal_odds"] for leg in legs)
            if implied_sum >= 1.0:
                continue
            profit_percent = (1.0 / implied_sum - 1.0) * 100
            if profit_percent > MAX_SANE_PROFIT_PERCENT:
                continue

            market_label = _MARKET_LABELS[market_key]
            if canonical_point is not None:
                market_label += f" ({canonical_point:g})"

            arb_legs = []
            for leg in legs:
                stake_percent = (1.0 / leg["decimal_odds"]) / implied_sum * 100
                arb_legs.append({
                    "sportsbook": leg["bookmaker_title"],
                    "selection": _format_selection(market_key, leg["name"], leg["point"]),
                    "odds_american": _format_american_odds(leg["price"]),
                    "stake_percent": round(stake_percent, 2),
                    # includeLinks is documented as adding bookmaker links
                    # "if available", but the exact response field name
                    # isn't confirmed from the docs pasted for this build -
                    # left null rather than guessing (the frontend already
                    # hides the Bet button entirely when this is null).
                    "deep_link": None,
                })

            arbs.append({
                "event_name": f"{away_team} @ {home_team}" if away_team and home_team else event.get("id"),
                "league": (league_title_by_key.get(event.get("sport_key")) or event.get("sport_key") or "").upper(),
                "market": market_label,
                "profit_percent": round(profit_percent, 2),
                "event_start_time": commence_time,
                "is_live": is_live,
                "legs": arb_legs,
            })

    arbs.sort(key=lambda a: a["profit_percent"], reverse=True)
    return arbs


MOCK_ARBS = [
    {
        "event_name": "Dallas Cowboys @ Philadelphia Eagles",
        "league": "NFL",
        "market": "Moneyline",
        "profit_percent": 3.1,
        "event_start_time": None,
        "is_live": False,
        "legs": [
            {"sportsbook": "FanDuel", "selection": "Philadelphia Eagles", "odds_american": "-110",
             "stake_percent": 52.4, "deep_link": None},
            {"sportsbook": "BetMGM (Ontario)", "selection": "Dallas Cowboys", "odds_american": "+130",
             "stake_percent": 47.6, "deep_link": None},
        ],
    },
]


@app.route("/api/sports")
def api_sports():
    raw = get_cached_sports_raw()
    groups = get_sport_groups(raw)
    available = {g["name"] for g in groups}
    preferred_available = [g for g in PREFERRED_GROUPS if g in available]
    preferred_ids = [_slug(g) for g in (preferred_available or PREFERRED_GROUPS[:1])]
    return jsonify({
        "source": "live" if ODDS_API_KEY else "mock",
        "sports": groups,
        "preferred": preferred_ids,
    })


@app.route("/api/leagues")
def api_leagues():
    group_slug = request.args.get("sport", "")
    if not group_slug:
        return jsonify({"source": "error", "error": "missing sport param", "leagues": []}), 200
    raw = get_cached_sports_raw()
    leagues = get_leagues_for_group(raw, group_slug)
    return jsonify({"source": "live" if ODDS_API_KEY else "mock", "leagues": leagues})


@app.route("/api/books")
def api_books():
    return jsonify({"source": "live" if ODDS_API_KEY else "mock", "books": _book_catalog()})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/arbs")
def api_arbs():
    if not ODDS_API_KEY:
        return jsonify({"source": "mock", "arbs": MOCK_ARBS})

    selected_books = request.args.get("books")
    sport_param = request.args.get("sport", DEFAULT_GROUP and _slug(DEFAULT_GROUP))
    league_param = request.args.get("league", DEFAULT_LEAGUE_KEY)

    scan_all_sports = sport_param == "all"
    group_slugs = [] if scan_all_sports else [s.strip() for s in sport_param.split(",") if s.strip()]
    multi_sport = len(group_slugs) > 1
    scan_all_leagues = league_param == "all" or multi_sport

    cache_key = (selected_books or ODDS_API_BOOKS, sport_param, league_param)
    cached = _arbs_cache.get(cache_key)
    if cached and time.time() - cached[0] < ARBS_CACHE_TTL_SECONDS:
        return jsonify(cached[1])

    resolved_books = [b.strip() for b in (selected_books or ODDS_API_BOOKS).split(",") if b.strip()]
    bookmakers_param = ",".join(resolved_books)
    allowed_books = set(resolved_books)

    raw_sports = get_cached_sports_raw()
    league_title_by_key = {s["key"]: s.get("title", s["key"]) for s in raw_sports}

    if scan_all_sports:
        league_keys = [s["key"] for s in raw_sports if s.get("active", True) and not s.get("has_outrights")]
    elif scan_all_leagues:
        league_keys = []
        for gs in group_slugs:
            league_keys.extend(l["id"] for l in get_leagues_for_group(raw_sports, gs))
    else:
        league_keys = [league_param] if league_param else [DEFAULT_LEAGUE_KEY]

    league_keys = league_keys[:MAX_LEAGUES_PER_SCAN] or [DEFAULT_LEAGUE_KEY]

    all_events = []
    league_issues = {}
    for key in league_keys:
        try:
            all_events.extend(fetch_odds_for_league(key, bookmakers_param))
        except Exception as e:
            league_issues[key] = str(e)

    if not all_events and league_issues:
        return jsonify({
            "source": "error",
            "error": "; ".join(f"{k}: {v}" for k, v in league_issues.items()),
            "arbs": MOCK_ARBS,
        }), 200

    arbs = compute_arbs_from_events(all_events, allowed_books, league_title_by_key)

    payload = {
        "source": "live",
        "mode": "odds_scan",
        "rows_scanned": len(all_events),
        "book_issues": {},
        "league_issues": league_issues,
        "arbs": arbs,
    }
    _arbs_cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
