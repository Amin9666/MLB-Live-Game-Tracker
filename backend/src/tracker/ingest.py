import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

from tracker import db, frames
from tracker.mlb_client import MlbClient
from tracker.models.runtime import Models
from tracker.parse import ParsedFeed, parse_feed
from tracker.stream import EventBus

log = logging.getLogger(__name__)

DERIVED = {"home_wp", "pitch_type_pred", "run_exp", "content_hash"}
UNHASHED = DERIVED | {"start_time"}
TRACKING_FIELDS = (
    "play_id",
    "pitch_type",
    "pitch_type_desc",
    "type_confidence",
    "start_speed",
    "end_speed",
    "spin_rate",
    "spin_direction",
    "pfx_x",
    "pfx_z",
    "ivb",
    "hb",
    "px",
    "pz",
    "sz_top",
    "sz_bot",
    "x0",
    "z0",
    "extension",
    "plate_time",
    "zone",
    "launch_speed",
    "launch_angle",
    "total_distance",
    "hit_x",
    "hit_y",
    "trajectory",
)


class FeedSource(Protocol):
    async def fetch(self, game_pk: int) -> dict[str, Any] | None: ...

    def next_delay(self, wait_hint: float) -> float: ...


class HttpFeedSource:
    def __init__(
        self,
        client: MlbClient,
        floor: float = 2.0,
        stale_after: float = 45.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.floor = floor
        self.stale_after = stale_after
        self.clock = clock
        self._seen: dict[int, tuple[str | None, float]] = {}
        self.cache_bypasses = 0

    async def fetch(self, game_pk: int) -> dict[str, Any] | None:
        feed = await self.client.live_feed(game_pk)
        timecode = frames.timecode_of(feed)
        now = self.clock()
        last_tc, since = self._seen.get(game_pk, (None, now))
        if timecode != last_tc:
            self._seen[game_pk] = (timecode, now)
            return feed
        status = feed.get("gameData", {}).get("status", {}).get("abstractGameState")
        if status != "Live" or now - since < self.stale_after:
            return feed
        latest = max(await self.client.timestamps(game_pk), default=None)
        if latest and (timecode is None or latest > timecode):
            log.warning(
                "game %s: feed stuck at %s for %.0fs; fetching timecode %s directly",
                game_pk,
                timecode,
                now - since,
                latest,
            )
            self.cache_bypasses += 1
            feed = await self.client.live_feed(game_pk, latest)
            self._seen[game_pk] = (frames.timecode_of(feed), now)
        else:
            self._seen[game_pk] = (timecode, now)
        return feed

    def next_delay(self, wait_hint: float) -> float:
        return max(wait_hint, self.floor)


class ReplayFeedSource:
    def __init__(
        self,
        path: Path,
        speed: float = 30.0,
        max_gap: float = 2.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._frames = frames.read_recording(path)
        self.speed = speed
        self.max_gap = max_gap
        self._sleep = sleep
        self._last_tc: datetime | None = None

    async def fetch(self, game_pk: int) -> dict[str, Any] | None:
        try:
            tc, frame = next(self._frames)
        except StopIteration:
            return None
        ts = frames.parse_timecode(tc)
        if self._last_tc is not None and self.speed > 0:
            gap = (ts - self._last_tc).total_seconds() / self.speed
            await self._sleep(min(gap, self.max_gap))
        self._last_tc = ts
        return frame

    def next_delay(self, wait_hint: float) -> float:
        return 0.0


def keep_tracking(prev: dict[str, Any], row: dict[str, Any]) -> bool:
    kept = False
    for name in TRACKING_FIELDS:
        if row[name] is None and prev.get(name) is not None:
            row[name] = prev[name]
            kept = True
    return kept


def content_hash(row: dict[str, Any]) -> str:
    payload = {k: v for k, v in row.items() if k not in UNHASHED}
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:16]


@dataclass
class StepResult:
    parsed: ParsedFeed
    new_pitches: int = 0
    corrections: int = 0
    pa_updates: int = 0
    regressions_ignored: int = 0
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


class GameIngester:
    def __init__(
        self,
        game_pk: int,
        source: FeedSource,
        engine: AsyncEngine,
        bus: EventBus,
        models: Models,
        full_frame_every: int = 100,
        final_grace_polls: int = 10,
    ) -> None:
        self.game_pk = game_pk
        self.source = source
        self.engine = engine
        self.bus = bus
        self.models = models
        self.full_frame_every = full_frame_every
        self.final_grace_polls = final_grace_polls
        self._pitch_rows: dict[tuple[int, int], dict[str, Any]] | None = None
        self._pa_hashes: dict[int, str] = {}
        self._last_state: dict[str, Any] | None = None
        self._last_frame: dict[str, Any] | None = None
        self._next_seq = 0
        self._frames_since_full = 0
        self._final_polls = 0
        self._final_emitted = False
        self._high_water: str | None = None
        self.stale_frames = 0
        self.finished = False

    async def _load_seen(self) -> None:
        async with self.engine.connect() as conn:
            self._pitch_rows = await db.pitch_rows(conn, self.game_pk)
            self._pa_hashes = await db.pa_hashes(conn, self.game_pk)
            self._high_water = await db.latest_timecode(conn, self.game_pk)
            self._next_seq = await db.next_raw_seq(conn, self.game_pk)

    async def step(self) -> tuple[StepResult | None, float]:
        if self._pitch_rows is None:
            await self._load_seen()
        assert self._pitch_rows is not None

        feed = await self.source.fetch(self.game_pk)
        if feed is None:
            self.finished = True
            return None, 0.0
        frame = frames.trim(feed)
        wait = float(frame["metaData"].get("wait") or 10)
        timecode = frames.timecode_of(frame)
        if timecode and self._high_water and timecode < self._high_water:
            self.stale_frames += 1
            if self.stale_frames % 20 == 1:
                log.warning(
                    "game %s: skipping frame %s older than %s (%d skipped)",
                    self.game_pk,
                    timecode,
                    self._high_water,
                    self.stale_frames,
                )
            return None, self.source.next_delay(wait)
        parsed = parse_feed(frame)
        result = StepResult(parsed=parsed)

        pitch_rows: list[dict[str, Any]] = []
        kinds: list[str] = []
        for pitch in parsed.pitches:
            row = pitch.model_dump()
            key = (pitch.at_bat_index, pitch.pitch_number)
            prev = self._pitch_rows.get(key)
            if prev is not None and keep_tracking(prev, row):
                result.regressions_ignored += 1
            row["content_hash"] = content_hash(row)
            if prev is not None and prev["content_hash"] == row["content_hash"]:
                continue
            pitch_rows.append(row)
            kinds.append("pitch" if prev is None else "correction")
        self.models.enrich_pitches(pitch_rows)

        pa_rows: list[dict[str, Any]] = []
        for pa in parsed.plate_appearances:
            row = pa.model_dump()
            row["content_hash"] = content_hash(row)
            if self._pa_hashes.get(pa.at_bat_index) != row["content_hash"]:
                pa_rows.append(row)

        state = parsed.state.model_dump()
        state["home_wp"] = self.models.state_wp(parsed.state)
        state["home_team"] = parsed.game.home_team
        state["away_team"] = parsed.game.away_team
        comparable = {k: v for k, v in state.items() if k != "timecode"}
        state_changed = comparable != self._last_state

        async with self.engine.begin() as conn:
            await db.upsert_game(conn, parsed.game.model_dump())
            await db.upsert_plate_appearances(conn, pa_rows)
            await db.upsert_pitches(conn, pitch_rows)
            await self._store_raw(conn, frame)

        for kind, row in zip(kinds, pitch_rows, strict=True):
            result.events.append((kind, _jsonable(row)))
        for row in pa_rows:
            if row["is_complete"]:
                result.events.append(("pa", _jsonable(row)))
        if state_changed:
            result.events.append(("state", _jsonable(state)))
        final = parsed.game.status == "Final"
        if final and not self._final_emitted:
            result.events.append(("final", _jsonable(state)))

        await self.bus.publish_many(self.game_pk, result.events)
        await self.bus.set_state(self.game_pk, _jsonable(state), live=not final)

        for row in pitch_rows:
            self._pitch_rows[(row["at_bat_index"], row["pitch_number"])] = row
        for row in pa_rows:
            self._pa_hashes[row["at_bat_index"]] = row["content_hash"]
        self._last_state = comparable
        if timecode and (self._high_water is None or timecode > self._high_water):
            if timecode <= _timecode_now(FUTURE_TOLERANCE):
                self._high_water = timecode
            else:
                log.warning("game %s: frame stamped in the future (%s)", self.game_pk, timecode)
        result.new_pitches = kinds.count("pitch")
        result.corrections = kinds.count("correction")
        result.pa_updates = len(pa_rows)
        if final:
            self._final_emitted = True
            self._final_polls += 1
            self.finished = self._final_polls > self.final_grace_polls
            return result, self.source.next_delay(max(wait, 60.0))
        return result, self.source.next_delay(wait)

    async def _store_raw(self, conn: Any, frame: dict[str, Any]) -> None:
        timecode = frames.timecode_of(frame) or ""
        if frame == self._last_frame:
            return
        if self._last_frame is None or self._frames_since_full >= self.full_frame_every:
            kind, body = "full", cast(Any, frame)
            self._frames_since_full = 0
        else:
            kind, body = "patch", frames.diff(self._last_frame, frame)
            self._frames_since_full += 1
        await db.insert_raw(conn, self.game_pk, self._next_seq, timecode, kind, body)
        self._next_seq += 1
        self._last_frame = frame

    async def run(self) -> None:
        failures = 0
        while not self.finished:
            try:
                result, delay = await self.step()
                failures = 0
                if result and (
                    result.new_pitches or result.corrections or result.regressions_ignored
                ):
                    log.info(
                        "game %s: +%d pitches, %d corrections, %d null regressions ignored",
                        self.game_pk,
                        result.new_pitches,
                        result.corrections,
                        result.regressions_ignored,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                delay = min(5.0 * 2**failures, 120.0)
                log.exception("game %s step failed; retrying in %.0fs", self.game_pk, delay)
            if not self.finished and delay > 0:
                await asyncio.sleep(delay)
        log.info("game %s finished", self.game_pk)


FUTURE_TOLERANCE = timedelta(minutes=2)


def _timecode_now(ahead: timedelta = timedelta(0)) -> str:
    return (datetime.now(UTC) + ahead).strftime("%Y%m%d_%H%M%S")


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(row, default=str))
    return out


ET = ZoneInfo("America/New_York")


async def run_live(
    client: MlbClient,
    engine: AsyncEngine,
    bus: EventBus,
    models: Models,
    interval: float = 60.0,
    floor: float = 2.0,
    lead: timedelta = timedelta(minutes=15),
) -> None:
    tasks: dict[int, asyncio.Task[None]] = {}
    source = HttpFeedSource(client, floor=floor)
    while True:
        try:
            today = datetime.now(ET).date()
            games = await client.schedule(today - timedelta(days=1), today)
            now = datetime.now(UTC)
            for g in games:
                pk = int(g["gamePk"])
                if pk in tasks and not tasks[pk].done():
                    continue
                state = g["status"]["abstractGameState"]
                start = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00"))
                if state == "Live" or (state == "Preview" and start - now <= lead):
                    log.info("starting ingester for game %s (%s)", pk, state)
                    ingester = GameIngester(pk, source, engine, bus, models)
                    tasks[pk] = asyncio.create_task(ingester.run(), name=f"game-{pk}")
            for pk in [pk for pk, t in tasks.items() if t.done()]:
                tasks.pop(pk)
        except asyncio.CancelledError:
            for t in tasks.values():
                t.cancel()
            raise
        except Exception:
            log.exception("schedule poll failed")
        await asyncio.sleep(interval)
