import { scaleLinear } from "d3-scale";
import { arc } from "d3-shape";
import type { Pitch, PlateAppearance } from "../types";

const W = 320;
const H = 300;
const HOME_X = 125.42;
const HOME_Y = 198.27;
const FT_PER_UNIT = 2.33;
const HITS = new Set(["single", "double", "triple", "home_run"]);

interface Props {
  pitches: Pitch[];
  pas: Record<number, PlateAppearance>;
  awayTeam: string;
  homeTeam: string;
}

export function SprayChart({ pitches, pas, awayTeam, homeTeam }: Props) {
  const scale = scaleLinear().domain([0, 420]).range([0, H - 24]);
  const cx = W / 2;
  const cy = H - 12;
  const toXY = (ft: number, deg: number): [number, number] => {
    const rad = (deg * Math.PI) / 180;
    return [cx + scale(ft) * Math.sin(rad), cy - scale(ft) * Math.cos(rad)];
  };
  const outfield = arc()({
    innerRadius: 0,
    outerRadius: scale(400),
    startAngle: -Math.PI / 4,
    endAngle: Math.PI / 4,
  });
  const infield = arc()({
    innerRadius: 0,
    outerRadius: scale(155),
    startAngle: -Math.PI / 4,
    endAngle: Math.PI / 4,
  });
  const diamond = [0, -45, 0, 45].map((deg, i) => toXY(i === 0 ? 0 : i === 2 ? 127.3 : 90, deg));

  const balls = pitches
    .filter((p) => p.is_in_play && p.hit_x !== null && p.hit_y !== null)
    .map((p) => {
      const dx = (p.hit_x! - HOME_X) * FT_PER_UNIT;
      const dy = (HOME_Y - p.hit_y!) * FT_PER_UNIT;
      const pa = pas[p.at_bat_index];
      return {
        key: `${p.at_bat_index}-${p.pitch_number}`,
        x: cx + scale(dx),
        y: cy - scale(dy),
        hit: pa ? HITS.has(pa.event_type ?? "") : false,
        team: p.half === "top" ? awayTeam : homeTeam,
        label: `${pa?.batter_name ?? ""}: ${pa?.event ?? "in play"}${p.launch_speed ? ` · ${p.launch_speed.toFixed(1)} mph EV` : ""}${p.total_distance ? ` · ${p.total_distance} ft` : ""}`,
        side: p.half,
      };
    });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart spray" role="img" aria-label="Spray chart">
      <path className="field-grass" d={outfield ?? ""} transform={`translate(${cx},${cy})`} />
      <path className="field-dirt" d={infield ?? ""} transform={`translate(${cx},${cy})`} />
      <polygon className="field-diamond" points={diamond.map((p) => p.join(",")).join(" ")} />
      {[-45, 45].map((deg) => {
        const [x2, y2] = toXY(330, deg);
        return <line key={deg} className="foul-line" x1={cx} y1={cy} x2={x2} y2={y2} />;
      })}
      {balls.map((b) => (
        <circle
          key={b.key}
          cx={b.x}
          cy={b.y}
          r={b.hit ? 5 : 4}
          className={`bip ${b.side} ${b.hit ? "hit" : "out"}`}
        >
          <title>{`${b.team} · ${b.label}`}</title>
        </circle>
      ))}
    </svg>
  );
}
