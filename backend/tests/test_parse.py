from typing import Any

from tracker.parse import parse_feed


def test_final_frame_totals(final_frame: dict[str, Any]) -> None:
    parsed = parse_feed(final_frame)
    assert parsed.game.status == "Final"
    assert (parsed.game.away_team, parsed.game.home_team) == ("LAD", "MIL")
    assert (parsed.game.away_score, parsed.game.home_score) == (1, 2)
    assert len(parsed.plate_appearances) == 70
    expected = sum(
        1
        for play in final_frame["liveData"]["plays"]["allPlays"]
        for ev in play["playEvents"]
        if ev.get("isPitch")
    )
    assert len(parsed.pitches) == expected


def test_pitch_keys_unique_and_states_valid(final_frame: dict[str, Any]) -> None:
    pitches = parse_feed(final_frame).pitches
    keys = [(p.at_bat_index, p.pitch_number) for p in pitches]
    assert len(set(keys)) == len(keys)
    for p in pitches:
        assert 0 <= p.balls <= 3
        assert 0 <= p.strikes <= 2
        assert 0 <= p.outs <= 2


def test_first_pitch_of_each_half_starts_clean(final_frame: dict[str, Any]) -> None:
    pitches = parse_feed(final_frame).pitches
    seen: set[tuple[int, str]] = set()
    for p in pitches:
        if (p.inning, p.half) in seen:
            continue
        seen.add((p.inning, p.half))
        assert (p.balls, p.strikes, p.outs) == (0, 0, 0)
        assert not (p.on_1b or p.on_2b or p.on_3b)


def test_scores_before_each_pa_match_prior_result(final_frame: dict[str, Any]) -> None:
    parsed = parse_feed(final_frame)
    first_pitch = {}
    for p in parsed.pitches:
        first_pitch.setdefault(p.at_bat_index, p)
    away = home = 0
    for pa in parsed.plate_appearances:
        p = first_pitch.get(pa.at_bat_index)
        if p is not None:
            assert (p.away_score, p.home_score) == (away, home)
        away, home = pa.away_score, pa.home_score


def _feed(game_type: str, inning: int, plays: list[dict[str, Any]]) -> dict[str, Any]:
    team = {"id": 1, "abbreviation": "AAA"}
    return {
        "gamePk": 1,
        "metaData": {"timeStamp": "20260701_000000"},
        "gameData": {
            "game": {"type": game_type, "season": "2026"},
            "datetime": {"officialDate": "2026-07-01"},
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
            "teams": {"home": team, "away": {**team, "id": 2, "abbreviation": "BBB"}},
        },
        "liveData": {
            "plays": {"allPlays": plays},
            "linescore": {"currentInning": inning, "isTopInning": True},
        },
    }


def _play(idx: int, inning: int, events: list[dict[str, Any]], runners: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    return {
        "about": {"atBatIndex": idx, "inning": inning, "halfInning": "top", "isComplete": False},
        "matchup": {"batter": {"id": 100 + idx}, "pitcher": {"id": 999}},
        "result": {},
        "count": {"outs": 0},
        "playEvents": events,
        "runners": runners,
    }


def _pitch_ev(index: int, number: int, balls: int = 0, strikes: int = 0) -> dict[str, Any]:
    return {
        "index": index,
        "pitchNumber": number,
        "isPitch": True,
        "count": {"balls": balls, "strikes": strikes, "outs": 0},
        "details": {},
    }


def _move(runner: int, start: str | None, end: str | None, play_index: int, out: bool = False):  # type: ignore[no-untyped-def]
    return {
        "movement": {"start": start, "end": end, "isOut": out},
        "details": {"runner": {"id": runner}, "playIndex": play_index},
    }


def test_regular_season_extra_innings_start_with_runner_on_second() -> None:
    plays = [_play(0, 10, [_pitch_ev(0, 1)], [])]
    assert parse_feed(_feed("R", 10, plays)).pitches[0].on_2b
    assert not parse_feed(_feed("L", 10, plays)).pitches[0].on_2b


def test_multi_segment_runner_scores_and_bases_update() -> None:
    plays = [
        _play(0, 1, [_pitch_ev(0, 1)], [_move(100, None, "2B", 0)]),
        _play(
            1,
            1,
            [_pitch_ev(0, 1)],
            [
                _move(101, None, "1B", 0),
                _move(100, "2B", "3B", 0),
                _move(100, "3B", "score", 0),
            ],
        ),
        _play(2, 1, [_pitch_ev(0, 1, balls=1), _pitch_ev(1, 2)], [_move(101, "1B", "2B", 0)]),
    ]
    pitches = parse_feed(_feed("R", 1, plays)).pitches
    second_pa = pitches[1]
    assert (second_pa.on_1b, second_pa.on_2b, second_pa.on_3b) == (False, True, False)
    third_pa_first, third_pa_second = pitches[2], pitches[3]
    assert (third_pa_first.on_1b, third_pa_first.on_2b) == (True, False)
    assert third_pa_first.away_score == 1
    assert (third_pa_second.on_1b, third_pa_second.on_2b) == (False, True)
    assert third_pa_second.balls == 1


def test_out_on_bases_counts_toward_outs() -> None:
    plays = [
        _play(0, 1, [_pitch_ev(0, 1)], [_move(100, None, "1B", 0)]),
        _play(1, 1, [_pitch_ev(0, 1), _pitch_ev(1, 2)], [_move(100, "1B", None, 0, out=True)]),
    ]
    pitches = parse_feed(_feed("R", 1, plays)).pitches
    assert pitches[1].on_1b and pitches[1].outs == 0
    assert not pitches[2].on_1b and pitches[2].outs == 1
