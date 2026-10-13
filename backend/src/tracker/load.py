import asyncio
import logging
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker import db
from tracker.ingest import content_hash
from tracker.models.runtime import Models

log = logging.getLogger(__name__)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    df = df.astype(object).where(df.notna(), None)
    return [{str(k): v for k, v in r.items()} for r in df.to_dict(orient="records")]


async def load_parquet(
    engine: AsyncEngine, models: Models, data_dir: Path, season: int | None = None
) -> int:
    con = duckdb.connect()
    total = 0
    files = await asyncio.to_thread(
        lambda: sorted(data_dir.glob(f"{season or '*'}/games-*.parquet"))
    )
    for games_file in files:
        part = games_file.name.removeprefix("games-")
        folder = games_file.parent
        games = con.execute(f"select * from read_parquet('{games_file}')").df()
        pas = con.execute(f"select * from read_parquet('{folder / ('pas-' + part)}')").df()
        pitches = con.execute(f"select * from read_parquet('{folder / ('pitches-' + part)}')").df()

        game_rows = _records(games)
        pa_rows = _records(pas)
        pitch_rows = _records(pitches)
        for row in pa_rows:
            row["content_hash"] = content_hash(row)
        for row in pitch_rows:
            if row["start_time"] is not None:
                row["start_time"] = pd.Timestamp(row["start_time"]).to_pydatetime()
            row["content_hash"] = content_hash(row)
        models.enrich_pitches(pitch_rows)
        for row in game_rows:
            row["game_date"] = pd.Timestamp(row["game_date"]).date()

        async with engine.begin() as conn:
            for row in game_rows:
                await db.upsert_game(conn, row)
            await db.upsert_plate_appearances(conn, pa_rows)
            await db.upsert_pitches(conn, pitch_rows)
        total += len(game_rows)
        log.info("loaded %s (%d games)", games_file, len(game_rows))
    return total
