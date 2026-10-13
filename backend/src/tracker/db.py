from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

metadata = MetaData()

JSONType = JSON().with_variant(JSONB(), "postgresql")

games = Table(
    "games",
    metadata,
    Column("game_pk", Integer, primary_key=True),
    Column("game_date", Date, nullable=False),
    Column("game_type", String(2), nullable=False),
    Column("season", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("detailed_status", String(64), nullable=False),
    Column("home_team", String(8), nullable=False),
    Column("away_team", String(8), nullable=False),
    Column("home_team_id", Integer, nullable=False),
    Column("away_team_id", Integer, nullable=False),
    Column("home_score", Integer, nullable=False),
    Column("away_score", Integer, nullable=False),
    Column("inning", Integer),
    Column("half", String(8)),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_games_game_date", "game_date"),
)

plate_appearances = Table(
    "plate_appearances",
    metadata,
    Column("game_pk", Integer, primary_key=True),
    Column("at_bat_index", Integer, primary_key=True),
    Column("inning", Integer, nullable=False),
    Column("half", String(8), nullable=False),
    Column("batter_id", Integer, nullable=False),
    Column("batter_name", String(64), nullable=False),
    Column("pitcher_id", Integer, nullable=False),
    Column("pitcher_name", String(64), nullable=False),
    Column("bat_side", String(1)),
    Column("pitch_hand", String(1)),
    Column("event_type", String(64)),
    Column("event", String(64)),
    Column("description", Text),
    Column("rbi", Integer),
    Column("away_score", Integer, nullable=False),
    Column("home_score", Integer, nullable=False),
    Column("is_complete", Boolean, nullable=False),
    Column("content_hash", String(16), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

pitches = Table(
    "pitches",
    metadata,
    Column("game_pk", Integer, primary_key=True),
    Column("at_bat_index", Integer, primary_key=True),
    Column("pitch_number", Integer, primary_key=True),
    Column("event_index", Integer, nullable=False),
    Column("play_id", String(40)),
    Column("inning", Integer, nullable=False),
    Column("half", String(8), nullable=False),
    Column("batter_id", Integer, nullable=False),
    Column("pitcher_id", Integer, nullable=False),
    Column("bat_side", String(1)),
    Column("pitch_hand", String(1)),
    Column("balls", Integer, nullable=False),
    Column("strikes", Integer, nullable=False),
    Column("outs", Integer, nullable=False),
    Column("on_1b", Boolean, nullable=False),
    Column("on_2b", Boolean, nullable=False),
    Column("on_3b", Boolean, nullable=False),
    Column("away_score", Integer, nullable=False),
    Column("home_score", Integer, nullable=False),
    Column("pitch_type", String(4)),
    Column("pitch_type_desc", String(32)),
    Column("type_confidence", Float),
    Column("call_code", String(4)),
    Column("call_desc", String(64)),
    Column("is_in_play", Boolean, nullable=False),
    Column("is_strike", Boolean, nullable=False),
    Column("is_ball", Boolean, nullable=False),
    Column("start_speed", Float),
    Column("end_speed", Float),
    Column("spin_rate", Float),
    Column("spin_direction", Float),
    Column("pfx_x", Float),
    Column("pfx_z", Float),
    Column("ivb", Float),
    Column("hb", Float),
    Column("px", Float),
    Column("pz", Float),
    Column("sz_top", Float),
    Column("sz_bot", Float),
    Column("x0", Float),
    Column("z0", Float),
    Column("extension", Float),
    Column("plate_time", Float),
    Column("zone", Integer),
    Column("launch_speed", Float),
    Column("launch_angle", Float),
    Column("total_distance", Float),
    Column("hit_x", Float),
    Column("hit_y", Float),
    Column("trajectory", String(32)),
    Column("start_time", DateTime(timezone=True)),
    Column("pitch_type_pred", String(4)),
    Column("home_wp", Float),
    Column("run_exp", Float),
    Column("content_hash", String(16), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_pitches_pitcher", "pitcher_id"),
    Index("ix_pitches_batter", "batter_id"),
)

raw_events = Table(
    "raw_events",
    metadata,
    Column("game_pk", Integer, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("timecode", String(16), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("body", JSONType, nullable=False),
    Column("received_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

CHUNK = 400


def _chunks(
    rows: Sequence[dict[str, Any]], size: int = CHUNK
) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def _upsert(
    conn: AsyncConnection, table: Table, keys: list[str], rows: Sequence[dict[str, Any]]
) -> None:
    for chunk in _chunks(rows):
        stmt = pg_insert(table).values(list(chunk))
        update: dict[str, Any] = {
            c.name: stmt.excluded[c.name]
            for c in table.columns
            if c.name not in keys and c.name != "updated_at"
        }
        update["updated_at"] = func.now()
        where = None
        if "content_hash" in table.c:
            where = table.c.content_hash != stmt.excluded.content_hash
        await conn.execute(
            stmt.on_conflict_do_update(index_elements=keys, set_=update, where=where)
        )


async def upsert_game(conn: AsyncConnection, row: dict[str, Any]) -> None:
    await _upsert(conn, games, ["game_pk"], [row])


async def upsert_plate_appearances(conn: AsyncConnection, rows: Sequence[dict[str, Any]]) -> None:
    await _upsert(conn, plate_appearances, ["game_pk", "at_bat_index"], rows)


async def upsert_pitches(conn: AsyncConnection, rows: Sequence[dict[str, Any]]) -> None:
    await _upsert(conn, pitches, ["game_pk", "at_bat_index", "pitch_number"], rows)


async def insert_raw(
    conn: AsyncConnection, game_pk: int, seq: int, timecode: str, kind: str, body: Any
) -> None:
    await conn.execute(
        raw_events.insert().values(
            game_pk=game_pk, seq=seq, timecode=timecode, kind=kind, body=body
        )
    )


async def next_raw_seq(conn: AsyncConnection, game_pk: int) -> int:
    value = await conn.scalar(
        select(func.max(raw_events.c.seq)).where(raw_events.c.game_pk == game_pk)
    )
    return 0 if value is None else int(value) + 1


async def pitch_rows(conn: AsyncConnection, game_pk: int) -> dict[tuple[int, int], dict[str, Any]]:
    rows = await conn.execute(select(pitches).where(pitches.c.game_pk == game_pk))
    return {(r.at_bat_index, r.pitch_number): dict(r._mapping) for r in rows}


async def pa_hashes(conn: AsyncConnection, game_pk: int) -> dict[int, str]:
    rows = await conn.execute(
        select(plate_appearances.c.at_bat_index, plate_appearances.c.content_hash).where(
            plate_appearances.c.game_pk == game_pk
        )
    )
    return {r.at_bat_index: r.content_hash for r in rows}


async def latest_timecode(conn: AsyncConnection, game_pk: int) -> str | None:
    value: str | None = await conn.scalar(
        select(func.max(raw_events.c.timecode)).where(raw_events.c.game_pk == game_pk)
    )
    return value


async def delete_game(conn: AsyncConnection, game_pk: int) -> None:
    for table in (pitches, plate_appearances, raw_events, games):
        await conn.execute(table.delete().where(table.c.game_pk == game_pk))


async def raw_frames(conn: AsyncConnection, game_pk: int) -> list[tuple[str, str, Any]]:
    rows = await conn.execute(
        select(raw_events.c.timecode, raw_events.c.kind, raw_events.c.body)
        .where(raw_events.c.game_pk == game_pk)
        .order_by(raw_events.c.seq)
    )
    return [(r.timecode, r.kind, r.body) for r in rows]
