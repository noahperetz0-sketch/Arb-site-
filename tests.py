"""Regression tests for the arb-matching logic in app.py (The Odds API build).

Mirrors the same matching philosophy proven in this project's SharpAPI
build - every case here is either a real bug caught during that build (and
re-tested here since the matching logic was rewritten from scratch for a
different schema) or one specific to this provider's response shape.

    python tests.py
"""

from app import (
    compute_arbs_from_events,
    _selection_side,
    _canonical_point,
    _american_to_decimal,
    _format_selection,
    MAX_SANE_PROFIT_PERCENT,
)


def _event(**overrides):
    base = {
        "id": "evt1",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-20T18:00:00Z",  # future - not live
        "home_team": "Team A",
        "away_team": "Team B",
        "bookmakers": [],
    }
    base.update(overrides)
    return base


def _bookmaker(key, title, markets):
    return {"key": key, "title": title, "markets": markets}


def test_same_book_is_rejected():
    """A market where only one book has data would compare that book's own
    two prices against each other and could show a fake arb."""
    event = _event(bookmakers=[
        _bookmaker("betmgm_ca_on", "BetMGM (Ontario)", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": 150},
                {"name": "Team B", "price": -170},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"betmgm_ca_on", "fanduel"}, {})
    assert len(arbs) == 0, f"expected 0 arbs (same book both sides), got {len(arbs)}"


def test_real_moneyline_arb_is_found():
    """Two different books, complementary h2h sides, genuine arb. Odds
    chosen to keep profit_percent realistic (~6%, comfortably under
    MAX_SANE_PROFIT_PERCENT) - a genuine arb of this kind rather than an
    implausibly large one, which the sanity cap would (correctly) reject."""
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": 120},
                {"name": "Team B", "price": -125},
            ]},
        ]),
        _bookmaker("betmgm_ca_on", "BetMGM (Ontario)", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": -110},
                {"name": "Team B", "price": 105},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "betmgm_ca_on"}, {})
    assert len(arbs) == 1, f"expected 1 moneyline arb, got {len(arbs)}"
    assert arbs[0]["market"] == "Moneyline"
    assert len(arbs[0]["legs"]) == 2


def test_disallowed_book_is_excluded():
    """A bookmaker present in the API response but not in the user's
    selected/toggled books must never contribute a leg."""
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": 130},
                {"name": "Team B", "price": -140},
            ]},
        ]),
        _bookmaker("draftkings", "DraftKings", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": -110},
                {"name": "Team B", "price": 120},
            ]},
        ]),
    ])
    # Only fanduel selected - draftkings' better complementary price must
    # not be used even though it would otherwise form a real arb.
    arbs = compute_arbs_from_events([event], {"fanduel"}, {})
    assert len(arbs) == 0, f"expected 0 arbs (only one allowed book had data), got {len(arbs)}"


def test_spread_true_complement_is_found():
    """Home team -6.5 on one book, away team +6.5 on another - genuine
    complements of the same line, must be found. Odds chosen so FanDuel
    wins the home side and DraftKings wins the away side (a real two-book
    hedge), with a modest, realistic profit_percent."""
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "spreads", "outcomes": [
                {"name": "Team A", "price": -105, "point": -6.5},
                {"name": "Team B", "price": -120, "point": 6.5},
            ]},
        ]),
        _bookmaker("draftkings", "DraftKings", [
            {"key": "spreads", "outcomes": [
                {"name": "Team A", "price": -130, "point": -6.5},
                {"name": "Team B", "price": 110, "point": 6.5},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "draftkings"}, {})
    assert len(arbs) == 1, f"expected 1 true-complement spread arb, got {len(arbs)}"


def test_spread_conflicting_favorite_is_rejected():
    """Bug pattern from the SharpAPI build, re-verified here: one book has
    the home team favored -6.5, another independently has the AWAY team
    favored -6.5 (i.e. point=-6.5 for the away outcome). Same magnitude,
    but both are 'my team wins outright' bets, not true complements."""
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "spreads", "outcomes": [
                {"name": "Team A", "price": -110, "point": -6.5},
                {"name": "Team B", "price": -110, "point": 6.5},
            ]},
        ]),
        _bookmaker("draftkings", "DraftKings", [
            {"key": "spreads", "outcomes": [
                {"name": "Team A", "price": 150, "point": 6.5},
                {"name": "Team B", "price": -170, "point": -6.5},  # away team ALSO favored -6.5
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "draftkings"}, {})
    assert len(arbs) == 0, f"expected 0 arbs (books disagree on who's favored), got {len(arbs)}"


def test_totals_over_under_arb_is_found():
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -105, "point": 48.5},
                {"name": "Under", "price": -115, "point": 48.5},
            ]},
        ]),
        _bookmaker("espnbet", "theScore Bet", [
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 48.5},
                {"name": "Under", "price": 130, "point": 48.5},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "espnbet"}, {})
    assert len(arbs) == 1, f"expected 1 totals arb, got {len(arbs)}"
    assert arbs[0]["market"] == "Total (48.5)"


def test_mismatched_totals_points_are_not_matched():
    """Two books quoting DIFFERENT total lines (48.5 vs 47.5) are not the
    same market and must never be paired, however profitable it looks."""
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -105, "point": 48.5},
                {"name": "Under", "price": -115, "point": 48.5},
            ]},
        ]),
        _bookmaker("espnbet", "theScore Bet", [
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": 200, "point": 47.5},
                {"name": "Under", "price": -110, "point": 47.5},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "espnbet"}, {})
    assert len(arbs) == 0, f"expected 0 arbs (different total lines), got {len(arbs)}"


def test_unrealistic_profit_percent_is_excluded():
    """Same sanity-cap philosophy as the SharpAPI build - an implausibly
    large implied profit is far more likely a data/matching problem than
    free money."""
    event = _event(bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": 260},
                {"name": "Team B", "price": -159},
            ]},
        ]),
        _bookmaker("betmgm_ca_on", "BetMGM (Ontario)", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": -400},
                {"name": "Team B", "price": 700},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "betmgm_ca_on"}, {})
    assert len(arbs) == 0, (
        f"expected the outsized arb to be excluded by MAX_SANE_PROFIT_PERCENT "
        f"({MAX_SANE_PROFIT_PERCENT}), got {len(arbs)}"
    )


def test_live_event_is_flagged():
    event = _event(commence_time="2020-01-01T00:00:00Z", bookmakers=[
        _bookmaker("fanduel", "FanDuel", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": 120},
                {"name": "Team B", "price": -125},
            ]},
        ]),
        _bookmaker("betmgm_ca_on", "BetMGM (Ontario)", [
            {"key": "h2h", "outcomes": [
                {"name": "Team A", "price": -110},
                {"name": "Team B", "price": 105},
            ]},
        ]),
    ])
    arbs = compute_arbs_from_events([event], {"fanduel", "betmgm_ca_on"}, {})
    assert len(arbs) == 1
    assert arbs[0]["is_live"] is True, "expected a past commence_time to be flagged as live"


def test_selection_side_helper():
    assert _selection_side("Team A", "Team A", "Team B") == "home"
    assert _selection_side("Team B", "Team A", "Team B") == "away"
    assert _selection_side("Draw", "Team A", "Team B") == "draw"
    assert _selection_side("Someone Else", "Team A", "Team B") is None


def test_canonical_point_flips_away_side():
    outcome_home = {"name": "Team A", "point": -6.5}
    outcome_away = {"name": "Team B", "point": 6.5}
    assert _canonical_point("spreads", outcome_home, "Team A", "Team B") == -6.5
    assert _canonical_point("spreads", outcome_away, "Team A", "Team B") == -6.5
    # totals: point is never flipped
    assert _canonical_point("totals", {"name": "Over", "point": 48.5}, "Team A", "Team B") == 48.5


def test_american_to_decimal_helper():
    assert round(_american_to_decimal(150), 4) == 2.5
    assert round(_american_to_decimal(-150), 4) == round(1 + 100 / 150, 4)
    assert _american_to_decimal(None) is None
    assert _american_to_decimal(0) is None


def test_format_selection_helper():
    assert _format_selection("h2h", "Team A", None) == "Team A"
    assert _format_selection("spreads", "Team A", -6.5) == "Team A -6.5"
    assert _format_selection("spreads", "Team B", 6.5) == "Team B +6.5"
    assert _format_selection("totals", "Over", 48.5) == "Over 48.5"


ALL_TESTS = [
    test_same_book_is_rejected,
    test_real_moneyline_arb_is_found,
    test_disallowed_book_is_excluded,
    test_spread_true_complement_is_found,
    test_spread_conflicting_favorite_is_rejected,
    test_totals_over_under_arb_is_found,
    test_mismatched_totals_points_are_not_matched,
    test_unrealistic_profit_percent_is_excluded,
    test_live_event_is_flagged,
    test_selection_side_helper,
    test_canonical_point_flips_away_side,
    test_american_to_decimal_helper,
    test_format_selection_helper,
]


if __name__ == "__main__":
    failures = []
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures.append(test.__name__)
            print(f"FAIL  {test.__name__}: {e}")

    print()
    if failures:
        print(f"{len(failures)} of {len(ALL_TESTS)} tests FAILED: {', '.join(failures)}")
        raise SystemExit(1)
    else:
        print(f"All {len(ALL_TESTS)} tests passed.")
