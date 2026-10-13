import type { Game, GameState } from "../types";

function Bases({ on1, on2, on3 }: { on1: boolean; on2: boolean; on3: boolean }) {
  const base = (x: number, y: number, on: boolean, label: string) => (
    <rect
      x={x}
      y={y}
      width={14}
      height={14}
      transform={`rotate(45 ${x + 7} ${y + 7})`}
      className={on ? "base on" : "base"}
      aria-label={`${label} ${on ? "occupied" : "empty"}`}
    />
  );
  return (
    <svg viewBox="0 0 60 44" className="bases" role="img" aria-label="Runners on base">
      {base(23, 4, on2, "second")}
      {base(41, 22, on1, "first")}
      {base(5, 22, on3, "third")}
    </svg>
  );
}

export function Scoreboard({ game, state }: { game: Game; state: GameState | null }) {
  const live = game.status === "Live";
  const wp = state?.home_wp ?? null;
  return (
    <section className="scoreboard">
      <div className="teams">
        <div className="team-row">
          <span className="abbr">{game.away_team}</span>
          <span className="runs">{game.away_score}</span>
        </div>
        <div className="team-row">
          <span className="abbr">{game.home_team}</span>
          <span className="runs">{game.home_score}</span>
        </div>
      </div>
      <div className="situation">
        <div className="inning">
          {live && game.inning
            ? `${game.half === "top" ? "▲" : "▼"} ${game.inning}`
            : game.detailed_status}
        </div>
        {live && state && (
          <>
            <Bases on1={state.on_1b} on2={state.on_2b} on3={state.on_3b} />
            <div className="count">
              {state.balls}-{state.strikes}
              <span className="outs" aria-label={`${state.outs} outs`}>
                {[0, 1, 2].map((i) => (
                  <i key={i} className={i < state.outs ? "out on" : "out"} />
                ))}
              </span>
            </div>
          </>
        )}
      </div>
      {wp !== null && (
        <div className="wp-bar" aria-label="Win probability">
          <div className="wp-labels">
            <span>
              {game.away_team} {((1 - wp) * 100).toFixed(1)}%
            </span>
            <span>
              {game.home_team} {(wp * 100).toFixed(1)}%
            </span>
          </div>
          <div className="wp-track">
            <div className="wp-away" style={{ width: `${(1 - wp) * 100}%` }} />
          </div>
        </div>
      )}
    </section>
  );
}
