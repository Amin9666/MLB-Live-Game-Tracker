export type Half = "top" | "bottom";

export interface Game {
  game_pk: number;
  game_date: string;
  game_type: string;
  season: number;
  status: "Preview" | "Live" | "Final" | string;
  detailed_status: string;
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
  inning: number | null;
  half: Half | null;
  is_live?: boolean;
}

export interface GameState {
  game_pk: number;
  status: string;
  detailed_status: string;
  inning: number | null;
  half: Half | null;
  outs: number;
  balls: number;
  strikes: number;
  on_1b: boolean;
  on_2b: boolean;
  on_3b: boolean;
  away_score: number;
  home_score: number;
  home_wp: number | null;
  home_team: string;
  away_team: string;
}

export interface Pitch {
  game_pk: number;
  at_bat_index: number;
  pitch_number: number;
  inning: number;
  half: Half;
  batter_id: number;
  pitcher_id: number;
  balls: number;
  strikes: number;
  outs: number;
  pitch_type: string | null;
  pitch_type_desc: string | null;
  pitch_type_pred: string | null;
  call_code: string | null;
  call_desc: string | null;
  is_in_play: boolean;
  start_speed: number | null;
  spin_rate: number | null;
  px: number | null;
  pz: number | null;
  sz_top: number | null;
  sz_bot: number | null;
  hit_x: number | null;
  hit_y: number | null;
  launch_speed: number | null;
  launch_angle: number | null;
  total_distance: number | null;
  home_wp: number | null;
  run_exp: number | null;
}

export interface PlateAppearance {
  at_bat_index: number;
  inning: number;
  half: Half;
  batter_id: number;
  batter_name: string;
  pitcher_id: number;
  pitcher_name: string;
  event_type: string | null;
  event: string | null;
  description: string | null;
  away_score: number;
  home_score: number;
  is_complete: boolean;
}

export interface Snapshot {
  stream_id: string;
  game: Game;
  state: GameState | null;
  pitches: Pitch[];
  plate_appearances: PlateAppearance[];
}

export type StreamMessage =
  | { id: string; type: "pitch" | "correction"; data: Pitch }
  | { id: string; type: "pa"; data: PlateAppearance }
  | { id: string; type: "state" | "final"; data: GameState };
