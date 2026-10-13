import argparse
import asyncio
import logging
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tracker.config import get_settings
from tracker.mlb_client import MlbClient
from tracker.models.runtime import Models
from tracker.stream import EventBus


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest", help="poll today's live games and stream them")

    replay = sub.add_parser("replay", help="replay a recorded game through the pipeline")
    replay.add_argument("recording", type=Path)
    replay.add_argument("--speed", type=float, default=30.0)
    replay.add_argument("--max-gap", type=float, default=2.0)
    replay.add_argument(
        "--fresh", action="store_true", help="clear the game's rows and stream first"
    )

    record = sub.add_parser("record", help="record every feed timecode of a game")
    record.add_argument("game_pk", type=int)
    record.add_argument("--out", type=Path)

    backfill = sub.add_parser("backfill", help="download final feeds for seasons to parquet")
    backfill.add_argument("seasons", type=int, nargs="+")
    backfill.add_argument("--out", type=Path, default=Path("data"))
    backfill.add_argument("--limit", type=int)
    backfill.add_argument("--concurrency", type=int, default=12)

    load = sub.add_parser("load", help="load backfilled parquet into Postgres")
    load.add_argument("--data", type=Path, default=Path("data"))
    load.add_argument("--season", type=int)

    train = sub.add_parser("train", help="train run expectancy, win prob, pitch type models")
    train.add_argument("--data", type=Path, default=Path("data"))
    train.add_argument("--out", type=Path)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "train":
        _train(args)
    else:
        asyncio.run(_dispatch(args))


async def _dispatch(args: argparse.Namespace) -> None:
    settings = get_settings()
    if args.cmd == "record":
        from tracker.backfill import record_game

        out = args.out or Path(f"recordings/{args.game_pk}.jsonl.gz")
        async with MlbClient(settings.mlb_base_url) as client:
            n = await record_game(client, args.game_pk, out)
        logging.info("recorded %d frames to %s", n, out)
        return
    if args.cmd == "backfill":
        from tracker.backfill import backfill_season

        async with MlbClient(settings.mlb_base_url, concurrency=args.concurrency) as client:
            for season in args.seasons:
                n = await backfill_season(client, season, args.out, limit=args.limit)
                logging.info("season %s: wrote %d games", season, n)
        return

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bus = EventBus(redis, maxlen=settings.stream_maxlen)
    models = Models.load(settings.artifacts_dir)
    try:
        if args.cmd == "ingest":
            from tracker.ingest import run_live

            async with MlbClient(settings.mlb_base_url) as client:
                await run_live(
                    client,
                    engine,
                    bus,
                    models,
                    interval=settings.schedule_interval_seconds,
                    floor=settings.poll_floor_seconds,
                )
        elif args.cmd == "replay":
            await _replay(args, engine, bus, models)
        elif args.cmd == "load":
            from tracker.load import load_parquet

            n = await load_parquet(engine, models, args.data, args.season)
            logging.info("loaded %d games", n)
    finally:
        await redis.aclose()
        await engine.dispose()


async def _replay(
    args: argparse.Namespace, engine: AsyncEngine, bus: EventBus, models: Models
) -> None:
    from tracker import db, frames
    from tracker.ingest import GameIngester, ReplayFeedSource

    _, first = next(frames.read_recording(args.recording))
    game_pk = int(first["gamePk"])
    if args.fresh:
        async with engine.begin() as conn:
            await db.delete_game(conn, game_pk)
        await bus.reset(game_pk)
    source = ReplayFeedSource(args.recording, speed=args.speed, max_gap=args.max_gap)
    ingester = GameIngester(game_pk, source, engine, bus, models)
    logging.info("replaying game %s at %sx", game_pk, args.speed)
    await ingester.run()


def _train(args: argparse.Namespace) -> None:
    from tracker.models.train import train_all

    out = args.out or get_settings().artifacts_dir
    train_all(args.data, out)
