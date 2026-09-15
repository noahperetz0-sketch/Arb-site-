"""Regression tests for the arb-matching logic in app.py.

Every case here is a real bug found and fixed during development - not
hypothetical. Run this before pushing any change to compute_arbs_from_odds,
_canonical_line, or _legs_form_valid_arb:

    python tests.py

No test framework dependency on purpose (keeps requirements.txt minimal) -
plain functions, plain asserts, a runner at the bottom.
"""

from datetime import datetime, timedelta, timezone

from app import (
    compute_arbs_from_odds,
    _canonical_line,
    _canonical_event_id,
    _legs_form_valid_arb,
    _is_stale_live_row,
    _format_leg_selection,
    MAX_LIVE_ROW_AGE_SECONDS,
)


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
        "is_live": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
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


def test_stale_live_price_is_excluded():
    """Bug: real live soccer "Total Goals" data showed BetRivers at -155
    (is_stale_pregame_price=False, since that flag only covers pregame
    prices) when the actual live BetRivers line had already moved to -560,
    almost certainly right after a goal - producing a fake ~20% "arb" that
    wasn't real (the deep link 404'd - BetRivers had already invalidated
    that quote). is_stale_pregame_price alone doesn't catch this; only the
    live-row age check does."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=MAX_LIVE_ROW_AGE_SECONDS + 60)).isoformat()
    rows = [
        _row(event_id="e11", market_type="total_goals", sportsbook="draftkings",
             selection_type="under", line=1.5, odds_decimal=4.38, odds_american=338,
             selection="Under", is_live=True),
        _row(event_id="e11", market_type="total_goals", sportsbook="betrivers",
             selection_type="over", line=1.5, odds_decimal=1.645, odds_american=-155,
             selection="Over", is_live=True, is_stale_pregame_price=False, timestamp=stale_ts),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,betrivers")
    assert len(arbs) == 0, f"expected 0 arbs (live leg's price is stale), got {len(arbs)}"


def test_fresh_live_arb_is_still_found():
    """The live-staleness check must not blanket-reject every live arb -
    only ones with an old timestamp."""
    rows = [
        _row(event_id="e12", market_type="total_goals", sportsbook="draftkings",
             selection_type="under", line=1.5, odds_decimal=2.1, odds_american=110,
             selection="Under", is_live=True),
        _row(event_id="e12", market_type="total_goals", sportsbook="betrivers",
             selection_type="over", line=1.5, odds_decimal=2.05, odds_american=105,
             selection="Over", is_live=True),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,betrivers")
    assert len(arbs) == 1, f"expected 1 real live arb (both legs fresh), got {len(arbs)}"


def test_is_stale_live_row_helper():
    now = datetime.now(timezone.utc)
    fresh = _row(is_live=True, timestamp=now.isoformat())
    stale = _row(is_live=True, timestamp=(now - timedelta(seconds=MAX_LIVE_ROW_AGE_SECONDS + 1)).isoformat())
    missing_ts = _row(is_live=True, timestamp=None)
    not_live = _row(is_live=False, timestamp=(now - timedelta(days=1)).isoformat())

    assert _is_stale_live_row(fresh, now=now) is False
    assert _is_stale_live_row(stale, now=now) is True
    assert _is_stale_live_row(missing_ts, now=now) is True
    assert _is_stale_live_row(not_live, now=now) is False


def test_doubleheader_suffix_reunited_across_books():
    """Bug: SharpAPI's own Event Matching docs confirm the SAME physical
    doubleheader game can carry two different event_id strings across
    books - one book reports both games of a same-day doubleheader in one
    update (getting the _g{N}-suffixed id), another sees only one game
    (getting the bare bucketed id). Grouping strictly by raw event_id
    would silently miss a real cross-book arb whenever that split occurs."""
    rows = [
        _row(event_id="mlb_athletics_mariners_2026-05-02_b0", sportsbook="draftkings",
             selection_type="home", odds_decimal=2.0, odds_american=100, selection="A"),
        _row(event_id="mlb_athletics_mariners_2026-05-02_b0_g1", sportsbook="fanduel",
             selection_type="away", odds_decimal=2.2, odds_american=120, selection="B"),
    ]
    arbs = compute_arbs_from_odds(rows, min_profit=0.0, books="draftkings,fanduel")
    assert len(arbs) == 1, f"expected the doubleheader-suffix split to be reunited into 1 arb, got {len(arbs)}"


def test_canonical_event_id_helper():
    assert _canonical_event_id("mlb_athletics_mariners_2026-05-02_b0_g1") == "mlb_athletics_mariners_2026-05-02_b0"
    assert _canonical_event_id("mlb_athletics_mariners_2026-05-02_b0") == "mlb_athletics_mariners_2026-05-02_b0"
    assert _canonical_event_id("nba_celtics_lakers_2026-02-08_b3") == "nba_celtics_lakers_2026-02-08_b3"
    assert _canonical_event_id(None) == ""
    # Never strips the start-time bucket itself (_b{N}) - only a trailing
    # doubleheader suffix (_g{N}) that comes after it.
    assert _canonical_event_id("mlb_athletics_mariners_2026-05-02_b0") != "mlb_athletics_mariners_2026-05-02"


def test_format_leg_selection_helper():
    """Bug: a real tennis slate showed multiple totals legs all as bare
    "Over"/"Under" with no line and no indication of which market - two
    different "Total Sets"/"3rd Set Total Games" legs were indistinguishable
    in the UI. SharpAPI's own "selection" field really is just the bare
    word for totals (and just the bare team name for spreads), confirmed
    against real rows - the line is always a separate field."""
    # Totals: line + a unit word scraped from "Total <noun>" in the label.
    assert _format_leg_selection("Under", "under", 2.5, "Total Sets (2.5)") == "Under 2.5 Sets"
    assert _format_leg_selection("Over", "over", 12.5, "3Rd Set Total Games (12.5)") == "Over 12.5 Games"
    # No "Total <noun>" pattern in the label - falls back to just the line,
    # not a guessed unit.
    assert _format_leg_selection("Over", "over", 2.5, "Something Else (2.5)") == "Over 2.5"
    # Spreads: bare team name + signed line, no invented unit word.
    assert _format_leg_selection("KC Chiefs", "home", -0.5, "Point Spread (-0.5)") == "KC Chiefs -0.5"
    assert _format_leg_selection("DEN Broncos", "away", 0.5, "Point Spread (-0.5)") == "DEN Broncos +0.5"
    # No usable numeric line (moneyline, draw, outright) - unchanged.
    assert _format_leg_selection("Alex Barrena", None, None, "Moneyline") == "Alex Barrena"
    assert _format_leg_selection("Draw", "draw", None, "Moneyline") == "Draw"
    # selection_type absent (unconfirmed on the Arbitrage endpoint's legs)
    # still works via the bare selection text itself.
    assert _format_leg_selection("Under", None, 8.5, "Total Points (8.5)") == "Under 8.5 Points"


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
    test_stale_live_price_is_excluded,
    test_fresh_live_arb_is_still_found,
    test_is_stale_live_row_helper,
    test_doubleheader_suffix_reunited_across_books,
    test_canonical_event_id_helper,
    test_format_leg_selection_helper,
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
