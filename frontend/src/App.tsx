import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { PitchLog } from "./components/PitchLog";
import { Scoreboard } from "./components/Scoreboard";
import { SprayChart } from "./components/SprayChart";
import { StrikeZone } from "./components/StrikeZone";
import { WinProbChart } from "./components/WinProbChart";
import { PITCH_COLORS, PITCH_NAMES } from "./pitchTypes";
import type { Game } from "./types";
import { useLiveGame, type SocketStatus } from "./useLiveGame";

type ZoneView = "pa" | "pitcher" | "game";

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json() as Promise<T>;
}

function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

function readHash(): { date: string | null; game: number | null } {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const game = params.get("game");
  return { date: params.get("date"), game: game ? Number(game) : null };
}

export function App() {
  const initial = readHash();
  const [date, setDate] = useState<string | null>(initial.date);
  const [gamePk, setGamePk] = useState<number | null>(initial.game);

  const latest = useQuery({
    queryKey: ["latest-date"],
    queryFn: () => getJson<{ date: string | null }>("/api/games/latest-date"),
    enabled: date === null,
  });
  const today = todayET();
  const games = useQuery({
    queryKey: ["games", date ?? today],
    queryFn: () => getJson<Game[]>(`/api/games?date=${date ?? today}`),
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (date === null && games.data && games.data.length === 0 && latest.data?.date) {
      setDate(latest.data.date);
    }
  }, [date, games.data, latest.data]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (gamePk) params.set("game", String(gamePk));
    window.history.replaceState(null, "", `#${params.toString()}`);
  }, [date, gamePk]);

  useEffect(() => {
    if (gamePk === null && games.data?.length) {
      const live = games.data.find((g) => g.is_live);
      setGamePk((live ?? games.data[0]).game_pk);
    }
  }, [games.data, gamePk]);

  return (
    <div className="app">
      <header className="topbar">
        <h1>MLB Live Tracker</h1>
        <input
          type="date"
          aria-label="Game date"
          value={date ?? today}
          onChange={(e) => {
            setDate(e.target.value);
            setGamePk(null);
          }}
        />
      </header>
      <nav className="game-strip" aria-label="Games">
        {games.isLoading && <span className="muted">Loading games…</span>}
        {games.data?.length === 0 && <span className="muted">No games stored for this date.</span>}
        {games.data?.map((g) => (
          <button
            key={g.game_pk}
            className={g.game_pk === gamePk ? "game-chip active" : "game-chip"}
            onClick={() => setGamePk(g.game_pk)}
          >
            {g.is_live && <span className="live-dot" aria-label="live" />}
            <span>
              {g.away_team} {g.away_score}
            </span>
            <span>
              {g.home_team} {g.home_score}
            </span>
            <span className="muted small">{g.status === "Live" ? `${g.half === "top" ? "▲" : "▼"}${g.inning}` : g.detailed_status}</span>
          </button>
        ))}
      </nav>
      {gamePk !== null && <GameView gamePk={gamePk} />}
    </div>
  );
}

const STATUS_TEXT: Record<SocketStatus, string> = {
  loading: "Loading…",
  connecting: "Connecting…",
  open: "Live stream connected",
  reconnecting: "Reconnecting…",
  error: "Could not load game",
};

function GameView({ gamePk }: { gamePk: number }) {
  const { live, status } = useLiveGame(gamePk);
  const [zoneView, setZoneView] = useState<ZoneView>("pa");

  const latestPitch = live?.pitches[live.pitches.length - 1] ?? null;
  const zonePitches = useMemo(() => {
    if (!live || !latestPitch) return [];
    if (zoneView === "pa") return live.pitches.filter((p) => p.at_bat_index === latestPitch.at_bat_index);
    if (zoneView === "pitcher") return live.pitches.filter((p) => p.pitcher_id === latestPitch.pitcher_id);
    return live.pitches;
  }, [live, latestPitch, zoneView]);
  const usedTypes = useMemo(
    () => Array.from(new Set(zonePitches.map((p) => p.pitch_type).filter(Boolean))) as string[],
    [zonePitches],
  );

  if (!live) return <p className="muted">{STATUS_TEXT[status]}</p>;
  const pa = latestPitch ? live.pas[latestPitch.at_bat_index] : undefined;

  return (
    <main className="game-view">
      <div className="status-line">
        <span className={`conn ${status}`}>{STATUS_TEXT[status]}</span>
        <span className="muted small">
          {live.pitches.length} pitches · {live.corrections} live corrections
        </span>
      </div>
      <Scoreboard game={live.game} state={live.state} />
      <section className="card wide">
        <h2>Win probability</h2>
        <WinProbChart
          pitches={live.pitches}
          currentWp={live.state?.home_wp ?? null}
          homeTeam={live.game.home_team}
          awayTeam={live.game.away_team}
        />
      </section>
      <div className="grid">
        <section className="card">
          <div className="card-head">
            <h2>Strike zone</h2>
            <div className="segmented" role="group" aria-label="Strike zone filter">
              {(["pa", "pitcher", "game"] as ZoneView[]).map((v) => (
                <button key={v} className={zoneView === v ? "active" : ""} onClick={() => setZoneView(v)}>
                  {v === "pa" ? "At-bat" : v === "pitcher" ? "Pitcher" : "Game"}
                </button>
              ))}
            </div>
          </div>
          {pa && (
            <p className="muted small">
              {pa.pitcher_name} vs {pa.batter_name}
              {pa.is_complete && pa.event ? ` — ${pa.event}` : ""}
            </p>
          )}
          <StrikeZone pitches={zonePitches} highlight={latestPitch} numbered={zoneView === "pa"} />
          <ul className="legend">
            {usedTypes.map((t) => (
              <li key={t}>
                <span className="chip" style={{ background: PITCH_COLORS[t] ?? "var(--muted)" }} />
                {PITCH_NAMES[t] ?? t}
              </li>
            ))}
          </ul>
          <p className="muted small">Catcher's view</p>
        </section>
        <section className="card">
          <h2>Spray chart</h2>
          <SprayChart
            pitches={live.pitches}
            pas={live.pas}
            awayTeam={live.game.away_team}
            homeTeam={live.game.home_team}
          />
          <ul className="legend">
            <li>
              <span className="chip ring top" /> {live.game.away_team}
            </li>
            <li>
              <span className="chip ring bottom" /> {live.game.home_team}
            </li>
            <li>
              <span className="chip solid" /> hit
            </li>
            <li>
              <span className="chip hollow" /> out
            </li>
          </ul>
        </section>
      </div>
      <section className="card wide">
        <h2>Pitch log</h2>
        <PitchLog pitches={live.pitches} pas={live.pas} />
      </section>
    </main>
  );
}
