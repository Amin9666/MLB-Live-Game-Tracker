import { mean } from "d3-array";
import { scaleLinear } from "d3-scale";
import { pitchColor } from "../pitchTypes";
import type { Pitch } from "../types";

const W = 300;
const H = 340;
const PLATE_HALF = 17 / 24;
const BALL_RADIUS_FT = 0.121;

interface Props {
  pitches: Pitch[];
  highlight?: Pitch | null;
  numbered?: boolean;
}

export function StrikeZone({ pitches, highlight, numbered }: Props) {
  const x = scaleLinear().domain([-2, 2]).range([0, W]).clamp(true);
  const y = scaleLinear().domain([0, 4.8]).range([H - 30, 0]).clamp(true);
  const located = pitches.filter((p) => p.px !== null && p.pz !== null);
  const top = mean(located, (p) => p.sz_top ?? undefined) ?? 3.4;
  const bot = mean(located, (p) => p.sz_bot ?? undefined) ?? 1.6;
  const r = x(BALL_RADIUS_FT) - x(0);
  const thirdW = (x(PLATE_HALF) - x(-PLATE_HALF)) / 3;
  const thirdH = (y(bot) - y(top)) / 3;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="chart strike-zone"
      role="img"
      aria-label="Strike zone, catcher's view"
    >
      {[1, 2].map((i) => (
        <g key={i} className="zone-grid">
          <line x1={x(-PLATE_HALF) + thirdW * i} x2={x(-PLATE_HALF) + thirdW * i} y1={y(top)} y2={y(bot)} />
          <line x1={x(-PLATE_HALF)} x2={x(PLATE_HALF)} y1={y(top) + thirdH * i} y2={y(top) + thirdH * i} />
        </g>
      ))}
      <rect
        className="zone"
        x={x(-PLATE_HALF)}
        y={y(top)}
        width={x(PLATE_HALF) - x(-PLATE_HALF)}
        height={y(bot) - y(top)}
      />
      <path
        className="plate"
        d={`M${x(-PLATE_HALF)},${H - 20} h${x(PLATE_HALF) - x(-PLATE_HALF)} l-12,8 l${-(x(PLATE_HALF) - x(-PLATE_HALF) - 24) / 2},8 l${-(x(PLATE_HALF) - x(-PLATE_HALF) - 24) / 2},-8 z`}
      />
      {located.map((p, i) => {
        const isHi =
          highlight &&
          highlight.at_bat_index === p.at_bat_index &&
          highlight.pitch_number === p.pitch_number;
        return (
          <g key={`${p.at_bat_index}-${p.pitch_number}`} className={isHi ? "pitch latest" : "pitch"}>
            <circle cx={x(p.px!)} cy={y(p.pz!)} r={r} fill={pitchColor(p.pitch_type)}>
              <title>
                {`${p.pitch_type_desc ?? p.pitch_type ?? "?"} ${p.start_speed?.toFixed(1) ?? ""} mph — ${p.call_desc ?? ""}`}
              </title>
            </circle>
            {numbered && (
              <text x={x(p.px!)} y={y(p.pz!)} dy="0.35em" textAnchor="middle" className="pitch-num">
                {i + 1}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
