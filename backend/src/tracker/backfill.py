import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from tracker import frames
from tracker.mlb_client import MlbClient
from tracker.parse import parse_feed

log = logging.getLogger(__name__)


async def season_game_pks(client: MlbClient, season: int, game_types: str = "R") -> list[int]:
    games = await client.schedule(date(season, 3, 1), date(season, 11, 15), game_types=game_types)
    pks = {
        int(g["gamePk"])
        for g in games
        if g["status"].get("codedGameState") == "F" and g.get("resumedFrom") is None
    }
    return sorted(pks)


async def backfill_season(
    client: MlbClient,
    season: int,
    out_dir: Path,
    part_size: int = 250,
    limit: int | None = None,
) -> int:
    season_dir = out_dir / str(season)
    season_dir.mkdir(parents=True, exist_ok=True)
    done = _done_games(season_dir)
    pks = [pk for pk in await season_game_pks(client, season) if pk not in done]
    if limit is not None:
        pks = pks[:limit]
    log.info("season %s: %d games to fetch (%d already done)", season, len(pks), len(done))

    part = len(list(season_dir.glob("games-*.parquet")))
    total = 0
    for i in range(0, len(pks), part_size):
        batch = pks[i : i + part_size]
        feeds = await asyncio.gather(*(_fetch(client, pk) for pk in batch))
        game_rows: list[dict[str, Any]] = []
        pa_rows: list[dict[str, Any]] = []
        pitch_rows: list[dict[str, Any]] = []
        for feed in feeds:
            if feed is None:
                continue
            parsed = parse_feed(frames.trim(feed))
            if parsed.game.status != "Final":
                continue
            game_rows.append(parsed.game.model_dump())
            pa_rows.extend(p.model_dump() for p in parsed.plate_appearances)
            pitch_rows.extend(p.model_dump() for p in parsed.pitches)
        if game_rows:
            pd.DataFrame(game_rows).to_parquet(season_dir / f"games-{part:04d}.parquet")
            pd.DataFrame(pa_rows).to_parquet(season_dir / f"pas-{part:04d}.parquet")
            pd.DataFrame(pitch_rows).to_parquet(season_dir / f"pitches-{part:04d}.parquet")
            part += 1
            total += len(game_rows)
        log.info("season %s: %d/%d games", season, i + len(batch), len(pks))
    return total


async def _fetch(client: MlbClient, game_pk: int) -> dict[str, Any] | None:
    try:
        return await client.live_feed(game_pk)
    except Exception:
        log.exception("failed to fetch game %s", game_pk)
        return None


def _done_games(season_dir: Path) -> set[int]:
    done: set[int] = set()
    for f in season_dir.glob("games-*.parquet"):
        done.update(int(pk) for pk in pd.read_parquet(f, columns=["game_pk"])["game_pk"])
    return done


async def record_game(client: MlbClient, game_pk: int, out: Path) -> int:
    timecodes = await client.timestamps(game_pk)

    async def fetch(tc: str) -> dict[str, Any]:
        return frames.trim(await client.live_feed(game_pk, tc))

    trimmed = await asyncio.gather(*(fetch(tc) for tc in timecodes))
    out.parent.mkdir(parents=True, exist_ok=True)
    return frames.write_recording(out, zip(timecodes, trimmed, strict=True))
