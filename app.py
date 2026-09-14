import os
import re
import time

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

# Real cross-book arbs are almost always single digits. Anything above this
# is far more likely to be stale or mismatched data than free money, so
# it's dropped rather than shown.
MAX_SANE_PROFIT_PERCENT = 25.0

# Rows returned per sportsbook per scan. We only read the first page per
# book rather than following pagination — chasing every page across 5
# books would burn through the plan's request budget fast, at the cost of
# only seeing whichever events/markets SharpAPI returns first. Revisit this
# once you know your plan's actual rate limit.
ODDS_PAGE_LIMIT = 200

# Short in-memory cache so rapid book-toggle clicks or multiple open tabs
# don't burn through the API's request budget.
ARBS_CACHE_TTL_SECONDS = 8
_arbs_cache = {}  # cache key -> (timestamp, response_dict)

# Sports/leagues change rarely, so this cache lives much longer.
SPORTS_CACHE_TTL_SECONDS = 3600
_sports_cache = None  # (timestamp, [{"id","name"}])
_leagues_cache = {}  # sport -> (timestamp, [{"id","name"}])

# Only used if SharpAPI's /sports or /leagues endpoints don't exist or fail -
# a minimal, honest fallback built only from sport/league ids we've directly
# confirmed against real API responses, not guessed.
FALLBACK_SPORTS = [
    {"id": "football", "name": "Football"},
    {"id": "basketball", "name": "Basketball"},
    {"id": "soccer", "name": "Soccer"},
    {"id": "tennis", "name": "Tennis"},
    {"id": "esports", "name": "Esports"},
]
FALLBACK_LEAGUES = {
    "football": [{"id": "nfl", "name": "NFL"}],
}

BOOK_DISPLAY_NAMES = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "betrivers": "BetRivers",
    "betano": "Betano",
}


def _book_display_name(book_id):
    return BOOK_DISPLAY_NAMES.get(book_id, book_id.title())


BOOKS = [
    {"id": b.strip(), "display_name": _book_display_name(b.strip())}
    for b in SHARPAPI_BOOKS.split(",")
    if b.strip()
]

# Mock data used only when no API key is set, so the site is viewable
# immediately without any setup. Once SHARPAPI_KEY is set, real data is used.
MOCK_ARBS = [
    {
        "event_name": "Lakers vs Celtics",
        "league": "NBA",
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
        "profit_percent": 1.8,
        "event_start_time": "2026-09-15T00:15:00Z",
        "is_live": True,
        "legs": [
            {"sportsbook": "BetMGM", "selection": "Chiefs -2.5", "odds_american": "+105", "stake_percent": 49.0},
            {"sportsbook": "BetRivers", "selection": "Broncos +2.5", "odds_american": "-105", "stake_percent": 51.0},
        ],
    },
]


def _normalize_book(name):
    """Collapses a book id/display name to a bare-lowercase key
    ('DraftKings' / 'draft-kings' / 'draftkings' all -> 'draftkings') so we
    can compare the toggle selection against whatever casing a leg's
    sportsbook field happens to use."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _format_american_odds(value):
    if isinstance(value, (int, float)) and value > 0:
        return f"+{value}"
    return str(value)


def fetch_arbs_paid(min_profit=0.5, books=None):
    """Attempts SharpAPI's pre-computed arbitrage endpoint, IF your plan
    actually has it. As of testing, no "Opportunities"/"Arbitrage" tab shows
    up anywhere in SharpAPI's own playground (only Odds/Events/Game State),
    so this may simply not exist as a real, callable endpoint — treat it as
    a bonus attempt. api_arbs() below falls back to compute_arbs_from_odds()
    (built from the confirmed-real /odds endpoint) if this fails for any
    reason at all, not just a 403."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    params = {"min_profit": min_profit, "sportsbook": books or SHARPAPI_BOOKS}
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
        if any("SUSPICIOUS" in w or "STALE" in w for w in arb.get("warnings", [])):
            continue

        profit_percent = arb.get("profit_percent", 0)
        if profit_percent < min_profit or profit_percent > MAX_SANE_PROFIT_PERCENT:
            continue

        raw_legs = arb.get("legs", [])
        if len(raw_legs) < 2:
            continue
        if not all(_normalize_book(leg.get("sportsbook")) in allowed_books for leg in raw_legs):
            continue

        arbs.append({
            "event_name": arb.get("event_name", ""),
            "league": arb.get("league", ""),
            "profit_percent": profit_percent,
            "event_start_time": arb.get("event_start_time"),
            "is_live": bool(arb.get("is_live", False)),
            "legs": [
                {
                    "sportsbook": leg.get("sportsbook", ""),
                    "selection": leg.get("selection", ""),
                    "odds_american": _format_american_odds(leg.get("odds_american")),
                    "stake_percent": leg.get("stake_percent", 0),
                }
                for leg in raw_legs
            ],
        })
    return arbs


def fetch_odds_for_book(sportsbook, sport=None, league=None, limit=ODDS_PAGE_LIMIT):
    """Calls SharpAPI's confirmed-real /odds endpoint for one sportsbook.
    Response shape (confirmed against a live response): {"data": [{...row}],
    "pagination": {...}} where each row has event_id, sportsbook,
    market_type, selection, selection_type, line, odds_decimal,
    odds_american, is_active, is_player_prop, home_team, away_team,
    league, etc."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    params = {
        "sport": sport or DEFAULT_SPORT,
        "league": league or DEFAULT_LEAGUE,
        "sportsbook": sportsbook,
        "limit": limit,
    }
    resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/odds", headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_all_odds(books, sport=None, league=None):
    """Pulls one page of odds per sportsbook and combines them. A book that
    errors out (bad id, temporary outage) is skipped rather than failing
    the whole scan."""
    all_rows = []
    for book in books:
        try:
            all_rows.extend(fetch_odds_for_book(book, sport=sport, league=league))
        except requests.RequestException:
            continue
    return all_rows


def compute_arbs_from_odds(rows, min_profit=0.0, books=None):
    """Groups odds rows into markets and flags any where the best price per
    outcome, taken across whichever books cover it, has a combined implied
    probability under 100% (an arbitrage).

    Grouping key is (event_id, market_type, |line|) — NOT market_id, which
    is sportsbook-specific (DraftKings and FanDuel each mint their own
    market_id for the identical real-world bet, so grouping by it would
    never find a cross-book match). event_id/market_type/line are the
    fields that stay consistent across books for the same bet. The line is
    compared by absolute value because spread markets store it with
    opposite signs per side (home -0.5 / away +0.5 for the same market).

    Player-prop markets are skipped entirely: market_type alone doesn't
    say WHICH player a row is about (two different players' passing-yards
    props share the same market_type), and matching that reliably would
    mean parsing player names out of free-text selection strings — too
    fragile to trust with real money.
    """
    allowed_books = {_normalize_book(b) for b in (books or SHARPAPI_BOOKS).split(",")}

    markets = {}
    for row in rows:
        if not row.get("is_active", True):
            continue
        if row.get("is_player_prop"):
            continue
        if not isinstance(row.get("odds_decimal"), (int, float)) or row["odds_decimal"] <= 1:
            continue
        if allowed_books and _normalize_book(row.get("sportsbook")) not in allowed_books:
            continue

        event_id = row.get("event_id")
        market_type = row.get("market_type")
        if not event_id or not market_type:
            continue

        line = row.get("line")
        line_key = abs(line) if isinstance(line, (int, float)) else line

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
            arb_legs.append({
                "sportsbook": _book_display_name(_normalize_book(leg.get("sportsbook", ""))),
                "selection": leg.get("selection", ""),
                "odds_american": _format_american_odds(leg.get("odds_american")),
                "stake_percent": round(stake_percent, 2),
            })

        arbs.append({
            "event_name": f"{away} @ {home}" if away and home else event_id,
            "league": f"{league.upper()} · {market_label}" if league else market_label,
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
    """Calls SharpAPI's sports-list endpoint. Not yet confirmed against a
    real response (unlike /odds) - inferred from the official Python SDK's
    documented client.sports.list() method plus the {"data": [...]} wrapper
    and id/name field convention every other confirmed endpoint uses
    (matches the sport_ref shape seen embedded in real /odds rows, e.g.
    {"id": "football", "name": "Football", "numerical_id": 12})."""
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
    """Calls SharpAPI's leagues-list endpoint for one sport. Same
    confirmation caveat as fetch_sports() - inferred from the SDK's
    client.leagues.list(sport) plus the league_ref shape seen in real /odds
    rows, e.g. {"id": "nfl", "label": "NFL", "numerical_id": 376}."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(
        f"{SHARPAPI_BASE_URL}/api/v1/leagues", headers=headers, params={"sport": sport}, timeout=10
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return [
        {"id": l.get("id"), "name": l.get("label") or l.get("name") or l.get("id")}
        for l in data
        if l.get("id")
    ]


@app.route("/api/sports")
def api_sports():
    global _sports_cache
    if not SHARPAPI_KEY:
        return jsonify({"source": "mock", "sports": FALLBACK_SPORTS})

    if _sports_cache and time.time() - _sports_cache[0] < SPORTS_CACHE_TTL_SECONDS:
        return jsonify({"source": "live", "sports": _sports_cache[1]})

    try:
        sports = fetch_sports()
        _sports_cache = (time.time(), sports)
        return jsonify({"source": "live", "sports": sports})
    except Exception as e:
        return jsonify({"source": "error", "error": str(e), "sports": FALLBACK_SPORTS}), 200


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
    return jsonify({"source": "live" if SHARPAPI_KEY else "mock", "books": BOOKS})


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
    sport = request.args.get("sport", DEFAULT_SPORT)
    league = request.args.get("league", DEFAULT_LEAGUE)

    cache_key = (selected_books or SHARPAPI_BOOKS, sport, league)
    cached = _arbs_cache.get(cache_key)
    if cached and time.time() - cached[0] < ARBS_CACHE_TTL_SECONDS:
        return jsonify(cached[1])

    arbs = None
    mode = None
    rows_scanned = None
    try:
        arbs = fetch_arbs_paid(books=selected_books)
        mode = "paid_endpoint"
    except Exception:
        pass  # endpoint may not exist on this plan/product at all — fall back below

    if arbs is None:
        try:
            resolved_books = selected_books or SHARPAPI_BOOKS
            rows = fetch_all_odds(resolved_books.split(","), sport=sport, league=league)
            rows_scanned = len(rows)
            arbs = compute_arbs_from_odds(rows, min_profit=0.0, books=resolved_books)
            mode = "odds_scan"
        except Exception as e:
            return jsonify({"source": "error", "error": str(e), "arbs": MOCK_ARBS}), 200

    # rows_scanned=0 on an odds_scan means SharpAPI returned nothing at all
    # for this sport/league/book combination (worth investigating) - as
    # opposed to rows_scanned>0 with zero arbs, which just means real prices
    # were found but none of them crossed into arbitrage territory (normal).
    payload = {"source": "live", "mode": mode, "rows_scanned": rows_scanned, "arbs": arbs}
    _arbs_cache[cache_key] = (time.time(), payload)
    return jsonify(payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
