import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from threadpoolctl import threadpool_limits

from tracker.models.features import (
    PITCH_COLUMNS,
    WP_FEATURES,
    pitch_frame,
    re24_key,
    re_key,
    wp_frame,
)
from tracker.parse import GameState

log = logging.getLogger(__name__)

WP_FILE = "win_prob.joblib"
PITCH_FILE = "pitch_type.joblib"
RE_FILE = "run_expectancy.json"
WP_COLUMNS = [
    "inning",
    "half",
    "home_score",
    "away_score",
    "outs",
    "on_1b",
    "on_2b",
    "on_3b",
    "balls",
    "strikes",
]


class Models:
    def __init__(
        self,
        wp: Any = None,
        pitch_clf: Any = None,
        re_table: Mapping[str, Any] | None = None,
    ) -> None:
        self.wp = wp
        self.pitch_clf = pitch_clf
        self.re288: Mapping[str, float] = (re_table or {}).get("re288", {})
        self.re24: Mapping[str, float] = (re_table or {}).get("re24", {})

    @classmethod
    def load(cls, artifacts_dir: Path) -> "Models":
        wp = pitch = re_table = None
        if (artifacts_dir / WP_FILE).exists():
            wp = joblib.load(artifacts_dir / WP_FILE)
        if (artifacts_dir / PITCH_FILE).exists():
            pitch = joblib.load(artifacts_dir / PITCH_FILE)
        if (artifacts_dir / RE_FILE).exists():
            re_table = json.loads((artifacts_dir / RE_FILE).read_text())
        log.info(
            "models loaded: wp=%s pitch=%s re=%s", wp is not None, pitch is not None, bool(re_table)
        )
        return cls(wp, pitch, re_table)

    def home_wp(self, rows: Sequence[Mapping[str, Any]]) -> list[float | None]:
        if self.wp is None or not rows:
            return [None] * len(rows)
        df = pd.DataFrame([{c: r[c] for c in WP_COLUMNS} for r in rows])
        with threadpool_limits(1):
            probs = self.wp.predict_proba(wp_frame(df)[WP_FEATURES])[:, 1]
        return [round(float(p), 4) for p in probs]

    def run_exp(self, row: Mapping[str, Any]) -> float | None:
        args = (row["outs"], row["on_1b"], row["on_2b"], row["on_3b"])
        value = self.re288.get(re_key(*args, row["balls"], row["strikes"]))
        if value is None:
            value = self.re24.get(re24_key(*args))
        return value

    def classify(self, rows: Sequence[Mapping[str, Any]]) -> list[str | None]:
        if self.pitch_clf is None or not rows:
            return [None] * len(rows)
        df = pd.DataFrame([{c: r.get(c) for c in PITCH_COLUMNS} for r in rows])
        x = pitch_frame(df)
        with threadpool_limits(1):
            preds = self.pitch_clf["model"].predict(x[self.pitch_clf["features"]])
        valid = x["start_speed"].notna().to_numpy()
        return [str(p) if ok else None for p, ok in zip(preds, valid, strict=True)]

    def enrich_pitches(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        for row, wp, pred in zip(rows, self.home_wp(rows), self.classify(rows), strict=True):
            row["home_wp"] = wp
            row["pitch_type_pred"] = pred
            row["run_exp"] = self.run_exp(row)

    def state_wp(self, state: GameState) -> float | None:
        if state.status == "Final":
            return 1.0 if state.home_score > state.away_score else 0.0
        if state.inning is None or state.status != "Live":
            return None
        row: dict[str, Any] = state.model_dump()
        if state.outs >= 3:
            row.update(outs=0, on_1b=False, on_2b=False, on_3b=False, balls=0, strikes=0)
            if state.half == "top":
                row["half"] = "bottom"
            else:
                row.update(half="top", inning=state.inning + 1)
        return self.home_wp([row])[0]
