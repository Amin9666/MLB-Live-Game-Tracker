import { pitchColor } from "../pitchTypes";
import type { Pitch, PlateAppearance } from "../types";

interface Props {
  pitches: Pitch[];
  pas: Record<number, PlateAppearance>;
  limit?: number;
}

export function PitchLog({ pitches, pas, limit = 14 }: Props) {
  const rows = pitches.slice(-limit).reverse();
  return (
    <div className="table-wrap">
      <table className="pitch-log">
        <thead>
          <tr>
            <th>Inn</th>
            <th>Matchup</th>
            <th>Count</th>
            <th>Pitch</th>
            <th title="Model prediction">Model</th>
            <th className="num">Velo</th>
            <th className="num">Spin</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const pa = pas[p.at_bat_index];
            const agree = p.pitch_type_pred && p.pitch_type_pred === p.pitch_type;
            return (
              <tr key={`${p.at_bat_index}-${p.pitch_number}`}>
                <td>
                  {p.half === "top" ? "▲" : "▼"}
                  {p.inning}
                </td>
                <td className="matchup">
                  {pa ? `${pa.pitcher_name.split(" ").slice(-1)[0]} → ${pa.batter_name.split(" ").slice(-1)[0]}` : ""}
                </td>
                <td>
                  {p.balls}-{p.strikes}
                </td>
                <td>
                  <span className="chip" style={{ background: pitchColor(p.pitch_type) }} />
                  {p.pitch_type ?? "—"}
                </td>
                <td className={p.pitch_type_pred ? (agree ? "agree" : "disagree") : ""}>
                  {p.pitch_type_pred ?? "—"}
                </td>
                <td className="num">{p.start_speed?.toFixed(1) ?? "—"}</td>
                <td className="num">{p.spin_rate ?? "—"}</td>
                <td className="result">{p.call_desc ?? ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
