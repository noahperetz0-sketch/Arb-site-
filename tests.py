"""Regression tests for the arb-matching logic in app.py.

Every case here is a real bug found and fixed during development - not
hypothetical. Run this before pushing any change to compute_arbs_from_odds,
_canonical_line, or _legs_form_valid_arb:

    python tests.py

No test framework dependency on purpose (keeps requirements.txt minimal) -
plain functions, plain asserts, a runner at the bottom.
"""

from app import compute_arbs_from_odds, _canonical_line, _legs_form_valid_arb


def _row(**overrides):
    """A minimally-valid odds row, with sane defaults for every field
    compute_arbs_from_odds reads, so each test only needs to specify what's
    different about it."""
    base = {
        "event_id": "e1",
        "market_type": "moneyline",
        "selection_type": "home",
        "sportsbook": "draftkings",
        "selection": "Team A",
        "odds_decimal": 2.0,
        "odds_american": 100,
        "line": None,
        "is_active": True,
        "is_player_prop": False,
        "is_stale_pregame_price": False,
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "nfl",
    }
    base.update(overrides)
    return base


def test_same_book_is_rejected():
    """Bug: a market where only one book has data would compare that
    book's own two prices against each other and could show a fake arb."""
    rows = [
        _row(sportsbook="betmgm", selection_type="home", market_type="point_spread",
             line=-2.5, odds_decimal=2.5, odds_american=150, selection="A"),
        _row(sportsbook="betmgm", selection_type="away", market_type="point_spread",
             line=2.5, odds_decimal=2.05, odds_american=105, selection="B"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="betmgm,fanduel")
    assert len(arbs) == 0, f"expected 0 arbs (same book both sides), got {len(arbs)}"


def test_different_players_are_rejected():
    """Bug: two different WNBA players' point props shared a market_type
    and is_player_prop was unset on the row, so they got matched as if
    they were one 2-way market."""
    rows = [
        _row(event_id="e2", sportsbook="fanduel", market_type="1st_quarter_player_points",
             selection_type="under", line=3.5, odds_decimal=2.2, odds_american=120,
             selection="Player1 Under", league="wnba"),
        _row(event_id="e2", sportsbook="fanduel", market_type="1st_quarter_player_points",
             selection_type="over", line=3.5, odds_decimal=1.94, odds_american=-106,
             selection="Player2 Over", league="wnba"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="fanduel")
    assert len(arbs) == 0, f"expected 0 arbs (different players), got {len(arbs)}"


def test_real_moneyline_arb_is_found():
    rows = [
        _row(event_id="e3", sportsbook="draftkings", selection_type="home",
             odds_decimal=2.0, odds_american=100, selection="A"),
        _row(event_id="e3", sportsbook="fanduel", selection_type="away",
             odds_decimal=2.2, odds_american=120, selection="B"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,fanduel")
    assert len(arbs) == 1, f"expected 1 real cross-book moneyline arb, got {len(arbs)}"


def test_real_totals_arb_is_found():
    rows = [
        _row(event_id="e4", sportsbook="draftkings", market_type="total_points",
             selection_type="over", line=20.5, odds_decimal=2.0, odds_american=100,
             selection="Over"),
        _row(event_id="e4", sportsbook="fanduel", market_type="total_points",
             selection_type="under", line=20.5, odds_decimal=2.2, odds_american=120,
             selection="Under"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,fanduel")
    assert len(arbs) == 1, f"expected 1 real cross-book totals arb, got {len(arbs)}"


def test_nfl_true_complement_spread_is_found():
    """Broncos -0.5 (away, favored) and Chiefs +0.5 (home, underdog) are
    genuine complements of ONE proposition - this must still be found."""
    rows = [
        _row(event_id="e5", market_type="1st_quarter_point_spread", sportsbook="betmgm",
             selection_type="away", team_side="away", selection="Denver Broncos",
             odds_decimal=2.55, odds_american=155, line=-0.5,
             home_team="Kansas City Chiefs", away_team="Denver Broncos"),
        _row(event_id="e5", market_type="1st_quarter_point_spread", sportsbook="draftkings",
             selection_type="home", team_side="home", selection="KC Chiefs",
             odds_decimal=1.95, odds_american=-105, line=0.5,
             home_team="Kansas City Chiefs", away_team="Denver Broncos"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="betmgm,draftkings")
    assert len(arbs) == 1, f"expected 1 true-complement spread arb, got {len(arbs)}"


def test_nfl_conflicting_favorite_spread_is_rejected():
    """Bug: DraftKings has the Chiefs (home) favored -0.5; BetMGM
    independently has the Broncos (away) favored -0.5. Same magnitude,
    but both are 'my team wins outright' bets, not true complements - if
    the segment ties, neither cashes."""
    rows = [
        _row(event_id="e6", market_type="1st_quarter_point_spread", sportsbook="draftkings",
             selection_type="home", team_side="home", selection="KC Chiefs",
             odds_decimal=2.30, odds_american=130, line=-0.5,
             home_team="Kansas City Chiefs", away_team="Denver Broncos"),
        _row(event_id="e6", market_type="1st_quarter_point_spread", sportsbook="betmgm",
             selection_type="away", team_side="away", selection="Denver Broncos",
             odds_decimal=2.55, odds_american=155, line=-0.5,
             home_team="Kansas City Chiefs", away_team="Denver Broncos"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,betmgm")
    assert len(arbs) == 0, f"expected 0 arbs (books disagree on who's favored), got {len(arbs)}"


def test_mlb_run_line_self_consistency_without_team_side():
    """Bug: real MLB run_line rows have NO team_side field at all (unlike
    NFL spreads), which silently defeated the original team_side-based
    canonicalization. Must use selection_type instead, confirmed present
    on every spread-type row seen across every sport so far."""
    dodgers = _row(event_id="e7", market_type="1st_5_innings_run_line", sportsbook="betrivers",
                    selection_type="away", selection="LA Dodgers", odds_decimal=1.629,
                    odds_american=-159, line=-0.5, home_team="Cincinnati Reds",
                    away_team="Los Angeles Dodgers", league="mlb")
    reds = _row(event_id="e7", market_type="1st_5_innings_run_line", sportsbook="betrivers",
                selection_type="home", selection="CIN Reds", odds_decimal=2.18,
                odds_american=118, line=0.5, home_team="Cincinnati Reds",
                away_team="Los Angeles Dodgers", league="mlb")
    assert _canonical_line(dodgers) == _canonical_line(reds), (
        "same-book Dodgers/Reds run line rows should canonicalize to the same value"
    )


def test_mlb_run_line_conflicting_favorite_is_rejected():
    """Same bug as test_nfl_conflicting_favorite_spread_is_rejected, but
    for a sport/market where team_side is absent - this is the actual
    reported case (BetRivers Dodgers -0.5 vs BetMGM Reds -0.5)."""
    dodgers = _row(event_id="e8", market_type="1st_5_innings_run_line", sportsbook="betrivers",
                    selection_type="away", selection="LA Dodgers", odds_decimal=1.629,
                    odds_american=-159, line=-0.5, home_team="Cincinnati Reds",
                    away_team="Los Angeles Dodgers", league="mlb")
    reds_disagreeing = _row(event_id="e8", market_type="1st_5_innings_run_line", sportsbook="betmgm",
                             selection_type="home", selection="CIN Reds", odds_decimal=3.4,
                             odds_american=240, line=-0.5, home_team="Cincinnati Reds",
                             away_team="Los Angeles Dodgers", league="mlb")
    arbs = compute_arbs_from_odds([dodgers, reds_disagreeing], min_profit=0.0, books="betrivers,betmgm")
    assert len(arbs) == 0, f"expected 0 arbs (BetMGM disagrees on who's favored), got {len(arbs)}"


def test_mlb_run_line_true_complement_is_found():
    dodgers = _row(event_id="e9", market_type="1st_5_innings_run_line", sportsbook="betrivers",
                    selection_type="away", selection="LA Dodgers", odds_decimal=1.629,
                    odds_american=-159, line=-0.5, home_team="Cincinnati Reds",
                    away_team="Los Angeles Dodgers", league="mlb")
    reds_agreeing = _row(event_id="e9", market_type="1st_5_innings_run_line", sportsbook="betmgm",
                          selection_type="home", selection="CIN Reds", odds_decimal=3.0,
                          odds_american=200, line=0.5, home_team="Cincinnati Reds",
                          away_team="Los Angeles Dodgers", league="mlb")
    arbs = compute_arbs_from_odds([dodgers, reds_agreeing], min_profit=0.0, books="betrivers,betmgm")
    assert len(arbs) == 1, f"expected 1 true-complement MLB run line arb, got {len(arbs)}"


def test_stale_price_is_excluded():
    """A stale row could otherwise be picked as the 'best' price for a
    side purely because it looks more attractive, producing an arb
    against a number that isn't actually live/bettable."""
    rows = [
        _row(event_id="e10", sportsbook="draftkings", selection_type="home",
             odds_decimal=2.0, odds_american=100, selection="A"),
        _row(event_id="e10", sportsbook="fanduel", selection_type="away",
             odds_decimal=2.2, odds_american=120, selection="B", is_stale_pregame_price=True),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,fanduel")
    assert len(arbs) == 0, f"expected 0 arbs (one leg is stale), got {len(arbs)}"


def test_legs_form_valid_arb_helper():
    assert _legs_form_valid_arb([{"sportsbook": "betmgm"}, {"sportsbook": "betmgm"}]) is False
    assert _legs_form_valid_arb([{"sportsbook": "betmgm"}, {"sportsbook": "fanduel"}]) is True


ALL_TESTS = [
    test_same_book_is_rejected,
    test_different_players_are_rejected,
    test_real_moneyline_arb_is_found,
    test_real_totals_arb_is_found,
    test_nfl_true_complement_spread_is_found,
    test_nfl_conflicting_favorite_spread_is_rejected,
    test_mlb_run_line_self_consistency_without_team_side,
    test_mlb_run_line_conflicting_favorite_is_rejected,
    test_mlb_run_line_true_complement_is_found,
    test_stale_price_is_excluded,
    test_legs_form_valid_arb_helper,
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
