from pathlib import Path
from typing import Any

from tracker import frames


def test_recording_round_trip(tmp_path: Path, recorded: list[tuple[str, dict[str, Any]]]) -> None:
    subset = recorded[::10]
    out = tmp_path / "rec.jsonl.gz"
    assert frames.write_recording(out, subset) == len(subset)
    assert list(frames.read_recording(out)) == subset


def test_recording_timecodes_are_ordered(recorded: list[tuple[str, dict[str, Any]]]) -> None:
    timecodes = [tc for tc, _ in recorded]
    assert timecodes == sorted(timecodes)
    assert len(set(timecodes)) == len(timecodes)


def test_trim_keeps_only_what_the_parser_needs(final_frame: dict[str, Any]) -> None:
    feed = {
        **final_frame,
        "gameData": {**final_frame["gameData"], "players": {"ID1": {}}},
        "liveData": {**final_frame["liveData"], "boxscore": {"teams": {}}},
    }
    trimmed = frames.trim(feed)
    assert "players" not in trimmed["gameData"]
    assert "boxscore" not in trimmed["liveData"]
    assert trimmed == frames.trim(trimmed)


def test_rebuild_from_full_and_patches(recorded: list[tuple[str, dict[str, Any]]]) -> None:
    window = recorded[100:110]
    rows: list[tuple[str, str, Any]] = [(window[0][0], "full", window[0][1])]
    for (_, prev), (tc, cur) in zip(window, window[1:], strict=False):
        rows.append((tc, "patch", frames.diff(prev, cur)))
    assert list(frames.rebuild(rows)) == window
