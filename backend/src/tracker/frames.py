import gzip
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonpatch

TEAM_KEYS = ("id", "name", "abbreviation", "teamName")


def trim(feed: dict[str, Any]) -> dict[str, Any]:
    gd = feed.get("gameData", {})
    ld = feed.get("liveData", {})
    return {
        "gamePk": feed.get("gamePk"),
        "metaData": feed.get("metaData", {}),
        "gameData": {
            "game": gd.get("game", {}),
            "datetime": gd.get("datetime", {}),
            "status": gd.get("status", {}),
            "teams": {
                side: {k: team.get(k) for k in TEAM_KEYS}
                for side, team in gd.get("teams", {}).items()
            },
        },
        "liveData": {
            "plays": {"allPlays": ld.get("plays", {}).get("allPlays", [])},
            "linescore": ld.get("linescore", {}),
        },
    }


def diff(prev: dict[str, Any], cur: dict[str, Any]) -> list[dict[str, Any]]:
    return list(jsonpatch.make_patch(prev, cur).patch)


def apply(doc: dict[str, Any], patch: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = jsonpatch.apply_patch(doc, patch)
    return result


def timecode_of(feed: dict[str, Any]) -> str | None:
    return feed.get("metaData", {}).get("timeStamp")


def parse_timecode(tc: str) -> datetime:
    return datetime.strptime(tc, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)


def write_recording(path: Path, frames: Iterable[tuple[str, dict[str, Any]]]) -> int:
    count = 0
    prev: dict[str, Any] | None = None
    with gzip.open(path, "wt") as f:
        for tc, frame in frames:
            rec = (
                {"timecode": tc, "full": frame}
                if prev is None
                else {
                    "timecode": tc,
                    "patch": diff(prev, frame),
                }
            )
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            prev = frame
            count += 1
    return count


def read_recording(path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    doc: dict[str, Any] | None = None
    with gzip.open(path, "rt") as f:
        for line in f:
            rec = json.loads(line)
            if "full" in rec:
                doc = rec["full"]
            else:
                assert doc is not None
                doc = apply(doc, rec["patch"])
            assert doc is not None
            yield rec["timecode"], doc


def rebuild(rows: Iterable[tuple[str, str, Any]]) -> Iterator[tuple[str, dict[str, Any]]]:
    doc: dict[str, Any] | None = None
    for timecode, kind, body in rows:
        if kind == "full":
            doc = body
        else:
            assert doc is not None, f"patch at {timecode} without a preceding full frame"
            doc = apply(doc, body)
        assert doc is not None
        yield timecode, doc
