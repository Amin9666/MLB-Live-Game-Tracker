from collections import defaultdict
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

BASES = ("1B", "2B", "3B")
GHOST_RUNNER = -1


class Game(BaseModel):
    game_pk: int
    game_date: date
    game_type: str
    season: int
    status: str
    detailed_status: str
    home_team: str
    away_team: str
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    inning: int | None
    half: str | None


class PlateAppearance(BaseModel):
    game_pk: int
    at_bat_index: int
    inning: int
    half: str
    batter_id: int
    batter_name: str
    pitcher_id: int
    pitcher_name: str
    bat_side: str | None
    pitch_hand: str | None
    event_type: str | None
    event: str | None
    description: str | None
    rbi: int | None
    away_score: int
    home_score: int
    is_complete: bool


class Pitch(BaseModel):
    game_pk: int
    at_bat_index: int
    pitch_number: int
    event_index: int
    play_id: str | None
    inning: int
    half: str
    batter_id: int
    pitcher_id: int
    bat_side: str | None
    pitch_hand: str | None
    balls: int
    strikes: int
    outs: int
    on_1b: bool
    on_2b: bool
    on_3b: bool
    away_score: int
    home_score: int
    pitch_type: str | None
    pitch_type_desc: str | None
    type_confidence: float | None
    call_code: str | None
    call_desc: str | None
    is_in_play: bool
    is_strike: bool
    is_ball: bool
    start_speed: float | None
    end_speed: float | None
    spin_rate: float | None
    spin_direction: float | None
    pfx_x: float | None
    pfx_z: float | None
    ivb: float | None
    hb: float | None
    px: float | None
    pz: float | None
    sz_top: float | None
    sz_bot: float | None
    x0: float | None
    z0: float | None
    extension: float | None
    plate_time: float | None
    zone: int | None
    launch_speed: float | None
    launch_angle: float | None
    total_distance: float | None
    hit_x: float | None
    hit_y: float | None
    trajectory: str | None
    start_time: datetime | None


class GameState(BaseModel):
    game_pk: int
    status: str
    detailed_status: str
    inning: int | None
    half: str | None
    outs: int
    balls: int
    strikes: int
    on_1b: bool
    on_2b: bool
    on_3b: bool
    away_score: int
    home_score: int
    batter_id: int | None
    pitcher_id: int | None
    timecode: str | None


class ParsedFeed(BaseModel):
    game: Game
    plate_appearances: list[PlateAppearance]
    pitches: list[Pitch]
    state: GameState


def parse_feed(feed: dict[str, Any]) -> ParsedFeed:
    gd = feed["gameData"]
    ld = feed["liveData"]
    game_pk = int(feed["gamePk"])
    game_type = gd["game"].get("type", "R")
    status = gd["status"]
    linescore = ld.get("linescore", {})
    line_teams = linescore.get("teams", {})
    away_score = int(line_teams.get("away", {}).get("runs") or 0)
    home_score = int(line_teams.get("home", {}).get("runs") or 0)
    inning = linescore.get("currentInning")
    half = ("top" if linescore.get("isTopInning", True) else "bottom") if inning else None
    teams = gd["teams"]
    dt = gd.get("datetime", {})
    game_date = dt.get("officialDate") or dt.get("originalDate") or dt["dateTime"][:10]

    game = Game(
        game_pk=game_pk,
        game_date=date.fromisoformat(game_date),
        game_type=game_type,
        season=int(gd["game"].get("season") or game_date[:4]),
        status=status.get("abstractGameState", "Preview"),
        detailed_status=status.get("detailedState", ""),
        home_team=teams["home"].get("abbreviation") or "",
        away_team=teams["away"].get("abbreviation") or "",
        home_team_id=int(teams["home"]["id"]),
        away_team_id=int(teams["away"]["id"]),
        home_score=home_score,
        away_score=away_score,
        inning=inning,
        half=half,
    )

    pas, pitches = _walk_plays(game_pk, game_type, ld.get("plays", {}).get("allPlays", []))

    offense = linescore.get("offense", {})
    state = GameState(
        game_pk=game_pk,
        status=game.status,
        detailed_status=game.detailed_status,
        inning=inning,
        half=half,
        outs=int(linescore.get("outs") or 0),
        balls=int(linescore.get("balls") or 0),
        strikes=int(linescore.get("strikes") or 0),
        on_1b="first" in offense,
        on_2b="second" in offense,
        on_3b="third" in offense,
        away_score=away_score,
        home_score=home_score,
        batter_id=offense.get("batter", {}).get("id"),
        pitcher_id=linescore.get("defense", {}).get("pitcher", {}).get("id"),
        timecode=feed.get("metaData", {}).get("timeStamp"),
    )
    return ParsedFeed(game=game, plate_appearances=pas, pitches=pitches, state=state)


def _walk_plays(
    game_pk: int, game_type: str, plays: list[dict[str, Any]]
) -> tuple[list[PlateAppearance], list[Pitch]]:
    pas: list[PlateAppearance] = []
    pitches: list[Pitch] = []
    bases: dict[str, int] = {}
    outs = away = home = 0
    half_key: tuple[int, str] | None = None

    for play in plays:
        about = play["about"]
        inning, half = int(about["inning"]), about["halfInning"]
        if (inning, half) != half_key:
            half_key = (inning, half)
            bases, outs = {}, 0
            if game_type == "R" and inning > 9:
                bases = {"2B": GHOST_RUNNER}

        moves: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
        for runner in play.get("runners", []):
            moves[runner["details"].get("playIndex")].append(runner)

        balls = strikes = 0
        for ev in play.get("playEvents", []):
            if ev.get("isPitch"):
                pitches.append(
                    _pitch(game_pk, play, ev, inning, half, balls, strikes, outs, bases, away, home)
                )
            count = ev.get("count")
            if count:
                balls = count.get("balls", balls)
                strikes = count.get("strikes", strikes)
            batch = moves.pop(ev.get("index"), None)
            if batch:
                outs, away, home = _apply_moves(batch, bases, outs, away, home, half)
        for batch in moves.values():
            outs, away, home = _apply_moves(batch, bases, outs, away, home, half)

        result = play.get("result", {})
        complete = bool(about.get("isComplete"))
        if complete:
            outs = int(play.get("count", {}).get("outs", outs))
            away = int(result.get("awayScore", away))
            home = int(result.get("homeScore", home))
        matchup = play["matchup"]
        pas.append(
            PlateAppearance(
                game_pk=game_pk,
                at_bat_index=int(about["atBatIndex"]),
                inning=inning,
                half=half,
                batter_id=int(matchup["batter"]["id"]),
                batter_name=matchup["batter"].get("fullName", ""),
                pitcher_id=int(matchup["pitcher"]["id"]),
                pitcher_name=matchup["pitcher"].get("fullName", ""),
                bat_side=matchup.get("batSide", {}).get("code"),
                pitch_hand=matchup.get("pitchHand", {}).get("code"),
                event_type=result.get("eventType"),
                event=result.get("event"),
                description=result.get("description"),
                rbi=result.get("rbi"),
                away_score=away,
                home_score=home,
                is_complete=complete,
            )
        )
    return pas, pitches


def _apply_moves(
    batch: list[dict[str, Any]],
    bases: dict[str, int],
    outs: int,
    away: int,
    home: int,
    half: str,
) -> tuple[int, int, int]:
    last_end: dict[int, str | None] = {}
    for runner in batch:
        mv = runner["movement"]
        rid = int(runner["details"]["runner"]["id"])
        if rid not in last_end:
            _remove_runner(bases, rid, mv.get("start"))
        if mv.get("isOut"):
            outs += 1
            last_end[rid] = None
        elif mv.get("end") == "score":
            if half == "top":
                away += 1
            else:
                home += 1
            last_end[rid] = None
        else:
            last_end[rid] = mv.get("end")
    for rid, end in last_end.items():
        if end in BASES:
            bases[end] = rid
    return min(outs, 3), away, home


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _remove_runner(bases: dict[str, int], rid: int, start: str | None) -> None:
    for base, occupant in list(bases.items()):
        if occupant == rid:
            del bases[base]
            return
    if start in bases:
        del bases[start]


def _pitch(
    game_pk: int,
    play: dict[str, Any],
    ev: dict[str, Any],
    inning: int,
    half: str,
    balls: int,
    strikes: int,
    outs: int,
    bases: dict[str, int],
    away: int,
    home: int,
) -> Pitch:
    d = ev.get("details", {})
    pd = ev.get("pitchData", {})
    coords = pd.get("coordinates", {})
    breaks = pd.get("breaks", {})
    hit = ev.get("hitData", {})
    hit_coords = hit.get("coordinates", {})
    matchup = play["matchup"]
    start_time = ev.get("startTime")
    return Pitch(
        game_pk=game_pk,
        at_bat_index=int(play["about"]["atBatIndex"]),
        pitch_number=int(ev["pitchNumber"]),
        event_index=int(ev["index"]),
        play_id=ev.get("playId"),
        inning=inning,
        half=half,
        batter_id=int(matchup["batter"]["id"]),
        pitcher_id=int(matchup["pitcher"]["id"]),
        bat_side=matchup.get("batSide", {}).get("code"),
        pitch_hand=matchup.get("pitchHand", {}).get("code"),
        balls=min(balls, 3),
        strikes=min(strikes, 2),
        outs=min(outs, 2),
        on_1b="1B" in bases,
        on_2b="2B" in bases,
        on_3b="3B" in bases,
        away_score=away,
        home_score=home,
        pitch_type=d.get("type", {}).get("code"),
        pitch_type_desc=d.get("type", {}).get("description"),
        type_confidence=pd.get("typeConfidence"),
        call_code=d.get("call", {}).get("code"),
        call_desc=d.get("call", {}).get("description"),
        is_in_play=bool(d.get("isInPlay")),
        is_strike=bool(d.get("isStrike")),
        is_ball=bool(d.get("isBall")),
        start_speed=pd.get("startSpeed"),
        end_speed=pd.get("endSpeed"),
        spin_rate=breaks.get("spinRate"),
        spin_direction=breaks.get("spinDirection"),
        pfx_x=coords.get("pfxX"),
        pfx_z=coords.get("pfxZ"),
        ivb=breaks.get("breakVerticalInduced"),
        hb=breaks.get("breakHorizontal"),
        px=coords.get("pX"),
        pz=coords.get("pZ"),
        sz_top=_round(pd.get("strikeZoneTop"), 2),
        sz_bot=_round(pd.get("strikeZoneBottom"), 2),
        x0=coords.get("x0"),
        z0=coords.get("z0"),
        extension=pd.get("extension"),
        plate_time=pd.get("plateTime"),
        zone=pd.get("zone"),
        launch_speed=hit.get("launchSpeed"),
        launch_angle=hit.get("launchAngle"),
        total_distance=hit.get("totalDistance"),
        hit_x=hit_coords.get("coordX"),
        hit_y=hit_coords.get("coordY"),
        trajectory=hit.get("trajectory"),
        start_time=datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if start_time
        else None,
    )
