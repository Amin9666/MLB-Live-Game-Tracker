import pytest

from tracker.config import get_settings
from tracker.models.runtime import Models
from tracker.parse import GameState

models = Models.load(get_settings().artifacts_dir)
pytestmark = pytest.mark.skipif(
    models.wp is None or models.pitch_clf is None or not models.re24,
    reason="trained artifacts not present",
)


def _state(**kw: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        inning=5,
        half="top",
        home_score=0,
        away_score=0,
        outs=0,
        on_1b=False,
        on_2b=False,
        on_3b=False,
        balls=0,
        strikes=0,
    )
    base.update(kw)
    return base


def test_run_expectancy_orders_states() -> None:
    loaded = models.run_exp(_state(on_1b=True, on_2b=True, on_3b=True))
    empty_two_out = models.run_exp(_state(outs=2))
    assert loaded is not None and empty_two_out is not None
    assert loaded > 2.0 > 0.5 > empty_two_out
    assert models.run_exp(_state(balls=3)) > models.run_exp(_state(strikes=2))  # type: ignore[operator]


def test_win_prob_responds_to_score_and_inning() -> None:
    tied, home_up_late, home_down_late = models.home_wp(
        [
            _state(inning=1),
            _state(inning=9, half="top", home_score=3, outs=2),
            _state(inning=9, half="bottom", away_score=3, outs=2),
        ]
    )
    assert tied is not None and 0.45 < tied < 0.6
    assert home_up_late is not None and home_up_late > 0.95
    assert home_down_late is not None and home_down_late < 0.05


def test_state_wp_handles_final_and_three_outs() -> None:
    common = dict(
        game_pk=1,
        detailed_status="",
        balls=0,
        strikes=0,
        on_1b=False,
        on_2b=False,
        on_3b=False,
        batter_id=None,
        pitcher_id=None,
        timecode=None,
    )
    final = GameState(
        status="Final", inning=9, half="top", outs=3, away_score=1, home_score=2, **common
    )  # type: ignore[arg-type]
    assert models.state_wp(final) == 1.0
    end_of_top = GameState(
        status="Live", inning=9, half="top", outs=3, away_score=2, home_score=2, **common
    )  # type: ignore[arg-type]
    wp = models.state_wp(end_of_top)
    assert wp is not None and wp > 0.6


def test_pitch_classifier_separates_obvious_pitches() -> None:
    fastball = dict(
        start_speed=97.5,
        spin_rate=2400,
        spin_direction=210,
        pfx_x=-8,
        pfx_z=9,
        ivb=17,
        hb=9,
        x0=-1.8,
        z0=5.8,
        extension=6.6,
        pitch_hand="R",
    )
    curve = dict(
        start_speed=79,
        spin_rate=2800,
        spin_direction=40,
        pfx_x=6,
        pfx_z=-8,
        ivb=-14,
        hb=-8,
        x0=-1.8,
        z0=5.9,
        extension=6.2,
        pitch_hand="R",
    )
    assert models.classify([fastball, curve]) == ["FF", "CU"]
