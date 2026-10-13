import type { Game, GameState, Pitch, PlateAppearance, Snapshot, StreamMessage } from "./types";

export interface LiveGame {
  game: Game;
  state: GameState | null;
  pitches: Pitch[];
  pas: Record<number, PlateAppearance>;
  lastId: string;
  corrections: number;
}

export type LiveAction =
  | { type: "snapshot"; snapshot: Snapshot }
  | { type: "message"; message: StreamMessage }
  | { type: "reset" };

export function parseId(id: string): [number, number] {
  const [ms, seq] = id.split("-");
  return [Number(ms), Number(seq ?? 0)];
}

export function isNewer(id: string, than: string): boolean {
  const [a, b] = parseId(id);
  const [c, d] = parseId(than);
  return a > c || (a === c && b > d);
}

function comparePitch(a: Pitch, b: Pitch): number {
  return a.at_bat_index - b.at_bat_index || a.pitch_number - b.pitch_number;
}

function upsertPitch(pitches: Pitch[], pitch: Pitch): Pitch[] {
  const i = pitches.findIndex(
    (p) => p.at_bat_index === pitch.at_bat_index && p.pitch_number === pitch.pitch_number,
  );
  if (i >= 0) {
    const next = pitches.slice();
    next[i] = pitch;
    return next;
  }
  const last = pitches[pitches.length - 1];
  if (!last || comparePitch(last, pitch) < 0) return [...pitches, pitch];
  return [...pitches, pitch].sort(comparePitch);
}

export function liveReducer(state: LiveGame | null, action: LiveAction): LiveGame | null {
  if (action.type === "reset") return null;
  if (action.type === "snapshot") {
    const { snapshot } = action;
    return {
      game: snapshot.game,
      state: snapshot.state,
      pitches: snapshot.pitches.slice().sort(comparePitch),
      pas: Object.fromEntries(snapshot.plate_appearances.map((pa) => [pa.at_bat_index, pa])),
      lastId: snapshot.stream_id,
      corrections: 0,
    };
  }
  if (!state) return state;
  const msg = action.message;
  if (!isNewer(msg.id, state.lastId)) return state;
  const next: LiveGame = { ...state, lastId: msg.id };
  switch (msg.type) {
    case "pitch":
    case "correction":
      next.pitches = upsertPitch(state.pitches, msg.data);
      if (msg.type === "correction") next.corrections = state.corrections + 1;
      break;
    case "pa":
      next.pas = { ...state.pas, [msg.data.at_bat_index]: msg.data };
      break;
    case "state":
    case "final":
      next.state = msg.data;
      next.game = {
        ...state.game,
        status: msg.data.status,
        detailed_status: msg.data.detailed_status,
        home_score: msg.data.home_score,
        away_score: msg.data.away_score,
        inning: msg.data.inning,
        half: msg.data.half,
      };
      break;
  }
  return next;
}
