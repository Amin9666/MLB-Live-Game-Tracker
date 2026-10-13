import json
import logging
from pathlib import Path
from typing import Any, cast

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, log_loss, roc_auc_score

from tracker.models.features import (
    PITCH_FEATURES,
    WP_FEATURES,
    WP_MONOTONIC,
    pitch_frame,
    re24_key,
    re_key,
    wp_frame,
)
from tracker.models.runtime import PITCH_FILE, RE_FILE, WP_FILE

log = logging.getLogger(__name__)

MIN_CLASS_SAMPLES = 1000
MIN_RE_SAMPLES = 50


def load(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect()

    def read(kind: str) -> pd.DataFrame:
        glob = str(data_dir / "*" / f"{kind}-*.parquet")
        return con.execute(f"select * from read_parquet('{glob}', union_by_name=true)").df()

    return read("games"), read("pas"), read("pitches")


def split_games(games: pd.DataFrame) -> tuple[set[int], set[int], str]:
    seasons = sorted(games["season"].unique())
    if len(seasons) > 1:
        test = games["season"] == seasons[-1]
        desc = f"train {[int(s) for s in seasons[:-1]]}, test {int(seasons[-1])}"
    else:
        cutoff = games["game_date"].quantile(0.8)
        test = games["game_date"] >= cutoff
        desc = f"train before {cutoff}, test from {cutoff}"
    return set(games.loc[~test, "game_pk"]), set(games.loc[test, "game_pk"]), desc


def run_expectancy(pas: pd.DataFrame, pitches: pd.DataFrame) -> dict[str, Any]:
    pas = pas.copy()
    pas["bat_after"] = np.where(pas["half"] == "top", pas["away_score"], pas["home_score"])
    half_cols = ["game_pk", "inning", "half"]
    half_end = pas.groupby(half_cols)["bat_after"].max().rename("half_end").reset_index()

    df = pitches.merge(half_end, on=half_cols)
    df = df[~((df["inning"] >= 9) & (df["half"] == "bottom"))]
    bat_before = np.where(df["half"] == "top", df["away_score"], df["home_score"])
    df = df.assign(runs_roi=(df["half_end"] - bat_before).clip(lower=0))

    state = ["outs", "on_1b", "on_2b", "on_3b"]
    first = df[df["pitch_number"] == 1]
    re24 = first.groupby(state)["runs_roi"].agg(["mean", "size"])
    re288 = df.groupby([*state, "balls", "strikes"])["runs_roi"].agg(["mean", "size"])
    return {
        "re24": {
            re24_key(*cast(tuple[Any, ...], k)): round(float(v["mean"]), 3)
            for k, v in re24.iterrows()
            if v["size"] >= MIN_RE_SAMPLES
        },
        "re288": {
            re_key(*cast(tuple[Any, ...], k)): round(float(v["mean"]), 3)
            for k, v in re288.iterrows()
            if v["size"] >= MIN_RE_SAMPLES
        },
        "samples": int(len(df)),
    }


def win_prob_model(
    games: pd.DataFrame, pitches: pd.DataFrame, train: set[int], test: set[int]
) -> tuple[HistGradientBoostingClassifier, dict[str, Any]]:
    home_win = (games["home_score"] > games["away_score"]).astype(int)
    label = dict(zip(games["game_pk"], home_win, strict=True))
    df = pitches[pitches["game_pk"].isin(label)]
    x = wp_frame(df)[WP_FEATURES]
    y = df["game_pk"].map(label).astype(int)
    is_train = df["game_pk"].isin(train).to_numpy()

    def fit(mask: np.ndarray) -> HistGradientBoostingClassifier:
        model = HistGradientBoostingClassifier(
            max_iter=300,
            learning_rate=0.06,
            max_leaf_nodes=31,
            min_samples_leaf=200,
            monotonic_cst=WP_MONOTONIC,
            random_state=0,
        )
        return model.fit(x[mask], y[mask])

    metrics: dict[str, Any] = {}
    if test:
        held = fit(is_train)
        p = held.predict_proba(x[~is_train])[:, 1]
        yt = y[~is_train]
        bucket = np.minimum((p * 10).astype(int), 9)
        calib = pd.DataFrame({"p": p, "y": yt.to_numpy(), "b": bucket}).groupby("b").mean()
        metrics = {
            "test_rows": int(len(yt)),
            "log_loss": round(float(log_loss(yt, p)), 4),
            "brier": round(float(brier_score_loss(yt, p)), 4),
            "auc": round(float(roc_auc_score(yt, p)), 4),
            "calibration": [
                {"pred": round(float(pred), 3), "actual": round(float(actual), 3)}
                for pred, actual in zip(calib["p"], calib["y"], strict=True)
            ],
        }
    return fit(np.ones(len(x), dtype=bool)), metrics


def pitch_type_model(
    pitches: pd.DataFrame, train: set[int], test: set[int]
) -> tuple[dict[str, Any], dict[str, Any]]:
    df = pitches[pitches["pitch_type"].notna() & pitches["start_speed"].notna()]
    counts = df["pitch_type"].value_counts()
    classes = sorted(counts[counts >= MIN_CLASS_SAMPLES].index)
    df = df[df["pitch_type"].isin(classes)]
    x = pitch_frame(df)[PITCH_FEATURES]
    y = df["pitch_type"].astype(str)
    is_train = df["game_pk"].isin(train).to_numpy()

    def fit(mask: np.ndarray) -> HistGradientBoostingClassifier:
        model = HistGradientBoostingClassifier(
            max_iter=200,
            learning_rate=0.1,
            max_leaf_nodes=63,
            early_stopping=True,
            validation_fraction=0.1,
            random_state=0,
        )
        return model.fit(x[mask], y[mask])

    metrics: dict[str, Any] = {"classes": classes}
    if test:
        held = fit(is_train)
        pred = held.predict(x[~is_train])
        yt = y[~is_train]
        per_class = pd.DataFrame({"y": yt.to_numpy(), "ok": pred == yt.to_numpy()})
        metrics.update(
            test_rows=int(len(yt)),
            accuracy=round(float(accuracy_score(yt, pred)), 4),
            macro_f1=round(float(f1_score(yt, pred, average="macro")), 4),
            per_class_accuracy={
                str(k): round(float(v), 3)
                for k, v in per_class.groupby("y")["ok"].mean().to_dict().items()
            },
        )
    return {"model": fit(np.ones(len(x), dtype=bool)), "features": PITCH_FEATURES}, metrics


def train_all(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    games, pas, pitches = load(data_dir)
    log.info("loaded %d games, %d PAs, %d pitches", len(games), len(pas), len(pitches))
    train, test, split_desc = split_games(games)
    out_dir.mkdir(parents=True, exist_ok=True)

    re = run_expectancy(pas, pitches)
    (out_dir / RE_FILE).write_text(json.dumps(re, indent=1))
    log.info("run expectancy: %d RE24 states, %d RE288 states", len(re["re24"]), len(re["re288"]))

    wp, wp_metrics = win_prob_model(games, pitches, train, test)
    joblib.dump(wp, out_dir / WP_FILE, compress=3)
    log.info("win prob: %s", {k: v for k, v in wp_metrics.items() if k != "calibration"})

    clf, clf_metrics = pitch_type_model(pitches, train, test)
    joblib.dump(clf, out_dir / PITCH_FILE, compress=3)
    log.info(
        "pitch type: acc=%s macro_f1=%s", clf_metrics.get("accuracy"), clf_metrics.get("macro_f1")
    )

    metrics = {
        "split": split_desc,
        "games": int(len(games)),
        "pitches": int(len(pitches)),
        "run_expectancy": {"samples": re["samples"], "re24": re["re24"]},
        "win_prob": wp_metrics,
        "pitch_type": clf_metrics,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics
