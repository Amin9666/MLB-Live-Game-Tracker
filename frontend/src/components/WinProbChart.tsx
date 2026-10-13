import { scaleLinear } from "d3-scale";
import { area, curveStepAfter, line } from "d3-shape";
import { useMemo, useState } from "react";
import type { Pitch } from "../types";

const W = 720;
const H = 220;
const M = { top: 16, right: 44, bottom: 22, left: 44 };

interface Point {
  i: number;
  wp: number;
  label: string;
}

interface Props {
  pitches: Pitch[];
  currentWp: number | null;
  homeTeam: string;
  awayTeam: string;
}

export function WinProbChart({ pitches, currentWp, homeTeam, awayTeam }: Props) {
  const [hover, setHover] = useState<Point | null>(null);
  const { points, innings } = useMemo(() => {
    const pts: Point[] = [];
    const marks: { i: number; label: string }[] = [];
    let lastHalf = "";
    pitches.forEach((p) => {
      if (p.home_wp === null) return;
      const half = `${p.inning}-${p.half}`;
      if (half !== lastHalf && p.half === "top") marks.push({ i: pts.length, label: String(p.inning) });
      lastHalf = half;
      pts.push({
        i: pts.length,
        wp: p.home_wp,
        label: `${p.half === "top" ? "▲" : "▼"}${p.inning} · ${p.balls}-${p.strikes}, ${p.outs} out`,
      });
    });
    if (currentWp !== null) pts.push({ i: pts.length, wp: currentWp, label: "now" });
    return { points: pts, innings: marks };
  }, [pitches, currentWp]);

  const x = scaleLinear()
    .domain([0, Math.max(points.length - 1, 1)])
    .range([M.left, W - M.right]);
  const y = scaleLinear().domain([0, 1]).range([H - M.bottom, M.top]);
  const path = line<Point>()
    .x((d) => x(d.i))
    .y((d) => y(d.wp))
    .curve(curveStepAfter)(points);
  const fill = area<Point>()
    .x((d) => x(d.i))
    .y0(y(0.5))
    .y1((d) => y(d.wp))
    .curve(curveStepAfter)(points);
  const last = points[points.length - 1];

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(x.invert(px));
    setHover(points[Math.max(0, Math.min(points.length - 1, i))] ?? null);
  };

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="chart winprob"
      role="img"
      aria-label={`Win probability, ${homeTeam} vs ${awayTeam}`}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      <clipPath id="above">
        <rect x={0} y={0} width={W} height={y(0.5)} />
      </clipPath>
      <clipPath id="below">
        <rect x={0} y={y(0.5)} width={W} height={H} />
      </clipPath>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line className={t === 0.5 ? "axis-mid" : "grid"} x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
          <text className="axis-label" x={M.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
            {t === 0.5 ? "50%" : t > 0.5 ? `${t * 100}%` : `${(1 - t) * 100}%`}
          </text>
        </g>
      ))}
      <text className="team-label home" x={W - M.right + 6} y={M.top + 4}>
        {homeTeam}
      </text>
      <text className="team-label away" x={W - M.right + 6} y={H - M.bottom}>
        {awayTeam}
      </text>
      {innings.map((m) => (
        <g key={m.label}>
          <line className="grid" x1={x(m.i)} x2={x(m.i)} y1={M.top} y2={H - M.bottom} />
          <text className="axis-label" x={x(m.i)} y={H - 6} textAnchor="middle">
            {m.label}
          </text>
        </g>
      ))}
      {fill && <path className="wp-fill home" d={fill} clipPath="url(#above)" />}
      {fill && <path className="wp-fill away" d={fill} clipPath="url(#below)" />}
      {path && <path className="wp-line" d={path} />}
      {last && <circle className="wp-now" cx={x(last.i)} cy={y(last.wp)} r={4} />}
      {hover && (
        <g className="wp-hover">
          <line x1={x(hover.i)} x2={x(hover.i)} y1={M.top} y2={H - M.bottom} />
          <circle cx={x(hover.i)} cy={y(hover.wp)} r={4} />
          <text
            x={Math.min(x(hover.i) + 8, W - M.right - 150)}
            y={M.top + 12}
          >{`${hover.label} · ${homeTeam} ${(hover.wp * 100).toFixed(1)}%`}</text>
        </g>
      )}
    </svg>
  );
}
