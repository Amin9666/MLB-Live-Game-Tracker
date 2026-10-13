import { describe, expect, it } from "vitest";
import { isNewer, liveReducer, type LiveGame } from "./live";
import type { Game, GameState, Pitch, Snapshot } from "./types";

const game: Game = {
  game_pk: 1,
  game_date: "2026-10-11",
  game_type: "L",
  season: 2026,
  status: "Live",
  detailed_status: "In Progress",
  home_team: "MIL",
  away_team: "LAD",
  home_score: 0,
  away_score: 0,
  inning: 1,
  half: "top",
};

function pitch(ab: number, n: number, extra: Partial<Pitch> = {}): Pitch {
  return {
    game_pk: 1,
    at_bat_index: ab,
    pitch_number: n,
    inning: 1,
    half: "top",
    batter_id: 10,
    pitcher_id: 20,
    balls: 0,
    strikes: 0,
    outs: 0,
    pitch_type: "FF",
    pitch_type_desc: "Four-Seam Fastball",
    pitch_type_pred: "FF",
    call_code: "B",
    call_desc: "Ball",
    is_in_play: false,
    start_speed: 97,
    spin_rate: 2300,
    px: 0,
    pz: 2.5,
    sz_top: 3.4,
    sz_bot: 1.6,
    hit_x: null,
    hit_y: null,
    launch_speed: null,
    launch_angle: null,
    total_distance: null,
    home_wp: 0.5,
    run_exp: 0.5,
    ...extra,
  };
}

function snapshot(pitches: Pitch[] = []): LiveGame {
  const snap: Snapshot = { stream_id: "100-0", game, state: null, pitches, plate_appearances: [] };
  return liveReducer(null, { type: "snapshot", snapshot: snap })!;
}

describe("isNewer", () => {
  it("compares millisecond and sequence parts numerically", () => {
    expect(isNewer("100-1", "100-0")).toBe(true);
    expect(isNewer("99-5", "100-0")).toBe(false);
    expect(isNewer("1000-0", "999-9")).toBe(true);
    expect(isNewer("100-0", "100-0")).toBe(false);
  });
});

describe("liveReducer", () => {
  it("ignores events at or before the snapshot cursor", () => {
    const s = snapshot([pitch(0, 1)]);
    const next = liveReducer(s, { type: "message", message: { id: "100-0", type: "pitch", data: pitch(0, 2) } });
    expect(next).toBe(s);
  });

  it("appends new pitches in order and advances the cursor", () => {
    let s = snapshot([pitch(0, 1)]);
    s = liveReducer(s, { type: "message", message: { id: "101-0", type: "pitch", data: pitch(1, 1) } })!;
    s = liveReducer(s, { type: "message", message: { id: "102-0", type: "pitch", data: pitch(0, 2) } })!;
    expect(s.pitches.map((p) => [p.at_bat_index, p.pitch_number])).toEqual([
      [0, 1],
      [0, 2],
      [1, 1],
    ]);
    expect(s.lastId).toBe("102-0");
  });

  it("replaces corrected pitches instead of duplicating them", () => {
    let s = snapshot([pitch(0, 1)]);
    s = liveReducer(s, {
      type: "message",
      message: { id: "101-0", type: "correction", data: pitch(0, 1, { hit_x: 100, hit_y: 80 }) },
    })!;
    expect(s.pitches).toHaveLength(1);
    expect(s.pitches[0].hit_x).toBe(100);
    expect(s.corrections).toBe(1);
  });

  it("applies state events to the game header", () => {
    const state: GameState = {
      game_pk: 1,
      status: "Final",
      detailed_status: "Final",
      inning: 9,
      half: "top",
      outs: 3,
      balls: 0,
      strikes: 0,
      on_1b: false,
      on_2b: false,
      on_3b: false,
      away_score: 1,
      home_score: 2,
      home_wp: 1,
      home_team: "MIL",
      away_team: "LAD",
    };
    const s = liveReducer(snapshot(), { type: "message", message: { id: "101-0", type: "final", data: state } })!;
    expect(s.game.status).toBe("Final");
    expect([s.game.away_score, s.game.home_score]).toEqual([1, 2]);
    expect(s.state?.home_wp).toBe(1);
  });
});
