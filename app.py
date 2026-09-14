import os
import re
import time

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SHARPAPI_KEY = os.environ.get("SHARPAPI_KEY", "")
SHARPAPI_BASE_URL = os.environ.get("SHARPAPI_BASE_URL", "https://api.sharpapi.io")

# Comma-separated list of the 5 books you picked in your SharpAPI dashboard.
# Update this once you know your final 5 (or set it as an env var instead).
SHARPAPI_BOOKS = os.environ.get(
    "SHARPAPI_BOOKS",
    "draftkings,fanduel,betmgm,caesars,fanatics",
)

# Real cross-book arbs are almost always single digits. Anything above this,
# on either the paid or free-tier scan, is far more likely to be stale or
# incomplete data than free money, so it's dropped rather than shown.
MAX_SANE_PROFIT_PERCENT = 25.0

# Short in-memory cache so rapid book-toggle clicks or multiple open tabs
# don't burn through the API's per-minute request budget.
ARBS_CACHE_TTL_SECONDS = 8
_arbs_cache = {}  # cache key -> (timestamp, response_dict)

# Mock data used only when no API key is set, so the site is viewable
# immediately without any setup. Once SHARPAPI_KEY is set, real data is used.
MOCK_ARBS = [
    {
        "event_name": "Lakers vs Celtics",
        "league": "NBA",
        "profit_percent": 3.4,
        "legs": [
            {"sportsbook": "DraftKings", "selection": "Lakers ML", "odds_american": "+150", "stake_percent": 42.0},
            {"sportsbook": "FanDuel", "selection": "Celtics ML", "odds_american": "-120", "stake_percent": 58.0},
        ],
    },
    {
        "event_name": "Chiefs vs Bills",
        "league": "NFL",
        "profit_percent": 1.8,
        "legs": [
            {"sportsbook": "BetMGM", "selection": "Chiefs -2.5", "odds_american": "+105", "stake_percent": 49.0},
            {"sportsbook": "Caesars", "selection": "Bills +2.5", "odds_american": "-105", "stake_percent": 51.0},
        ],
    },
]


def _normalize_book(name):
    """Collapses a book id/display name to a bare-lowercase key
    ('DraftKings' / 'draft-kings' / 'draftkings' all -> 'draftkings') so we
    can compare the toggle selection against whatever casing SharpAPI's
    leg.sportsbook field happens to use."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _format_american_odds(value):
    if isinstance(value, (int, float)) and value > 0:
        return f"+{value}"
    return str(value)


class TierRestrictedError(Exception):
    pass


def fetch_arbs_paid(min_profit=0.5, books=None):
    """Calls SharpAPI's real, pre-computed arbitrage endpoint (Hobby+ only).
    Response shape: {"data": [{"event_name", "profit_percent",
    "legs": [{"sportsbook","selection","odds_american","stake_percent"}, ...],
    "possibly_stale", "oldest_odds_age_seconds", "warnings": [...]}], ...}

    We don't fully trust the server-side `sportsbook` filter param or its
    own staleness/profit filtering to be applied exactly the way we expect,
    so everything here is re-checked client-side as well."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    params = {"min_profit": min_profit, "sportsbook": books or SHARPAPI_BOOKS}
    resp = requests.get(
        f"{SHARPAPI_BASE_URL}/api/v1/opportunities/arbitrage",
        headers=headers,
        params=params,
        timeout=10,
    )
    if resp.status_code == 403:
        raise TierRestrictedError(resp.text)
    resp.raise_for_status()
    data = resp.json().get("data", [])

    allowed_books = {_normalize_book(b) for b in (books or SHARPAPI_BOOKS).split(",")}

    arbs = []
    for arb in data:
        # Skip anything flagged as possibly stale or a known-suspicious pattern —
        # these look attractive but usually aren't real, actionable arbs.
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
        # Every leg must be a book the user actually has toggled on — if the
        # API's own filter param didn't honor that, an arb with an unselected
        # book's leg is unbeatable to the user and must not be shown.
        if not all(_normalize_book(leg.get("sportsbook")) in allowed_books for leg in raw_legs):
            continue

        arbs.append({
            "event_name": arb.get("event_name", ""),
            "league": arb.get("league", ""),
            "profit_percent": profit_percent,
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


DEFAULT_BOOKS = [
    {"id": "draftkings", "display_name": "DraftKings"},
    {"id": "fanduel", "display_name": "FanDuel"},
    {"id": "betmgm", "display_name": "BetMGM"},
    {"id": "caesars", "display_name": "Caesars"},
    {"id": "fanatics", "display_name": "Fanatics"},
]


@app.route("/api/books")
def api_books():
    """Lists the sportsbooks available to this API key, so the frontend
    can render a toggle for each one. Falls back to a placeholder list
    if there's no key yet (mock mode) or the call fails for any reason."""
    if not SHARPAPI_KEY:
        return jsonify({"source": "mock", "books": DEFAULT_BOOKS})

    try:
        headers = {"X-API-Key": SHARPAPI_KEY}
        resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/sportsbooks", headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        books = [{"id": b.get("id"), "display_name": b.get("display_name", b.get("id"))} for b in data if b.get("id")]
        return jsonify({"source": "live", "books": books or DEFAULT_BOOKS})
    except Exception as e:
        return jsonify({"source": "error", "error": str(e), "books": DEFAULT_BOOKS}), 200


@app.route("/")
def index():
    return render_template("index.html")


def fetch_events(limit=10):
    """Gets a list of events to scan. We only pull a handful to stay
    within the API's per-minute request budget."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(
        f"{SHARPAPI_BASE_URL}/api/v1/events",
        headers=headers,
        params={"limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_event_odds(event_id):
    """Gets the COMPLETE odds set for one event — every market, every
    selection, every book. This is what makes arb math trustworthy: partial
    data (e.g. a paginated dump across many events) can make a market look
    profitable just because some outcomes are missing from what we pulled."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(
        f"{SHARPAPI_BASE_URL}/api/v1/events/{event_id}/odds",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_all_odds(event_limit=8):
    """Pulls complete odds for a handful of events (not a broad partial
    slice) so every market we analyze has its full outcome set."""
    events = fetch_events(limit=event_limit)
    all_rows = []
    for ev in events:
        event_id = ev.get("event_id") or ev.get("id")
        if not event_id:
            continue
        try:
            all_rows.extend(fetch_event_odds(event_id))
        except requests.HTTPError:
            continue  # skip events that error out, keep scanning the rest
    return all_rows


def compute_arbs_from_odds(rows, min_profit=0.0, books=None):
    """Groups odds by market_id (a specific market+line), takes the best
    price per selection across whichever books cover it, and flags markets
    where the combined implied probability is under 100% (an arbitrage).

    Only markets with exactly 2 or 3 outcomes are considered (moneylines,
    spreads, totals) — high-outcome markets like 'correct score' are far
    more likely to look falsely profitable if even one selection is missing
    from what we pulled. Rows missing the fields this needs are skipped
    rather than allowed to crash the whole scan.
    """
    allowed_books = {_normalize_book(b) for b in books.split(",")} if books else None

    markets = {}
    for row in rows:
        if not row.get("is_active", True):
            continue
        if not isinstance(row.get("odds_decimal"), (int, float)) or row["odds_decimal"] <= 1:
            continue  # unusable/missing price, can't factor into implied probability
        if allowed_books and _normalize_book(row.get("sportsbook")) not in allowed_books:
            continue
        market_id = row.get("market_id")
        if not market_id:
            continue
        markets.setdefault(market_id, []).append(row)

    arbs = []
    for market_id, entries in markets.items():
        unique_selection_ids = {e.get("selection_id") or e.get("selection") for e in entries}
        if len(unique_selection_ids) not in (2, 3):
            continue  # skip high-outcome markets (correct score, props, etc.)

        best_by_selection = {}
        for e in entries:
            sel_id = e.get("selection_id") or e.get("selection")
            if sel_id not in best_by_selection or e["odds_decimal"] > best_by_selection[sel_id]["odds_decimal"]:
                best_by_selection[sel_id] = e

        legs = list(best_by_selection.values())
        if len(legs) < 2 or len(legs) != len(unique_selection_ids):
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
        league = (sample.get("league_ref") or {}).get("label", sample.get("league", ""))
        market_label = (sample.get("market_ref") or {}).get("label", sample.get("market_type", ""))

        arb_legs = []
        for leg in legs:
            stake_percent = (1.0 / leg["odds_decimal"]) / implied_sum * 100
            arb_legs.append({
                "sportsbook": (leg.get("sportsbook_ref") or {}).get("label", leg.get("sportsbook", "")),
                "selection": leg.get("selection", ""),
                "odds_american": _format_american_odds(leg.get("odds_american")),
                "stake_percent": round(stake_percent, 2),
            })

        arbs.append({
            "event_name": f"{away} @ {home}" if away and home else market_id,
            "league": f"{league} · {market_label}" if league or market_label else "",
            "profit_percent": round(profit_percent, 2),
            "legs": arb_legs,
        })

    arbs.sort(key=lambda a: a["profit_percent"], reverse=True)
    return arbs


@app.route("/api/test-odds")
def api_test_odds():
    """Temporary route to confirm the API key works at all, using the
    free-tier /odds endpoint (arbitrage requires Hobby+)."""
    headers = {"X-API-Key": SHARPAPI_KEY}
    resp = requests.get(f"{SHARPAPI_BASE_URL}/api/v1/odds", headers=headers, timeout=10)
    return jsonify({"status_code": resp.status_code, "body": resp.text[:500]})


@app.route("/api/arbs")
def api_arbs():
    if not SHARPAPI_KEY:
        return jsonify({"source": "mock", "arbs": MOCK_ARBS})

    selected_books = request.args.get("books")  # comma-separated, from the toggles

    cache_key = selected_books or SHARPAPI_BOOKS
    cached = _arbs_cache.get(cache_key)
    if cached and time.time() - cached[0] < ARBS_CACHE_TTL_SECONDS:
        return jsonify(cached[1])

    try:
        arbs = fetch_arbs_paid(books=selected_books)
        payload = {"source": "live", "mode": "paid_endpoint", "arbs": arbs}
        _arbs_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)
    except TierRestrictedError:
        pass  # not on Hobby+ yet — fall back to the free-tier custom scan below
    except Exception as e:
        return jsonify({"source": "error", "error": str(e), "arbs": MOCK_ARBS}), 200

    try:
        rows = fetch_all_odds()
        arbs = compute_arbs_from_odds(rows, min_profit=0.0, books=selected_books)
        payload = {"source": "live", "mode": "free_tier_custom_scan", "arbs": arbs, "rows_scanned": len(rows)}
        _arbs_cache[cache_key] = (time.time(), payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"source": "error", "error": str(e), "arbs": MOCK_ARBS}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
