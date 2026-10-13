export const PITCH_COLORS: Record<string, string> = {
  FF: "#d22d49",
  FA: "#d22d49",
  SI: "#fe9d00",
  FC: "#933f2c",
  SL: "#eee716",
  ST: "#ddb33a",
  SV: "#93afd4",
  CU: "#00d1ed",
  KC: "#6236cd",
  CS: "#0068ff",
  CH: "#1dbe3a",
  FS: "#3bacac",
  FO: "#55ccab",
  SC: "#60db33",
  KN: "#867a08",
  EP: "#888888",
};

export const PITCH_NAMES: Record<string, string> = {
  FF: "Four-seam",
  FA: "Fastball",
  SI: "Sinker",
  FC: "Cutter",
  SL: "Slider",
  ST: "Sweeper",
  SV: "Slurve",
  CU: "Curveball",
  KC: "Knuckle curve",
  CS: "Slow curve",
  CH: "Changeup",
  FS: "Splitter",
  FO: "Forkball",
  SC: "Screwball",
  KN: "Knuckleball",
  EP: "Eephus",
};

export function pitchColor(code: string | null | undefined): string {
  return (code && PITCH_COLORS[code]) || "var(--muted)";
}
