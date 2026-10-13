# MLB Live Tracker

Real-time, pitch-by-pitch MLB game tracker built on the MLB Stats API live feed (`statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live`).

- **Ingestion:** an async Python pipeline polls live games and diffs every frame.
- **Storage and streaming:** new pitches and scoring corrections are written to Postgres and published to Redis Streams.
- **API:** FastAPI serves WebSocket and REST endpoints.
- **Frontend:** a React/TypeScript app draws a live strike zone, spray chart and win probability graph with D3.
- **Models:** each pitch is scored by a gradient-boosted pitch-type classifier and a win probability model, and gets a run-expectancy value. All three were trained on two full regular seasons.

```
                 ┌──────────────────────────────┐
 statsapi.mlb.com│  ingester (asyncio, httpx)   │
 ───────────────▶│  poll → trim → parse → diff  │
  feed/live,     │  sticky tracking fields      │
  timestamps     │  high-water timecode guard   │
                 │  model enrichment            │
                 └──────┬───────────────┬───────┘
          upsert on hash│               │ XADD game:{pk}:events
                        ▼               ▼
                 ┌────────────┐   ┌────────────┐
                 │  Postgres  │   │   Redis    │
                 │ games, PAs,│   │  Streams + │
                 │ pitches,   │   │ live state │
                 │ raw_events │   └─────┬──────┘
                 └─────┬──────┘         │ XREAD (one reader per game, fan-out)
                       │                ▼
                       │        ┌──────────────────┐   WS /ws/games/{pk}?last_id=
                       └───────▶│  FastAPI         │──────────────────────────▶ React + D3
                        REST    │  REST · WS · SSE │   REST /api/...
                                └──────────────────┘
```

## Numbers

| | |
|---|---|
| Training data | 4,859 regular-season games (2025–2026), 1,424,567 pitches |
| Ingest step (parse, diff, enrich, persist, publish) | 31 ms p50, 67 ms p95 per feed frame |
| Redis to WebSocket client | 2.5 ms p50, 3.2 ms p95 on a replayed game; 2.9 ms p50 on a live postseason game (local) |
| Recorded game fixture | 458 frames of a full postseason game in 160 KB (JSON Patch deltas) |
| Tests | 43 backend (pytest, real Postgres and Redis) and 5 frontend (vitest) |

## Models

All three are evaluated by training on 2025 and testing on 2026. The shipped artifacts are then refit on both seasons. Full output is in [`backend/artifacts/metrics.json`](backend/artifacts/metrics.json).

| Model | What | Held-out result |
|---|---|---|
| Run expectancy | Empirical RE24 and count-aware RE288 tables (runs from each base/out/count state to the end of the inning) | Bases empty, 0 out: 0.50 runs. Loaded, 0 out: 2.50. Bases empty, 2 out: 0.11. These match published values. |
| Win probability | `HistGradientBoostingClassifier` on inning, half, score differential (monotonic constraint), outs, base state and count | Log loss 0.475, Brier 0.160, AUC 0.846 on 714k pitches. Calibrated within about 3 points per decile, slightly overconfident toward the home team. |
| Pitch type | `HistGradientBoostingClassifier` on velocity, spin rate and axis, movement, release point and extension, with left-handed pitchers mirrored | 85.5% accuracy, macro-F1 0.66. Four-seam 96.5%, sinker 91%, changeup 87%. Weak on rare pitches (slurve 22%, knuckle curve 48%) because it has no per-pitcher context. |

## Problems found in the real feed

Each of these came up while ingesting live games. Each one has a regression test.

- **Late hit data.** Batted-ball coordinates and trajectory arrive in a later frame than the pitch itself. Content hashing per `(game_pk, at_bat_index, pitch_number)` turns these into `correction` events: 38 in the recorded fixture game.
- **Degraded frames.** During a live game the feed briefly served a version with all pitch tracking data stripped out (velocity, spin, location, `playId`), then restored it. That produced about 200 bogus corrections in each direction. Tracking fields are now sticky: a non-null value is never replaced by null, and fields that only differ by rounding are normalized.
- **Future-stamped frames.** One frame arrived stamped 14 minutes ahead of real time. Because the ingester skips frames older than the newest timecode it has seen, every real frame for the next 14 minutes was skipped. Timestamps more than 2 minutes in the future no longer move that mark forward.
- **Stuck CDN cache.** The feed is served with `stale-if-error=86400`. If the feed timestamp stops advancing for 45 s while a game is live, the source asks `/feed/live/timestamps` for the newest timecode and fetches that directly.
- **Silent WebSocket stalls.** The per-game stream reader used a blocking `XREAD` with a 15 s block, but redis-py's default socket timeout is shorter. During the first quiet stretch of a live game, the read timed out, the reader task died, and every subscriber stopped receiving events with no error. Blocking reads now use a dedicated client whose timeout is longer than the block, and the reader logs and retries any Redis error instead of exiting.
- **Restarts and replays.** On startup, each game ingester loads its existing rows from Postgres. Re-ingesting a game, or restarting halfway through, produces no duplicate events. Raw frames are stored as a full snapshot every N frames plus JSON Patch deltas, so any game can be re-parsed after a parser fix.

## Running it

```bash
docker compose up -d --build        # postgres, redis, migrations, api (serves the UI) and live ingester
open http://localhost:8000
```

To work on the code locally:

```bash
docker compose up -d postgres redis
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn tracker.api:app --reload          # API on :8000
uv run tracker ingest                            # poll today's live games

cd ../frontend && npm install && npm run dev     # UI on :5173, proxies /api and /ws to :8000
```

To replay a recorded game through the full pipeline (handy when no games are on):

```bash
uv run tracker replay tests/fixtures/849809.jsonl.gz --fresh --speed 60
```

To rebuild the data and models from scratch:

```bash
uv run tracker backfill 2025 2026 --out data     # about 5 min, 213 MB of parquet
uv run tracker train --data data                 # writes artifacts/ and metrics.json
uv run tracker load --data data --season 2026    # optional: load history into Postgres for /api/players queries
uv run tracker record 849809                     # record every timecode of a game as a test fixture
```

## API

| Endpoint | |
|---|---|
| `GET /api/games?date=YYYY-MM-DD` | Games on a date, with a live flag |
| `GET /api/games/{pk}/snapshot` | Game, current state, all pitches and plate appearances, plus the stream cursor to resume from |
| `GET /api/games/{pk}/winprob` | Home win probability before every pitch |
| `GET /api/players/{id}/pitches?role=pitcher&season=2026&balls=0&strikes=2&pitch_type=SL` | Historical pitch query |
| `WS /ws/games/{pk}?last_id=<stream id or $>` | Replays every event after `last_id`, then pushes live events (`pitch`, `correction`, `pa`, `state`, `final`) |
| `GET /sse/games/{pk}` | The same stream as Server-Sent Events (supports `Last-Event-ID`) |
| `GET /healthz` | Checks Postgres and Redis |

The WebSocket server runs one Redis reader per game and fans events out to every subscriber. A client that falls too far behind is disconnected with code 1013 and reconnects using its last stream id. Since the database holds the state and Redis holds the event log, the API can run as several replicas without sticky sessions.

## CI/CD and deployment

The GitHub Actions workflow in `.github/workflows/ci.yml` runs these jobs on every push and pull request:

- **Backend checks:** ruff, mypy, and an Alembic migration against a fresh Postgres.
- **Backend tests:** pytest against Postgres and Redis service containers.
- **Frontend:** typecheck, vitest and production build.
- **Image:** a Docker build, pushed to GHCR when `main` changes.

The app deploys to Render's free tier using the blueprint in [`render.yaml`](render.yaml):

- **One Docker web service.** It runs migrations on boot, serves the UI and API, and runs the ingester in the same process (`RUN_INGESTER=true`), because Render has no free background workers.
- **Free Postgres and Key Value (Redis).**
- **Deploys gated on CI.** With `autoDeployTrigger: checksPass`, Render deploys a commit to `main` only after the CI checks pass.
- **Health check restarts.** `/healthz` returns 503 if the embedded ingester task has died, so Render restarts the service.

Free-tier limits and how this setup handles them:

| Limit | Handling |
|---|---|
| The web service sleeps after 15 min without traffic | `.github/workflows/keepalive.yml` pings `/healthz` every 10 min. Set the repo variable `APP_URL` to enable it. |
| 512 MB RAM | API and ingester together use about 170 MB at idle. |
| 25 MB Key Value | Streams are capped at 3,000 events per game, and a finished game's stream expires after 6 h. |
| Free Postgres expires after 30 days | Recreate it, or point `DATABASE_URL` at a free Neon database. |

Locally, `docker-compose.yml` runs the API and ingester as separate services, which is how it would run on a paid tier.

## Layout

```
backend/src/tracker/
  mlb_client.py   async Stats API client: retries, backoff, Retry-After
  frames.py       feed trimming, JSON Patch recording and rebuild
  parse.py        feed → games / plate appearances / pitches, with base-out state tracked per pitch
  ingest.py       per-game ingester, live scheduler, HTTP/replay sources
  stream.py       Redis Streams event bus
  hub.py          per-game stream reader and WebSocket fan-out
  api.py          FastAPI app
  models/         features, training, runtime inference
frontend/src/
  live.ts         event reducer (ordering, dedupe, corrections)
  useLiveGame.ts  snapshot, then WebSocket with resume-from-cursor reconnects
  components/     StrikeZone, SprayChart, WinProbChart (D3), Scoreboard, PitchLog
```
